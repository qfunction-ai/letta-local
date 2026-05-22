"""Grid carbon intensity lookup.

Loads built-in data from grid_intensity.yaml, supports user override via
LLMConfig.grid_intensity_gco2e_per_kwh, and optionally queries real-time
APIs (Electricity Maps, WattTime) for dynamic carbon intensity.

Resolution order:
  1. llm_config.grid_intensity_gco2e_per_kwh (user override, highest priority)
  2. grid_intensity_lookup(llm_config.electricity_mix_zone) (built-in table)
  3. Optional real-time API (Electricity Maps / WattTime) if configured
  4. World average (458 gCO2e/kWh, last resort)
"""

from pathlib import Path
from typing import Optional

import yaml
from letta.log import get_logger

logger = get_logger(__name__)

# Default: world average, per Ember 2025
WORLD_AVERAGE_GCO2E_PER_KWH = 458.0

# Built-in data path
_DATA_DIR = Path(__file__).parent / "data"
_GRID_INTENSITY_FILE = _DATA_DIR / "grid_intensity.yaml"

# Loaded data, cached after first load
_grid_intensity_data: Optional[dict[str, float]] = None


def _load_grid_intensity_data() -> dict[str, float]:
    """Load the built-in grid intensity table from YAML."""
    global _grid_intensity_data
    if _grid_intensity_data is not None:
        return _grid_intensity_data

    if not _GRID_INTENSITY_FILE.exists():
        logger.warning("Grid intensity data file not found: %s", _GRID_INTENSITY_FILE)
        _grid_intensity_data = {}
        return _grid_intensity_data

    with open(_GRID_INTENSITY_FILE, "r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        logger.error("Grid intensity data file is not a dict: %s", type(raw))
        _grid_intensity_data = {}
        return _grid_intensity_data

    # Convert all keys to uppercase for consistent lookup
    _grid_intensity_data = {k.upper(): float(v) for k, v in raw.items()}
    return _grid_intensity_data


def lookup_grid_intensity(zone: Optional[str]) -> float:
    """Look up grid carbon intensity for a zone.

    Args:
        zone: Zone key. ISO 3166-1 alpha-2 country code (e.g., 'SE', 'US')
              or sub-region key (e.g., 'US-OR', 'US-OR-BPA').
              Case-insensitive.

    Returns:
        Grid carbon intensity in gCO2e/kWh, or world average if zone not found.
    """
    if zone is None:
        return WORLD_AVERAGE_GCO2E_PER_KWH

    data = _load_grid_intensity_data()
    key = zone.upper()

    if key in data:
        return data[key]

    # Try two-letter country code as fallback for sub-region codes
    # e.g., "US-OR" -> try "US"
    if "-" in key:
        country = key.split("-")[0]
        if country in data:
            logger.info(
                "Zone '%s' not found in grid intensity table, falling back to country '%s' (%.0f gCO2e/kWh)",
                zone, country, data[country],
            )
            return data[country]

    logger.warning(
        "Zone '%s' not found in grid intensity table, using world average (%.0f gCO2e/kWh)",
        zone, WORLD_AVERAGE_GCO2E_PER_KWH,
    )
    return WORLD_AVERAGE_GCO2E_PER_KWH


def resolve_grid_intensity(
    zone: Optional[str] = None,
    override_gco2e_per_kwh: Optional[float] = None,
) -> tuple[float, Optional[str]]:
    """Resolve grid carbon intensity with full priority chain.

    Priority:
      1. override_gco2e_per_kwh (user config, highest priority)
      2. Built-in table lookup via zone
      3. World average (458 gCO2e/kWh)

    Returns:
        Tuple of (intensity_gco2e_per_kwh, zone_used).
        zone_used is None if the override was used.
    """
    if override_gco2e_per_kwh is not None:
        if override_gco2e_per_kwh < 0:
            raise ValueError(f"grid_intensity must be non-negative, got {override_gco2e_per_kwh}")
        return override_gco2e_per_kwh, None

    intensity = lookup_grid_intensity(zone)
    return intensity, zone


# --- Real-time API integration (optional, future) ---

def lookup_electricity_maps(
    zone: str,
    api_key: str,
) -> Optional[float]:
    """Query Electricity Maps API for real-time carbon intensity.

    Not yet implemented — placeholder for future integration.
    Electricity Maps API: https://api.electricitymap.org/v3/carbon-intensity/latest?zone={zone}
    """
    # TODO: Implement Electricity Maps API integration
    logger.debug("Electricity Maps integration not yet implemented for zone %s", zone)
    return None


def lookup_watttime(
    region: str,
    api_key: str,
) -> Optional[float]:
    """Query WattTime API for real-time carbon intensity.

    Not yet implemented — placeholder for future integration.
    WattTime API: https://api2.watttime.org/v2/index/
    """
    # TODO: Implement WattTime API integration
    logger.debug("WattTime integration not yet implemented for region %s", region)
    return None
