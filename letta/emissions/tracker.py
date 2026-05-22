"""EmissionsTracker — the main orchestrator for per-request emissions estimation.

Dispatches to the right estimation tier based on available data and config:
  Layer 1: EcoLogits (cloud API calls via supported providers)
  Layer 2: Model-size-class estimator (unsupported cloud providers)
  Layer 3:
    Tier 1: GPU metrics sidecar (remote hardware measurement)
    Tier 2: CodeCarbon (same-machine hardware measurement)
    Tier 3: User-provided hardware config
    Tier 4: Model-size-class fallback

Also manages cumulative EmissionsSummary per agent.
"""

import time
from typing import Optional

from pydantic import BaseModel, Field

from letta.emissions.estimator import EmissionsRecord, estimate_emissions
from letta.emissions.grid_intensity import resolve_grid_intensity
from letta.log import get_logger

logger = get_logger(__name__)


class EmissionsSummary(BaseModel):
    """Cumulative emissions for an agent. Stored on AgentState."""

    total_energy_kwh: float = 0.0
    total_emissions_gco2e: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    step_count: int = 0
    grid_zones_used: dict[str, int] = Field(
        default_factory=dict,
        description="Zone → count of requests using that zone.",
    )
    estimation_methods_used: dict[str, int] = Field(
        default_factory=dict,
        description="Method → count of requests using that method.",
    )


class EmissionsTracker:
    """Orchestrates per-request emissions estimation and cumulative tracking.

    Usage:
        tracker = EmissionsTracker()

        # Per-request estimation
        record = tracker.estimate_and_record(
            llm_config=config,
            prompt_tokens=1000,
            completion_tokens=500,
            request_latency_s=2.5,
        )

        # Cumulative summary
        summary = tracker.get_summary(agent_id="agent-123")
    """

    def __init__(self):
        # Per-agent cumulative summaries
        self._summaries: dict[str, EmissionsSummary] = {}

    def estimate_and_record(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        grid_intensity_gco2e_per_kwh: Optional[float] = None,
        electricity_mix_zone: Optional[str] = None,
        gpu_power_watts: Optional[float] = None,
        model_tokens_per_second: Optional[float] = None,
        gpu_metrics_url: Optional[str] = None,
        start_power_watts: Optional[float] = None,
        end_power_watts: Optional[float] = None,
        request_latency_s: Optional[float] = None,
        ecologits_energy_kwh: Optional[float] = None,
        ecologits_gwp_kgco2eq: Optional[float] = None,
        codecarbon_energy_kwh: Optional[float] = None,
        codecarbon_emissions_kgco2eq: Optional[float] = None,
        agent_id: Optional[str] = None,
    ) -> EmissionsRecord:
        """Estimate emissions for a single LLM request.

        Resolves grid intensity, dispatches to the right estimation method,
        and optionally updates the cumulative summary for the agent.
        """
        # Resolve grid intensity
        resolved_intensity, resolved_zone = resolve_grid_intensity(
            zone=electricity_mix_zone,
            override_gco2e_per_kwh=grid_intensity_gco2e_per_kwh,
        )

        # Dispatch to the right estimation method
        record = estimate_emissions(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            grid_intensity_gco2e_per_kwh=resolved_intensity,
            electricity_mix_zone=resolved_zone,
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

        # Override the resolved zone on the record if our resolution found one
        # When grid_intensity_gco2e_per_kwh is provided (override), resolve_grid_intensity
        # returns None for the zone, but we still want to record the original zone
        if electricity_mix_zone is not None and record.electricity_mix_zone is None:
            record = record.model_copy(update={"electricity_mix_zone": electricity_mix_zone})

        # Update cumulative summary
        if agent_id is not None:
            self._update_summary(agent_id, record)

        return record

    def _update_summary(self, agent_id: str, record: EmissionsRecord) -> None:
        """Update the cumulative EmissionsSummary for an agent."""
        if agent_id not in self._summaries:
            self._summaries[agent_id] = EmissionsSummary()

        summary = self._summaries[agent_id]
        summary.total_energy_kwh += record.energy_kwh
        summary.total_emissions_gco2e += record.emissions_gco2e
        summary.total_prompt_tokens += record.prompt_tokens
        summary.total_completion_tokens += record.completion_tokens
        summary.step_count += 1

        # Track zone usage
        zone = record.electricity_mix_zone or "unknown"
        summary.grid_zones_used[zone] = summary.grid_zones_used.get(zone, 0) + 1

        # Track method usage
        method = record.estimation_method
        summary.estimation_methods_used[method] = summary.estimation_methods_used.get(method, 0) + 1

    def get_summary(self, agent_id: str) -> Optional[EmissionsSummary]:
        """Get the cumulative emissions summary for an agent."""
        return self._summaries.get(agent_id)

    def reset_summary(self, agent_id: str) -> None:
        """Reset the cumulative emissions summary for an agent."""
        self._summaries.pop(agent_id, None)
