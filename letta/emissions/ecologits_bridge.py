"""EcoLogits bridge for supported cloud providers.

EcoLogits patches the OpenAI/Anthropic/etc. client SDKs. After init, every
response gets response.impacts with model-specific power data. We extract
those impacts into our EmissionsRecord format.

EcoLogits is an optional dependency. If not installed, this module degrades
gracefully — the tracker falls back to the size-class estimator.
"""

from typing import Optional

from letta.log import get_logger

logger = get_logger(__name__)

# Check if ecologits is available
_ECOLOGITS_AVAILABLE = False
try:
    from ecologits import EcoLogits
    _ECOLOGITS_AVAILABLE = True
except ImportError:
    pass


def is_ecologits_available() -> bool:
    """Check if EcoLogits is installed and importable."""
    return _ECOLOGITS_AVAILABLE


def init_ecologits(zone: str = "WOR") -> bool:
    """Initialize EcoLogits for supported cloud providers.

    Call this once at server startup. After init, EcoLogits patches the
    OpenAI/Anthropic/etc. client SDKs automatically.

    Args:
        zone: ISO 3166-1 alpha-3 electricity_mix_zone code.
              Defaults to "WOR" (world average).
              Common values: "WOR", "USA", "SWE", "FIN", "FRA", "SGP".

    Returns:
        True if initialization succeeded, False if EcoLogits isn't available.
    """
    if not _ECOLOGITS_AVAILABLE:
        logger.info("EcoLogits not installed, skipping initialization")
        return False

    try:
        EcoLogits.init(providers=["openai", "anthropic", "mistralai"], electricity_mix_zone=zone)
        logger.info("EcoLogits initialized with zone=%s", zone)
        return True
    except Exception as e:
        logger.error("EcoLogits initialization failed: %s", e)
        return False


def extract_impacts(response) -> Optional[dict]:
    """Extract emissions impacts from an EcoLogits-patched response.

    After EcoLogits.init(), supported clients (OpenAI, Anthropic, etc.)
    automatically add an `impacts` attribute to response objects.

    Args:
        response: The LLM response object (may or may not have .impacts).

    Returns:
        Dict with 'energy_kwh' and 'gwp_kgco2eq', or None if no impacts.
    """
    if not _ECOLOGITS_AVAILABLE:
        return None

    if not hasattr(response, "impacts") or response.impacts is None:
        return None

    try:
        impacts = response.impacts
        energy_kwh = impacts.energy.value.mean  # kWh
        gwp_kgco2eq = impacts.gwp.value.mean     # kgCO2eq

        return {
            "energy_kwh": energy_kwh,
            "gwp_kgco2eq": gwp_kgco2eq,
        }
    except (AttributeError, TypeError) as e:
        logger.debug("Failed to extract EcoLogits impacts: %s", e)
        return None


# Map our zone keys (ISO 3166-1 alpha-2) to EcoLogits zone keys (alpha-3).
# EcoLogits uses ADEME Base Empreinte zones.
_ALPHA2_TO_ALPHA3 = {
    "SE": "SWE",
    "FI": "FIN",
    "FR": "FRA",
    "DK": "DNK",
    "NL": "NLD",
    "IE": "IRL",
    "DE": "DEU",
    "NO": "NOR",
    "IS": "ISL",
    "US": "USA",
    "SG": "SGP",
    "JP": "JPN",
    "KR": "KOR",
    "AU": "AUS",
    "IN": "IND",
    "BR": "BRA",
    "GB": "GBR",
    "CA": "CAN",
    "WOR": "WOR",
}


def zone_to_ecologits_zone(zone: Optional[str]) -> str:
    """Convert our zone key to EcoLogits-compatible alpha-3 zone code.

    Args:
        zone: Our zone key (ISO 3166-1 alpha-2 or sub-region like 'US-OR').

    Returns:
        EcoLogits-compatible zone code (alpha-3), or 'WOR' for unknown.
    """
    if zone is None:
        return "WOR"

    # Sub-region codes (US-OR, US-VA, etc.) — map to country
    if "-" in zone:
        country = zone.split("-")[0].upper()
        return _ALPHA2_TO_ALPHA3.get(country, "WOR")

    return _ALPHA2_TO_ALPHA3.get(zone.upper(), "WOR")
