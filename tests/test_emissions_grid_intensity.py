"""Tests for letta.emissions.grid_intensity — grid carbon intensity lookup."""

import pytest

from letta.emissions.grid_intensity import (
    WORLD_AVERAGE_GCO2E_PER_KWH,
    lookup_grid_intensity,
    resolve_grid_intensity,
)
from letta.emissions.ecologits_bridge import zone_to_ecologits_zone


class TestLookupGridIntensity:
    def test_sweden(self):
        assert lookup_grid_intensity("SE") == 37.0

    def test_finland(self):
        assert lookup_grid_intensity("FI") == 92.0

    def test_case_insensitive(self):
        assert lookup_grid_intensity("se") == 37.0
        assert lookup_grid_intensity("Se") == 37.0

    def test_sub_region_us_oregon(self):
        assert lookup_grid_intensity("US-OR") == 165.0

    def test_sub_region_us_oregon_bpa(self):
        assert lookup_grid_intensity("US-OR-BPA") == 23.0

    def test_unknown_zone_returns_world_average(self):
        assert lookup_grid_intensity("XX") == WORLD_AVERAGE_GCO2E_PER_KWH

    def test_none_returns_world_average(self):
        assert lookup_grid_intensity(None) == WORLD_AVERAGE_GCO2E_PER_KWH

    def test_unknown_sub_region_falls_back_to_country(self):
        """US-XX is not in the table, should fall back to US average."""
        us_avg = lookup_grid_intensity("US")
        result = lookup_grid_intensity("US-XX")
        assert result == us_avg  # Falls back to US country code

    def test_singapore(self):
        assert lookup_grid_intensity("SG") == 400.0


class TestResolveGridIntensity:
    def test_override_takes_priority(self):
        intensity, zone = resolve_grid_intensity(
            zone="SE",
            override_gco2e_per_kwh=100.0,
        )
        assert intensity == 100.0
        assert zone is None  # Override means zone is not used

    def test_zone_lookup_without_override(self):
        intensity, zone = resolve_grid_intensity(zone="SE")
        assert intensity == 37.0
        assert zone == "SE"

    def test_none_zone_no_override(self):
        intensity, zone = resolve_grid_intensity()
        assert intensity == WORLD_AVERAGE_GCO2E_PER_KWH
        assert zone is None

    def test_negative_override_raises(self):
        with pytest.raises(ValueError):
            resolve_grid_intensity(override_gco2e_per_kwh=-1.0)


class TestZoneToEcoLogitsZone:
    def test_alpha2_to_alpha3(self):
        assert zone_to_ecologits_zone("SE") == "SWE"
        assert zone_to_ecologits_zone("FI") == "FIN"
        assert zone_to_ecologits_zone("US") == "USA"

    def test_sub_region_maps_to_country(self):
        assert zone_to_ecologits_zone("US-OR") == "USA"
        assert zone_to_ecologits_zone("US-VA") == "USA"

    def test_unknown_returns_wor(self):
        assert zone_to_ecologits_zone("XX") == "WOR"

    def test_none_returns_wor(self):
        assert zone_to_ecologits_zone(None) == "WOR"
