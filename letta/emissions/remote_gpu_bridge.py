"""GPU metrics sidecar client.

Queries a lightweight GPU metrics API on the inference host to get real-time
power draw. This is Tier 1 of Layer 3 — the most accurate method for remote
inference because the measurement happens on the right machine.

Supported sidecar formats:
  - nvidia-gpu-metrics-api: GET /gpu → [{power: {current_watts: X}}]
  - DCGM-Exporter: GET /metrics → DCGM_FI_DEV_POWER_USAGE{gpu="0"} X
  - nvidia-smi-web/agent: GET /status → {power_status: "XW / YW"}
"""

import re
from typing import Optional

import httpx
from letta.log import get_logger

logger = get_logger(__name__)

# Request timeout for sidecar queries — must be fast, inference is waiting
_SIDECAR_TIMEOUT_S = 2.0


class RemoteGPUMonitor:
    """Client for GPU metrics sidecar APIs."""

    def __init__(self, metrics_url: str, metrics_format: str = "auto"):
        self.metrics_url = metrics_url.rstrip("/")
        self.metrics_format = metrics_format
        self._client = httpx.AsyncClient(timeout=_SIDECAR_TIMEOUT_S)

    async def read_power_watts(self) -> Optional[float]:
        """Query the GPU metrics sidecar for current power draw.

        Returns:
            Power in watts, or None if the query fails.
        """
        if self.metrics_format == "auto":
            return await self._auto_detect_and_read()

        fmt = self.metrics_format
        if fmt == "nvidia_metrics_api":
            return await self._read_nvidia_metrics_api()
        elif fmt == "dcgm_exporter":
            return await self._read_dcgm_exporter()
        elif fmt == "nvidia_smi_web":
            return await self._read_nvidia_smi_web()
        else:
            logger.error("Unknown metrics format: %s", fmt)
            return None

    async def _auto_detect_and_read(self) -> Optional[float]:
        """Try to auto-detect the sidecar format and read."""
        # Try DCGM-Exporter first (Prometheus format, most common in K8s)
        if "/metrics" in self.metrics_url or ":9400" in self.metrics_url:
            return await self._read_dcgm_exporter()

        # Try nvidia-gpu-metrics-api (port 8000, /gpu endpoint)
        if "/gpu" in self.metrics_url or ":8000" in self.metrics_url:
            return await self._read_nvidia_metrics_api()

        # Try nvidia-smi-web/agent (/status endpoint)
        if "/status" in self.metrics_url:
            return await self._read_nvidia_smi_web()

        # Default: try nvidia-gpu-metrics-api format
        return await self._read_nvidia_metrics_api()

    async def _read_nvidia_metrics_api(self) -> Optional[float]:
        """Read from nvidia-gpu-metrics-api (tlockcuff).

        GET /gpu → [{power: {current_watts: 450}}]
        """
        url = f"{self.metrics_url}/gpu"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list) and len(data) > 0:
                # Sum power across all GPUs
                total_power = 0.0
                for gpu in data:
                    power_info = gpu.get("power", {})
                    current = power_info.get("current_watts")
                    if current is not None:
                        total_power += float(current)
                if total_power > 0:
                    return total_power

            logger.warning("Unexpected response format from nvidia-gpu-metrics-api: %s", url)
            return None
        except Exception as e:
            logger.warning("Failed to query GPU metrics sidecar at %s: %s", url, e)
            return None

    async def _read_dcgm_exporter(self) -> Optional[float]:
        """Read from DCGM-Exporter (NVIDIA official Prometheus endpoint).

        GET /metrics → DCGM_FI_DEV_POWER_USAGE{gpu="0"} 450.5
        """
        url = self.metrics_url
        if not url.endswith("/metrics"):
            url = f"{url}/metrics"

        try:
            response = await self._client.get(url)
            response.raise_for_status()
            text = response.text

            # Parse Prometheus exposition format
            # DCGM_FI_DEV_POWER_USAGE{gpu="0", ...} 450.5
            total_power = 0.0
            found = False
            for line in text.splitlines():
                if line.startswith("DCGM_FI_DEV_POWER_USAGE"):
                    found = True
                    # Extract the value after the last space
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            total_power += float(parts[-1])
                        except ValueError:
                            continue

            if found:
                return total_power

            logger.warning("No DCGM_FI_DEV_POWER_USAGE metric found at %s", url)
            return None
        except Exception as e:
            logger.warning("Failed to query DCGM-Exporter at %s: %s", url, e)
            return None

    async def _read_nvidia_smi_web(self) -> Optional[float]:
        """Read from nvidia-smi-web/agent.

        GET /status → {power_status: "121W / 350W"}
        """
        url = f"{self.metrics_url}/status"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()

            # nvidia-smi-web returns a list of GPUs
            if isinstance(data, list):
                total_power = 0.0
                for gpu in data:
                    power_str = gpu.get("power_status", "")
                    # Parse "121W / 350W" — extract the first number
                    match = re.match(r"([\d.]+)\s*W", power_str)
                    if match:
                        total_power += float(match.group(1))
                if total_power > 0:
                    return total_power

            logger.warning("Unexpected response format from nvidia-smi-web: %s", url)
            return None
        except Exception as e:
            logger.warning("Failed to query nvidia-smi-web at %s: %s", url, e)
            return None

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
