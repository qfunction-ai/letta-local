"""Tests for letta.emissions.estimator — core estimation math."""

import pytest

from letta.emissions.estimator import (
    EmissionsRecord,
    ModelSizeClass,
    classify_model_size,
    estimate_emissions,
    estimate_from_sidecar,
    estimate_from_size_class,
    estimate_from_user_config,
    JOULES_PER_KWH,
)


class TestClassifyModelSize:
    def test_openai_reasoning_models(self):
        assert classify_model_size("o1-preview") == ModelSizeClass.REASONING
        assert classify_model_size("o3-mini") == ModelSizeClass.REASONING
        assert classify_model_size("o4-mini") == ModelSizeClass.REASONING

    def test_deepseek_reasoning(self):
        assert classify_model_size("deepseek-r1") == ModelSizeClass.REASONING
        assert classify_model_size("deepseek-reasoner") == ModelSizeClass.REASONING

    def test_small_models(self):
        assert classify_model_size("llama-3.2-1b") == ModelSizeClass.SMALL
        assert classify_model_size("phi-3-mini") == ModelSizeClass.SMALL
        assert classify_model_size("qwen2.5-0.5b") == ModelSizeClass.SMALL
        assert classify_model_size("gemma-2-2b") == ModelSizeClass.SMALL

    def test_large_models(self):
        assert classify_model_size("gpt-4") == ModelSizeClass.LARGE
        assert classify_model_size("gpt-5") == ModelSizeClass.LARGE
        assert classify_model_size("claude-opus-4") == ModelSizeClass.LARGE
        assert classify_model_size("llama-3.1-405b") == ModelSizeClass.LARGE

    def test_medium_models(self):
        assert classify_model_size("llama-3-8b") == ModelSizeClass.MEDIUM
        assert classify_model_size("mistral-7b") == ModelSizeClass.MEDIUM
        assert classify_model_size("qwen2.5-7b") == ModelSizeClass.MEDIUM

    def test_unknown_defaults_to_unknown(self):
        assert classify_model_size("some-new-model") == ModelSizeClass.UNKNOWN


class TestEstimateFromSizeClass:
    def test_basic_estimation(self):
        record = estimate_from_size_class(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            electricity_mix_zone="US-VA",
        )
        assert record.estimation_method == "size_class"
        assert record.model_size_class == "large"
        assert record.electricity_mix_zone == "US-VA"
        assert record.prompt_tokens == 1000
        assert record.completion_tokens == 500
        assert record.energy_kwh > 0
        assert record.emissions_gco2e > 0
        assert record.grid_intensity_gco2e_per_kwh == 270

    def test_sweden_low_emissions(self):
        record = estimate_from_size_class(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=37,
            electricity_mix_zone="SE",
        )
        # Same energy, but Sweden grid => much lower emissions
        assert record.energy_kwh > 0
        assert record.emissions_gco2e > 0
        # Sweden emissions should be ~7x lower than Virginia
        va_record = estimate_from_size_class(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
        )
        assert record.emissions_gco2e < va_record.emissions_gco2e / 5

    def test_manual_math_medium_model(self):
        """Verify the math from the plan: 1000 input + 500 output on medium model."""
        record = estimate_from_size_class(
            model_name="llama-3-8b",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=37,
            electricity_mix_zone="SE",
        )
        # Medium: input=0.1J, output=0.25J
        # energy = (1000*0.1 + 500*0.25) J = 225 J = 6.25e-5 kWh
        expected_energy_j = 1000 * 0.1 + 500 * 0.25  # 225 J
        expected_energy_kwh = expected_energy_j / JOULES_PER_KWH
        assert abs(record.energy_kwh - expected_energy_kwh) < 1e-10
        expected_emissions = expected_energy_kwh * 37
        assert abs(record.emissions_gco2e - expected_emissions) < 1e-6


class TestEstimateFromUserConfig:
    def test_basic_user_config(self):
        record = estimate_from_user_config(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            gpu_power_watts=300,
            model_tokens_per_second=2000,
        )
        assert record.estimation_method == "user_config"
        assert record.gpu_power_watts == 300
        # Duration = 1500 tokens / 2000 tps = 0.75s
        assert abs(record.request_latency_s - 0.75) < 0.01
        # Energy = 300W * 0.75s = 225 J = 6.25e-5 kWh
        expected_energy_kwh = 225 / JOULES_PER_KWH
        assert abs(record.energy_kwh - expected_energy_kwh) < 1e-10

    def test_invalid_tps_raises(self):
        with pytest.raises(ValueError):
            estimate_from_user_config(
                prompt_tokens=1000,
                completion_tokens=500,
                grid_intensity_gco2e_per_kwh=270,
                gpu_power_watts=300,
                model_tokens_per_second=0,
            )


class TestEstimateFromSidecar:
    def test_basic_sidecar(self):
        record = estimate_from_sidecar(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            start_power_watts=300,
            end_power_watts=350,
            duration_s=0.75,
        )
        assert record.estimation_method == "sidecar"
        # Avg power = 325W, energy = 325 * 0.75 = 243.75 J
        expected_energy_kwh = 243.75 / JOULES_PER_KWH
        assert abs(record.energy_kwh - expected_energy_kwh) < 1e-10
        assert record.gpu_power_watts == 325.0


class TestEstimateEmissions:
    def test_ecologits_priority(self):
        """EcoLogits data takes priority over all other methods."""
        record = estimate_emissions(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            ecologits_energy_kwh=1.5e-5,
            ecologits_gwp_kgco2eq=4.05e-6,
        )
        assert record.estimation_method == "ecologits"
        assert abs(record.energy_kwh - 1.5e-5) < 1e-12
        assert abs(record.emissions_gco2e - 4.05e-3) < 1e-8  # 4.05e-6 kg * 1000 = 4.05e-3 g

    def test_codecarbon_priority_over_sidecar(self):
        """CodeCarbon data takes priority over sidecar/user config."""
        record = estimate_emissions(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            codecarbon_energy_kwh=2.0e-5,
            codecarbon_emissions_kgco2eq=5.4e-6,
            start_power_watts=300,  # Should be ignored
            end_power_watts=350,
            duration_s=0.75,
        )
        assert record.estimation_method == "codecarbon"

    def test_sidecar_priority_over_user_config(self):
        """Sidecar data takes priority over user config."""
        record = estimate_emissions(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            start_power_watts=300,
            end_power_watts=350,
            duration_s=0.75,
            gpu_power_watts=300,  # Should be ignored
            model_tokens_per_second=2000,
        )
        assert record.estimation_method == "sidecar"

    def test_user_config_priority_over_size_class(self):
        """User config takes priority over size-class fallback."""
        record = estimate_emissions(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            gpu_power_watts=300,
            model_tokens_per_second=2000,
        )
        assert record.estimation_method == "user_config"

    def test_size_class_fallback(self):
        """Size-class fallback when nothing else available."""
        record = estimate_emissions(
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            model_name="gpt-4",
        )
        assert record.estimation_method == "size_class"

    def test_zero_tokens(self):
        record = estimate_emissions(
            prompt_tokens=0,
            completion_tokens=0,
            grid_intensity_gco2e_per_kwh=270,
            model_name="gpt-4",
        )
        assert record.energy_kwh == 0
        assert record.emissions_gco2e == 0
