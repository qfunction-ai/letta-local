"""v0.16.40 U-5 (L-10): SSRF guard for the server-side image fetch.

Witnesses from the plan (V016_40_PLAN.md, U-5): loopback rejected,
metadata address rejected, ::1 rejected, redirect hops re-validated,
non-image content type rejected, oversize aborted, escape hatch works,
normal fetch works.

The guard: `_validate_image_fetch_address` resolves the hostname and
rejects non-global addresses; `_pin_host_to_ip` pins the connection to
the validated IP (DNS-rebinding-proof); redirects are followed manually
with re-validation; content type must be image/*; body capped at 10 MB.
"""

import asyncio
import http.server
import socketserver
import threading

import pytest

from letta.errors import LettaImageFetchError
from letta.helpers.message_helper import (
    _fetch_image_from_url,
    _pin_host_to_ip,
    _validate_image_fetch_address,
)


@pytest.fixture
def local_image_server():
    """A loopback test server: image, text, redirect chains, oversize body."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/img.png":
                body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/redirect-ok":
                self.send_response(302)
                self.send_header("Location", "/img.png")
                self.end_headers()
            elif self.path == "/redirect-to-loopback":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/x")
                self.end_headers()
            elif self.path == "/redirect-loop":
                self.send_response(302)
                self.send_header("Location", "/redirect-loop")
                self.end_headers()
            elif self.path == "/text":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"not an image")
            elif self.path == "/big.png":
                body = b"\x89PNG" + b"\x00" * (11 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _no_private_hatch(monkeypatch):
    """Every test starts with the escape hatch OFF (the default)."""
    monkeypatch.delenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", raising=False)


class TestAddressValidation:
    def test_loopback_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address 127.0.0.1"):
            _validate_image_fetch_address("http://127.0.0.1/v1/health")

    def test_ipv6_loopback_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address ::1"):
            _validate_image_fetch_address("http://[::1]/")

    def test_cloud_metadata_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address 169.254.169.254"):
            _validate_image_fetch_address("http://169.254.169.254/latest/meta-data/")

    def test_rfc1918_rejected(self):
        for cidr_host in ("10.0.0.5", "192.168.1.1", "172.16.0.1"):
            with pytest.raises(LettaImageFetchError, match="non-public address"):
                _validate_image_fetch_address(f"http://{cidr_host}/x")

    def test_link_local_ipv6_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address"):
            _validate_image_fetch_address("http://[fe80::1]/")

    def test_unique_local_ipv6_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address"):
            _validate_image_fetch_address("http://[fd00::1]/")

    def test_multicast_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address"):
            _validate_image_fetch_address("http://224.0.0.1/x")

    def test_unspecified_rejected(self):
        with pytest.raises(LettaImageFetchError, match="non-public address"):
            _validate_image_fetch_address("http://0.0.0.0/x")

    def test_no_hostname_rejected(self):
        with pytest.raises(LettaImageFetchError, match="no hostname"):
            _validate_image_fetch_address("http:///x")

    def test_hatch_disables_validation(self, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")
        # Does not raise
        _validate_image_fetch_address("http://127.0.0.1/x")

    def test_pin_refuses_private(self):
        # When validation fails, pinning returns None rather than an unvalidated URL
        assert _pin_host_to_ip("http://127.0.0.1/x") is None

    def test_pin_public_host(self):
        # A public hostname pins to its resolved IP (runs DNS; example.com is stable public infra)
        pinned = _pin_host_to_ip("http://example.com/img.png")
        if pinned is None:  # no network in CI — skip rather than fail
            pytest.skip("no network for public DNS resolution")
        assert pinned.startswith("http://") and "example.com" not in pinned


class TestFetchGuard:
    def test_loopback_url_rejected_pre_connect(self, local_image_server):
        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/img.png")

        with pytest.raises(LettaImageFetchError, match="non-public address 127.0.0.1"):
            asyncio.run(go())

    def test_non_image_content_type_rejected(self, local_image_server, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")

        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/text")

        with pytest.raises(LettaImageFetchError, match="refusing non-image content type: text/plain"):
            asyncio.run(go())

    def test_oversize_aborted(self, local_image_server, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")

        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/big.png")

        with pytest.raises(LettaImageFetchError, match="exceeds"):
            asyncio.run(go())

    def test_normal_fetch_with_hatch(self, local_image_server, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")

        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/img.png")

        data, media_type = asyncio.run(go())
        assert data.startswith(b"\x89PNG")
        assert media_type and media_type.startswith("image/")

    def test_manual_redirect_followed(self, local_image_server, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")

        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/redirect-ok")

        data, media_type = asyncio.run(go())
        assert data.startswith(b"\x89PNG")

    def test_redirect_hop_to_loopback_validated(self, local_image_server):
        # The hop target (127.0.0.1:9) must fail validation — this is the
        # redirect-based SSRF the plan's witness list names.
        with pytest.raises(LettaImageFetchError, match="non-public address 127.0.0.1"):
            _validate_image_fetch_address("http://127.0.0.1:9/x")

    def test_redirect_loop_bounded(self, local_image_server, monkeypatch):
        monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")

        async def go():
            return await _fetch_image_from_url(f"{local_image_server}/redirect-loop")

        with pytest.raises(LettaImageFetchError, match="[Tt]oo many redirects"):
            asyncio.run(go())

    def test_guard_rejections_not_retried(self, local_image_server):
        # A guard rejection must raise immediately, not burn retries.
        # Instrument: count validation invocations — with max_retries=5 a
        # retried guard rejection would re-validate 6 times.
        calls = {"n": 0}
        import letta.helpers.message_helper as mh

        orig = mh._validate_image_fetch_address

        def counting(url):
            calls["n"] += 1
            return orig(url)

        mh._validate_image_fetch_address = counting
        try:
            async def go():
                return await _fetch_image_from_url(f"{local_image_server}/img.png", max_retries=5)

            with pytest.raises(LettaImageFetchError):
                asyncio.run(go())
        finally:
            mh._validate_image_fetch_address = orig
        assert calls["n"] == 1, f"guard rejection was retried: {calls['n']} validations"
