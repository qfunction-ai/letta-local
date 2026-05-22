"""Tests for letta.emissions.tracker — stateless estimate_step_emissions."""

import pytest

from letta.emissions.tracker import estimate_step_emissions
from letta.settings import emissions_settings


class TestEstimateStepEmissions:
    def test_returns_none_when_disabled(self, monkeypatch):
        """When track_emissions is False, returns None."""
        monkeypatch.setattr(emissions_settings, "track_emissions", False)
        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result is None

    def test_returns_none_when_zero_tokens(self, monkeypatch):
        """When both token counts are zero, returns None."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert result is None

    def test_size_class_fallback(self, monkeypatch):
        """When no hardware config is set, falls back to size-class estimator."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        monkeypatch.setattr(emissions_settings, "electricity_mix_zone", None)
        monkeypatch.setattr(emissions_settings, "grid_intensity_gco2e_per_kwh", None)
        monkeypatch.setattr(emissions_settings, "gpu_power_watts", None)
        monkeypatch.setattr(emissions_settings, "model_tokens_per_second", None)
        monkeypatch.setattr(emissions_settings, "gpu_metrics_url", None)
        monkeypatch.setattr(emissions_settings, "enable_hardware_monitor", False)

        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result is not None
        assert result.estimation_method == "size_class"
        assert result.prompt_tokens == 1000
        assert result.completion_tokens == 500

    def test_user_config_from_settings(self, monkeypatch):
        """When gpu_power_watts is set in settings, uses user-config method."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        monkeypatch.setattr(emissions_settings, "electricity_mix_zone", "US-VA")
        monkeypatch.setattr(emissions_settings, "grid_intensity_gco2e_per_kwh", None)
        monkeypatch.setattr(emissions_settings, "gpu_power_watts", 300.0)
        monkeypatch.setattr(emissions_settings, "model_tokens_per_second", 2000.0)
        monkeypatch.setattr(emissions_settings, "gpu_metrics_url", None)
        monkeypatch.setattr(emissions_settings, "enable_hardware_monitor", False)

        result = estimate_step_emissions(
            model_name="llama3.1:8b",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result is not None
        assert result.estimation_method == "user_config"
        assert result.gpu_power_watts == 300.0

    def test_zone_from_settings(self, monkeypatch):
        """When electricity_mix_zone is set in settings, uses it for grid intensity."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        monkeypatch.setattr(emissions_settings, "electricity_mix_zone", "SE")
        monkeypatch.setattr(emissions_settings, "grid_intensity_gco2e_per_kwh", None)
        monkeypatch.setattr(emissions_settings, "gpu_power_watts", None)
        monkeypatch.setattr(emissions_settings, "model_tokens_per_second", None)
        monkeypatch.setattr(emissions_settings, "gpu_metrics_url", None)
        monkeypatch.setattr(emissions_settings, "enable_hardware_monitor", False)

        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result is not None
        # Sweden grid intensity is 37 gCO2e/kWh
        assert result.grid_intensity_gco2e_per_kwh == 37.0

    def test_grid_intensity_override(self, monkeypatch):
        """When grid_intensity_gco2e_per_kwh is set, it overrides zone."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        monkeypatch.setattr(emissions_settings, "electricity_mix_zone", "SE")
        monkeypatch.setattr(emissions_settings, "grid_intensity_gco2e_per_kwh", 100.0)
        monkeypatch.setattr(emissions_settings, "gpu_power_watts", None)
        monkeypatch.setattr(emissions_settings, "model_tokens_per_second", None)
        monkeypatch.setattr(emissions_settings, "gpu_metrics_url", None)
        monkeypatch.setattr(emissions_settings, "enable_hardware_monitor", False)

        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result is not None
        assert result.grid_intensity_gco2e_per_kwh == 100.0

    def test_ecologits_data_passes_through(self, monkeypatch):
        """EcoLogits data is passed through when provided."""
        monkeypatch.setattr(emissions_settings, "track_emissions", True)
        monkeypatch.setattr(emissions_settings, "electricity_mix_zone", None)
        monkeypatch.setattr(emissions_settings, "grid_intensity_gco2e_per_kwh", None)
        monkeypatch.setattr(emissions_settings, "gpu_power_watts", None)
        monkeypatch.setattr(emissions_settings, "model_tokens_per_second", None)

        result = estimate_step_emissions(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            ecologits_energy_kwh=1.5e-5,
            ecologits_gwp_kgco2eq=4.05e-6,
        )
        assert result is not None
        assert result.estimation_method == "ecologits"
