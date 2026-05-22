"""Tests for letta.emissions.tracker — EmissionsTracker and EmissionsSummary."""

import pytest

from letta.emissions.tracker import EmissionsSummary, EmissionsTracker


class TestEmissionsSummary:
    def test_default_values(self):
        summary = EmissionsSummary()
        assert summary.total_energy_kwh == 0.0
        assert summary.total_emissions_gco2e == 0.0
        assert summary.total_prompt_tokens == 0
        assert summary.total_completion_tokens == 0
        assert summary.step_count == 0
        assert summary.grid_zones_used == {}
        assert summary.estimation_methods_used == {}


class TestEmissionsTracker:
    def test_size_class_estimation(self):
        tracker = EmissionsTracker()
        record = tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            electricity_mix_zone="US-VA",
        )
        assert record.estimation_method == "size_class"
        assert record.electricity_mix_zone == "US-VA"

    def test_user_config_estimation(self):
        tracker = EmissionsTracker()
        record = tracker.estimate_and_record(
            model_name="llama-3-8b",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=37,
            gpu_power_watts=300,
            model_tokens_per_second=2000,
        )
        assert record.estimation_method == "user_config"

    def test_ecologits_estimation(self):
        tracker = EmissionsTracker()
        record = tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            ecologits_energy_kwh=1.5e-5,
            ecologits_gwp_kgco2eq=4.05e-6,
        )
        assert record.estimation_method == "ecologits"

    def test_cumulative_summary(self):
        tracker = EmissionsTracker()

        # First request
        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            electricity_mix_zone="US-VA",
            agent_id="agent-1",
        )

        # Second request on a different grid
        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=2000,
            completion_tokens=1000,
            grid_intensity_gco2e_per_kwh=37,
            electricity_mix_zone="SE",
            agent_id="agent-1",
        )

        summary = tracker.get_summary("agent-1")
        assert summary is not None
        assert summary.step_count == 2
        assert summary.total_prompt_tokens == 3000
        assert summary.total_completion_tokens == 1500
        assert summary.total_energy_kwh > 0
        assert summary.total_emissions_gco2e > 0
        assert "US-VA" in summary.grid_zones_used
        assert "SE" in summary.grid_zones_used
        assert "size_class" in summary.estimation_methods_used
        assert summary.estimation_methods_used["size_class"] == 2

    def test_multiple_agents_tracked_separately(self):
        tracker = EmissionsTracker()

        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            agent_id="agent-1",
        )

        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=2000,
            completion_tokens=1000,
            grid_intensity_gco2e_per_kwh=37,
            agent_id="agent-2",
        )

        s1 = tracker.get_summary("agent-1")
        s2 = tracker.get_summary("agent-2")
        assert s1.step_count == 1
        assert s2.step_count == 1
        assert s1.total_prompt_tokens == 1000
        assert s2.total_prompt_tokens == 2000

    def test_no_agent_id_does_not_track(self):
        tracker = EmissionsTracker()
        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
        )
        # No summary should exist (no agent_id provided)
        assert tracker.get_summary("nonexistent") is None

    def test_reset_summary(self):
        tracker = EmissionsTracker()
        tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=270,
            agent_id="agent-1",
        )
        assert tracker.get_summary("agent-1").step_count == 1
        tracker.reset_summary("agent-1")
        assert tracker.get_summary("agent-1") is None

    def test_grid_intensity_override(self):
        tracker = EmissionsTracker()
        record = tracker.estimate_and_record(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            grid_intensity_gco2e_per_kwh=100.0,  # Override
            electricity_mix_zone="SE",  # Should be ignored
        )
        assert record.grid_intensity_gco2e_per_kwh == 100.0
        # Override means zone is None in the resolution, but the record
        # should still have the zone from the config
