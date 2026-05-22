"""Emissions tracking for letta-local.

Three-layer estimation:
  Layer 1: EcoLogits for supported cloud providers (OpenAI, Anthropic, etc.)
  Layer 2: Model-size-class estimator for unsupported cloud providers
  Layer 3: Local inference with four-tier accuracy chain:
    Tier 1: GPU metrics sidecar (remote hardware measurement)
    Tier 2: CodeCarbon (same-machine hardware measurement)
    Tier 3: User-provided hardware config (gpu_power_watts + tps)
    Tier 4: Model-size-class estimator (fallback)

The estimator does the math, the step records the result.
Summaries are computed on demand from the DB, not stored redundantly.
"""

from letta.emissions.estimator import EmissionsRecord, ModelSizeClass, estimate_emissions
from letta.emissions.tracker import estimate_step_emissions

__all__ = [
    "EmissionsRecord",
    "ModelSizeClass",
    "estimate_emissions",
    "estimate_step_emissions",
]
