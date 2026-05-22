"""CodeCarbon bridge for same-machine local inference.

Wraps CodeCarbon with start_task/stop_task for per-request emissions tracking.
Works when inference happens on the same machine (Letta in Docker with
--gpus all and /sys/class/powercap bind mount).

CodeCarbon is an optional dependency. If not installed, this module degrades
gracefully — the tracker falls back to the size-class estimator.
"""

from typing import Optional

from letta.log import get_logger

logger = get_logger(__name__)

# Check if codecarbon is available
_CODECARBON_AVAILABLE = False
try:
    from codecarbon import OfflineEmissionsTracker
    _CODECARBON_AVAILABLE = True
except ImportError:
    pass


class LocalInferenceTracker:
    """Wraps CodeCarbon for per-request local inference emissions tracking.

    Only useful when inference happens inside the same container or on the
    same machine with host device access (--gpus all + /sys/class/powercap).
    """

    def __init__(
        self,
        country_iso_code: str = "USA",
        force_mode_constant: bool = True,
        measure_power_secs: int = 10,
        output_dir: str = "/tmp/codecarbon",
        save_to_file: bool = False,
        save_to_api: bool = False,
    ):
        if not _CODECARBON_AVAILABLE:
            raise RuntimeError(
                "CodeCarbon is not installed. Install with: pip install codecarbon>=3.0.0"
            )

        # force_mode_constant=True bypasses psutil 500ms overhead
        # OfflineEmissionsTracker: no data sent to their API
        self._tracker = OfflineEmissionsTracker(
            country_iso_code=country_iso_code,
            force_mode_constant=force_mode_constant,
            measure_power_secs=measure_power_secs,
            output_dir=output_dir,
            save_to_file=save_to_file,
            save_to_api=save_to_api,
        )
        self._tracker.start()

    def start_request(self, request_id: str) -> None:
        """Mark the start of an LLM inference request."""
        if not _CODECARBON_AVAILABLE:
            return
        self._tracker.start_task(f"inference_{request_id}")

    def stop_request(self) -> Optional[dict]:
        """Mark the end and return per-request emissions data.

        Returns:
            Dict with 'energy_kwh' and 'emissions_kgco2eq', or None on failure.
        """
        if not _CODECARBON_AVAILABLE:
            return None

        try:
            task_emissions = self._tracker.stop_task()
            if task_emissions is None:
                return None

            return {
                "energy_kwh": task_emissions.energy,
                "emissions_kgco2eq": task_emissions.emissions,
            }
        except Exception as e:
            logger.warning("CodeCarbon stop_request failed: %s", e)
            return None

    def stop(self) -> None:
        """Stop the CodeCarbon tracker."""
        if not _CODECARBON_AVAILABLE:
            return
        try:
            self._tracker.stop()
        except Exception as e:
            logger.warning("CodeCarbon stop failed: %s", e)


def is_codecarbon_available() -> bool:
    """Check if CodeCarbon is installed and importable."""
    return _CODECARBON_AVAILABLE
