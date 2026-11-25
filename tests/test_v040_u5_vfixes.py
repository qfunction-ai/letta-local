"""v0.16.40 review V-2/V-3 + should-fix witnesses.

V-2: pinned-IP HTTPS needs sni_hostname (SNI + cert verification follow the
     URL host, not the Host header — a bare pinned URL fails TLS on every
     public cert). Local TLS server with a self-signed cert for img.test.
V-3: the 10 MB cap must STREAM — a Content-Length precheck + per-chunk
     counting; an endless chunked body aborts at the cap without downloading
     it all.
Should-fix: a missing Content-Type is rejected.
"""

import asyncio
import os
import socket
import socketserver
import ssl
import subprocess
import threading
import http.server
from unittest.mock import patch
from pathlib import Path

import pytest

from letta.errors import LettaImageFetchError
from letta.helpers.message_helper import _fetch_image_from_url


def _make_tls_server(tmp_path, cn="img.test"):
    """Self-signed TLS server for cn, serving /img.png. Returns (port, cert)."""
    key, cert = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key), "-out", str(cert),
         "-days", "1", "-nodes", "-subj", f"/CN={cn}", "-addext", f"subjectAltName=DNS:{cn}"],
        check=True, capture_output=True,
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/img.png":
                body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port, str(cert)


@pytest.fixture(autouse=True)
def _allow_private(monkeypatch):
    monkeypatch.setenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "true")


def _resolve_img_test(port):
    """getaddrinfo stub: img.test -> 127.0.0.1 (the local TLS server)."""
    def fake_getaddrinfo(host, *a, **kw):
        if host == "img.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return socket.getaddrinfo(host, *a, **kw)
    return fake_getaddrinfo


class TestV2SNI:
    def test_https_pinned_ip_fetch_succeeds(self, tmp_path, monkeypatch):
        """The pinned-IP URL with the right SNI extension fetches and verifies
        against the ORIGINAL hostname's certificate (V-2)."""
        port, cert = _make_tls_server(tmp_path)
        monkeypatch.setattr("letta.helpers.message_helper.socket.getaddrinfo", _resolve_img_test(port))
        # trust the test CA: httpx/ssl honor SSL_CERT_FILE
        monkeypatch.setenv("SSL_CERT_FILE", cert)
        # explicit port: the pin keeps it; a default-port URL would pin to :443
        data, media_type = asyncio.run(_fetch_image_from_url(f"https://img.test:{port}/img.png"))
        assert data.startswith(b"\x89PNG")
        assert media_type.startswith("image/")

    def test_https_wrong_certificate_fails(self, tmp_path, monkeypatch):
        """Same server, but the URL asks for a DIFFERENT name than the
        certificate carries: TLS verification must fail (SNI is honest)."""
        port, cert = _make_tls_server(tmp_path, cn="other.test")

        # img.test resolves to the server whose cert is for other.test
        def fake(host, *a, **kw):
            if host == "img.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return socket.getaddrinfo(host, *a, **kw)

        monkeypatch.setattr("letta.helpers.message_helper.socket.getaddrinfo", fake)
        with pytest.raises(LettaImageFetchError):
            asyncio.run(_fetch_image_from_url(f"https://img.test:{port}/img.png"))


class TestV3StreamingCap:
    def test_endless_chunked_aborts_at_cap(self, monkeypatch):
        """An endless chunked body aborts at the 10 MB cap WITHOUT downloading
        it all: the server counts bytes sent; the fetch must fail while the
        server has sent only a bit over the cap."""

        sent = {"bytes": 0, "stopped": False}

        class Endless(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # chunked requires 1.1

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunk = b"\x00" * 65536
                try:
                    while sent["bytes"] < 64 * 1024 * 1024:  # would be 64 MB if uncapped
                        # chunked encoding framing
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                        sent["bytes"] += len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    sent["stopped"] = True

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), Endless)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        with pytest.raises(LettaImageFetchError, match="exceeds"):
            asyncio.run(_fetch_image_from_url(f"http://127.0.0.1:{port}/endless.png", timeout_seconds=30))
        # The abort happened EARLY: the server sent the cap plus bounded
        # buffering slack (64KB chunks + socket buffers), NOT the 64 MB it
        # would have pushed uncapped.
        slack = 2 * 1024 * 1024  # chunked framing + kernel socket buffers
        assert sent["bytes"] <= 10 * 1024 * 1024 + slack, (
            f"server sent {sent['bytes']} bytes — the cap did not abort the stream"
        )
        srv.shutdown()

    def test_content_length_precheck(self):
        """A declared Content-Length over the cap is rejected before any body
        read (the server never sends the body)."""

        class Never(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(50 * 1024 * 1024))
                self.end_headers()
                # body intentionally never written

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), Never)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with pytest.raises(LettaImageFetchError, match="content-length"):
            asyncio.run(_fetch_image_from_url(f"http://127.0.0.1:{port}/big.png", timeout_seconds=10))
        srv.shutdown()


class TestMissingContentType:
    def test_missing_content_type_rejected(self):
        class NoType(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                # NO content-type header
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"\x89PNG")

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), NoType)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with pytest.raises(LettaImageFetchError, match="no content type"):
            asyncio.run(_fetch_image_from_url(f"http://127.0.0.1:{port}/x.png"))
        srv.shutdown()
