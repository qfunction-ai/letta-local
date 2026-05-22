"""Core emissions estimation math.

Pure functions. No I/O. No side effects. All estimation happens here so it's
testable without any hardware, network, or database access.

The math is straightforward:
    energy_kwh = (prompt_tokens * input_energy + completion_tokens * output_energy) / 3.6e6
    emissions_gco2e = energy_kwh * grid_intensity_gco2e_per_kwh

Energy per token is in Joules. 1 kWh = 3.6e6 J.

Conservative defaults deliberately overestimate by ~30% to avoid under-reporting.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ModelSizeClass(str, Enum):
    """Model size categories for energy-per-token estimation."""

    SMALL = "small"       # <7B parameters
    MEDIUM = "medium"     # 7-70B parameters
    LARGE = "large"       # >70B parameters
    REASONING = "reasoning"  # o1/o3, extended thinking models
    UNKNOWN = "unknown"   # Can't determine size


# Energy per token in Joules, by model size class.
# Conservative (overestimate ~30%) to avoid under-reporting.
# Based on published GPU benchmarks (NVIDIA A100/H100, L40S) and typical
# inference throughput.
ENERGY_PER_TOKEN_J: dict[ModelSizeClass, dict[str, float]] = {
    #                   input    output
    ModelSizeClass.SMALL:     {"input": 0.02, "output": 0.05},   # A100, ~10K tok/s
    ModelSizeClass.MEDIUM:    {"input": 0.1,  "output": 0.25},   # A100, ~2K tok/s
    ModelSizeClass.LARGE:     {"input": 0.5,  "output": 1.0},    # 8xA100, ~500 tok/s
    ModelSizeClass.REASONING: {"input": 0.5,  "output": 1.0},   # Extended thinking, multi-pass
    ModelSizeClass.UNKNOWN:   {"input": 0.1,  "output": 0.25},   # Default to medium
}

# Conversion: 1 J = 2.78e-7 kWh  (1 kWh = 3.6e6 J)
JOULES_PER_KWH = 3.6e6


class EmissionsRecord(BaseModel):
    """Per-request emissions estimate. Attached to Step."""

    energy_kwh: float = Field(
        ...,
        description="Estimated energy consumption in kWh.",
    )
    grid_intensity_gco2e_per_kwh: float = Field(
        ...,
        description="Grid carbon intensity used for calculation, in gCO2e/kWh.",
    )
    emissions_gco2e: float = Field(
        ...,
        description="Estimated emissions in gCO2e. energy_kwh * grid_intensity.",
    )
    electricity_mix_zone: Optional[str] = Field(
        None,
        description="Zone used for grid intensity lookup.",
    )
    estimation_method: str = Field(
        ...,
        description="How emissions were estimated: 'ecologits', 'codecarbon', "
        "'sidecar', 'user_config', 'size_class'.",
    )
    model_size_class: str = Field(
        ...,
        description="Model size class used: 'small', 'medium', 'large', 'reasoning', 'unknown'.",
    )
    prompt_tokens: int = Field(
        ...,
        description="Input token count.",
    )
    completion_tokens: int = Field(
        ...,
        description="Output token count.",
    )
    gpu_power_watts: Optional[float] = Field(
        None,
        description="GPU power in watts, if user-specified or measured.",
    )
    request_latency_s: Optional[float] = Field(
        None,
        description="Request duration in seconds, if measured.",
    )


def classify_model_size(model_name: str) -> ModelSizeClass:
    """Classify a model by parameter count heuristics.

    Uses the model name to guess the size class. This is imperfect —
    some models don't encode size in their name — but it's a reasonable
    default when no other information is available.
    """
    name = model_name.lower()

    # Reasoning models
    if any(prefix in name for prefix in ("o1-", "o1_", "o3-", "o3_", "o4-", "o4_")):
        return ModelSizeClass.REASONING
    if "deepseek-r1" in name or "deepseek-reasoner" in name:
        return ModelSizeClass.REASONING

    # Small models (<7B)
    # Note: Use word boundaries to avoid matching "405b" as "5b" etc.
    # We check for specific small model patterns.
    small_indicators = [
        "-0.5b", "-1b", "-2b", "-3b", "-4b", "-5b", "-6b",
        "-0.5b", "-1.5b", "-2.7b", "-3.8b",
        "phi-2", "phi-3", "tiny", "mini",
        "qwen2.5-0.5", "qwen2.5-1.5", "qwen2.5-3",
        "gemma-2b", "gemma-2-2b",
        "llama-3.2-1b", "llama-3.2-3b",
        "bitnet",
    ]
    if any(ind in name for ind in small_indicators):
        return ModelSizeClass.SMALL

    # Large models (>70B)
    large_indicators = [
        "-70b", "-72b", "-104b", "-123b", "-175b", "-405b", "-671b",
        "gpt-4", "gpt-5",  # Cloud models, large by assumption
        "claude-opus", "claude-sonnet-4",
        "gemini-2.5-pro", "gemini-1.5-pro",
    ]
    if any(ind in name for ind in large_indicators):
        return ModelSizeClass.LARGE

    # Medium models (7-70B) — default for most
    medium_indicators = [
        "-7b", "-8b", "-9b", "-11b", "-13b", "-14b", "-15b",
        "-22b", "-27b", "-32b", "-34b", "-35b", "-47b", "-70b",
        "llama-3", "mistral-7b", "mixtral", "qwen2.5-7b",
        "qwen2.5-14b", "qwen2.5-32b", "gemma-27b",
        "codestral", "deepseek-v2", "deepseek-v3",
    ]
    if any(ind in name for ind in medium_indicators):
        return ModelSizeClass.MEDIUM

    # Can't determine — default to medium (same as UNKNOWN in the table)
    return ModelSizeClass.UNKNOWN


def estimate_from_size_class(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    grid_intensity_gco2e_per_kwh: float,
    electricity_mix_zone: Optional[str] = None,
) -> EmissionsRecord:
    """Estimate emissions using model-size-class energy-per-token defaults.

    This is the fallback method (Tier 4 / Layer 2). It's deliberately
    conservative — overestimates by ~30% to avoid under-reporting.
    """
    size_class = classify_model_size(model_name)
    energy_table = ENERGY_PER_TOKEN_J[size_class]

    input_energy_j = prompt_tokens * energy_table["input"]
    output_energy_j = completion_tokens * energy_table["output"]
    total_energy_j = input_energy_j + output_energy_j
    energy_kwh = total_energy_j / JOULES_PER_KWH

    emissions_gco2e = energy_kwh * grid_intensity_gco2e_per_kwh

    return EmissionsRecord(
        energy_kwh=energy_kwh,
        grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
        emissions_gco2e=emissions_gco2e,
        electricity_mix_zone=electricity_mix_zone,
        estimation_method="size_class",
        model_size_class=size_class.value,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def estimate_from_user_config(
    prompt_tokens: int,
    completion_tokens: int,
    grid_intensity_gco2e_per_kwh: float,
    gpu_power_watts: float,
    model_tokens_per_second: float,
    electricity_mix_zone: Optional[str] = None,
    model_name: Optional[str] = None,
) -> EmissionsRecord:
    """Estimate emissions using user-provided hardware config.

    This is Tier 3. The user tells us their GPU TDP and measured throughput.
    We compute: energy = (total_tokens / tps) * gpu_power_w.

    More accurate than size-class defaults, less accurate than real measurement.
    """
    total_tokens = prompt_tokens + completion_tokens
    if model_tokens_per_second <= 0:
        raise ValueError(f"model_tokens_per_second must be positive, got {model_tokens_per_second}")

    # Duration in seconds
    duration_s = total_tokens / model_tokens_per_second

    # Energy: power (W) * time (s) = energy (J), then convert to kWh
    energy_j = gpu_power_watts * duration_s
    energy_kwh = energy_j / JOULES_PER_KWH

    emissions_gco2e = energy_kwh * grid_intensity_gco2e_per_kwh

    size_class = classify_model_size(model_name) if model_name else ModelSizeClass.UNKNOWN

    return EmissionsRecord(
        energy_kwh=energy_kwh,
        grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
        emissions_gco2e=emissions_gco2e,
        electricity_mix_zone=electricity_mix_zone,
        estimation_method="user_config",
        model_size_class=size_class.value,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        gpu_power_watts=gpu_power_watts,
        request_latency_s=duration_s,
    )


def estimate_from_sidecar(
    prompt_tokens: int,
    completion_tokens: int,
    grid_intensity_gco2e_per_kwh: float,
    start_power_watts: float,
    end_power_watts: float,
    duration_s: float,
    electricity_mix_zone: Optional[str] = None,
    model_name: Optional[str] = None,
) -> EmissionsRecord:
    """Estimate emissions from GPU metrics sidecar readings.

    This is Tier 1. We have actual power readings from the inference host.
    We average the start/end power readings and multiply by duration.
    """
    avg_power_watts = (start_power_watts + end_power_watts) / 2
    energy_j = avg_power_watts * duration_s
    energy_kwh = energy_j / JOULES_PER_KWH

    emissions_gco2e = energy_kwh * grid_intensity_gco2e_per_kwh

    size_class = classify_model_size(model_name) if model_name else ModelSizeClass.UNKNOWN

    return EmissionsRecord(
        energy_kwh=energy_kwh,
        grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
        emissions_gco2e=emissions_gco2e,
        electricity_mix_zone=electricity_mix_zone,
        estimation_method="sidecar",
        model_size_class=size_class.value,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        gpu_power_watts=avg_power_watts,
        request_latency_s=duration_s,
    )


# Convenience: the main entry point that all estimation flows through.
def estimate_emissions(
    prompt_tokens: int,
    completion_tokens: int,
    grid_intensity_gco2e_per_kwh: float,
    electricity_mix_zone: Optional[str] = None,
    model_name: Optional[str] = None,
    gpu_power_watts: Optional[float] = None,
    model_tokens_per_second: Optional[float] = None,
    start_power_watts: Optional[float] = None,
    end_power_watts: Optional[float] = None,
    duration_s: Optional[float] = None,
    ecologits_energy_kwh: Optional[float] = None,
    ecologits_gwp_kgco2eq: Optional[float] = None,
    codecarbon_energy_kwh: Optional[float] = None,
    codecarbon_emissions_kgco2eq: Optional[float] = None,
) -> EmissionsRecord:
    """Estimate emissions for a single LLM request.

    Dispatches to the right estimation method based on available data:
    1. EcoLogits data (ecologits_energy_kwh / ecologits_gwp_kgco2eq)
    2. CodeCarbon data (codecarbon_energy_kwh / codecarbon_emissions_kgco2eq)
    3. Sidecar data (start_power_watts + end_power_watts + duration_s)
    4. User config (gpu_power_watts + model_tokens_per_second)
    5. Model-size-class fallback
    """
    # Layer 1: EcoLogits
    if ecologits_energy_kwh is not None and ecologits_gwp_kgco2eq is not None:
        size_class = classify_model_size(model_name) if model_name else ModelSizeClass.UNKNOWN
        return EmissionsRecord(
            energy_kwh=ecologits_energy_kwh,
            grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
            emissions_gco2e=ecologits_gwp_kgco2eq * 1000,  # kg -> g
            electricity_mix_zone=electricity_mix_zone,
            estimation_method="ecologits",
            model_size_class=size_class.value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # Layer 3 Tier 2: CodeCarbon
    if codecarbon_energy_kwh is not None and codecarbon_emissions_kgco2eq is not None:
        size_class = classify_model_size(model_name) if model_name else ModelSizeClass.UNKNOWN
        return EmissionsRecord(
            energy_kwh=codecarbon_energy_kwh,
            grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
            emissions_gco2e=codecarbon_emissions_kgco2eq * 1000,  # kg -> g
            electricity_mix_zone=electricity_mix_zone,
            estimation_method="codecarbon",
            model_size_class=size_class.value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # Layer 3 Tier 1: Sidecar
    if (
        start_power_watts is not None
        and end_power_watts is not None
        and duration_s is not None
    ):
        return estimate_from_sidecar(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
            start_power_watts=start_power_watts,
            end_power_watts=end_power_watts,
            duration_s=duration_s,
            electricity_mix_zone=electricity_mix_zone,
            model_name=model_name,
        )

    # Layer 3 Tier 3: User config
    if gpu_power_watts is not None and model_tokens_per_second is not None:
        return estimate_from_user_config(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
            gpu_power_watts=gpu_power_watts,
            model_tokens_per_second=model_tokens_per_second,
            electricity_mix_zone=electricity_mix_zone,
            model_name=model_name,
        )

    # Layer 2 / Layer 3 Tier 4: Size-class fallback
    return estimate_from_size_class(
        model_name=model_name or "unknown",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        grid_intensity_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
        electricity_mix_zone=electricity_mix_zone,
    )
