"""Stateless emissions estimation for per-step recording.

The estimator does the math, the step records the result, summaries are
computed on demand from the DB. No in-memory state, no accumulation.

Dispatches to the right estimation tier based on server-level config:
  Layer 1: EcoLogits (cloud API calls via supported providers)
  Layer 2: Model-size-class estimator (unsupported cloud providers)
  Layer 3: Local inference with four-tier accuracy chain:
    Tier 1: GPU metrics sidecar (remote hardware measurement)
    Tier 2: CodeCarbon (same-machine hardware measurement)
    Tier 3: User-provided hardware config (gpu_power_watts + tps)
    Tier 4: Model-size-class fallback
"""

from typing import Optional

from letta.emissions.estimator import EmissionsRecord, estimate_emissions
from letta.emissions.grid_intensity import resolve_grid_intensity
from letta.log import get_logger
from letta.settings import emissions_settings

logger = get_logger(__name__)


def estimate_step_emissions(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    ecologits_energy_kwh: Optional[float] = None,
    ecologits_gwp_kgco2eq: Optional[float] = None,
    codecarbon_energy_kwh: Optional[float] = None,
    codecarbon_emissions_kgco2eq: Optional[float] = None,
    start_power_watts: Optional[float] = None,
    end_power_watts: Optional[float] = None,
    request_latency_s: Optional[float] = None,
) -> Optional[EmissionsRecord]:
    """Estimate emissions for a single LLM request. Stateless.

    Reads deployment-level config from emissions_settings (grid zone, GPU
    power, sidecar URL, etc.). The step_manager writes the result to the DB.
    Summaries are computed on demand from step records.

    Args:
        model_name: Model identifier (e.g. "gpt-4", "llama3.1:8b").
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        ecologits_energy_kwh: EcoLogits-provided energy (if available).
        ecologits_gwp_kgco2eq: EcoLogits-provided GWP (if available).
        codecarbon_energy_kwh: CodeCarbon-provided energy (if available).
        codecarbon_emissions_kgco2eq: CodeCarbon-provided GWP (if available).
        start_power_watts: GPU power at request start (from sidecar).
        end_power_watts: GPU power at request end (from sidecar).
        request_latency_s: Wall-clock request duration (for sidecar/CodeCarbon).

    Returns:
        EmissionsRecord, or None if tracking is disabled.
    """
    if not emissions_settings.track_emissions:
        return None

    if not prompt_tokens and not completion_tokens:
        return None

    # Resolve grid intensity from deployment-level config
    resolved_intensity, resolved_zone = resolve_grid_intensity(
        zone=emissions_settings.electricity_mix_zone,
        override_gco2e_per_kwh=emissions_settings.grid_intensity_gco2e_per_kwh,
    )

    # Collect optional hardware telemetry from sidecar if configured
    gpu_power_watts = emissions_settings.gpu_power_watts
    model_tokens_per_second = emissions_settings.model_tokens_per_second

    # If GPU metrics sidecar is configured, try to get power readings
    if emissions_settings.gpu_metrics_url and start_power_watts is None:
        start_power_watts, end_power_watts, request_latency_s = _probe_sidecar(
            emissions_settings.gpu_metrics_url, request_latency_s
        )

    # If CodeCarbon hardware monitor is enabled, try to get readings
    if emissions_settings.enable_hardware_monitor and codecarbon_energy_kwh is None:
        codecarbon_energy_kwh, codecarbon_emissions_kgco2eq = _probe_codecarbon(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_latency_s=request_latency_s,
        )

    # Dispatch to the right estimation method
    record = estimate_emissions(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        grid_intensity_gco2e_per_kwh=resolved_intensity,
        electricity_mix_zone=resolved_zone or emissions_settings.electricity_mix_zone,
        model_name=model_name,
        gpu_power_watts=gpu_power_watts,
        model_tokens_per_second=model_tokens_per_second,
        start_power_watts=start_power_watts,
        end_power_watts=end_power_watts,
        duration_s=request_latency_s,
        ecologits_energy_kwh=ecologits_energy_kwh,
        ecologits_gwp_kgco2eq=ecologits_gwp_kgco2eq,
        codecarbon_energy_kwh=codecarbon_energy_kwh,
        codecarbon_emissions_kgco2eq=codecarbon_emissions_kgco2eq,
    )

    return record


def _probe_sidecar(
    gpu_metrics_url: str,
    request_latency_s: Optional[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Try to get GPU power readings from the sidecar. Lazy import."""
    try:
        from letta.emissions.remote_gpu_bridge import RemoteGPUMonitor

        monitor = RemoteGPUMonitor(gpu_metrics_url)
        power_data = monitor.get_power_reading()
        if power_data:
            return (
                power_data.get("power_watts"),
                power_data.get("power_watts"),  # same reading for start/end
                request_latency_s,
            )
    except Exception as e:
        logger.debug(f"GPU sidecar probe failed: {e}")
    return None, None, request_latency_s


def _probe_codecarbon(
    prompt_tokens: int,
    completion_tokens: int,
    request_latency_s: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Try to get CodeCarbon readings. Lazy import."""
    try:
        from letta.emissions.codecarbon_bridge import LocalInferenceTracker

        tracker = LocalInferenceTracker()
        tracker.start_task("emissions_step")
        # CodeCarbon needs to run alongside the request; if we reach here
        # after the fact, we can only provide an estimate based on duration
        energy_kwh = tracker.stop_task("emissions_step")
        if energy_kwh is not None:
            return energy_kwh, None  # CodeCarbon gives energy, not GWP directly
    except Exception as e:
        logger.debug(f"CodeCarbon probe failed: {e}")
    return None, None
