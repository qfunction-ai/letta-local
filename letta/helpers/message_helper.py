import asyncio
import base64
import ipaddress
import mimetypes
import os
import socket
from urllib.parse import urlparse

import httpx

from letta import __version__, system
from letta.errors import LettaImageFetchError, LettaInvalidArgumentError
from letta.schemas.enums import MessageRole
from letta.schemas.letta_message_content import Base64Image, ImageContent, ImageSourceType, TextContent
from letta.schemas.message import Message, MessageCreate


# v0.16.40 (U-5 / L-10): SSRF guard for the server-side image fetch.
# An authenticated caller can put any URL in an image message; without this
# check the fetch would happily reach loopback, RFC 1918, link-local, or the
# cloud metadata address — and hand the response to the model.
_IMAGE_FETCH_MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap, streamed abort
_IMAGE_FETCH_MAX_REDIRECTS = 3  # followed MANUALLY, re-validated each hop


def _image_fetch_private_allowed() -> bool:
    """Operator escape hatch for lab setups serving images internally."""
    return os.getenv("LETTA_IMAGE_FETCH_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


def _validate_image_fetch_address(url: str) -> None:
    """Resolve the hostname and reject non-public destinations (SSRF guard).

    Rejects loopback, private (RFC 1918), link-local (incl. 169.254.169.254),
    unique-local IPv6, multicast, reserved, and unspecified addresses. DNS
    rebinding between check and connect is closed by pinning the connection
    to the resolved IP (the caller passes the validated IP to httpx).
    """
    if _image_fetch_private_allowed():
        return
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise LettaImageFetchError(url=url, reason="URL has no hostname")
    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise LettaImageFetchError(url=url, reason=f"DNS resolution failed: {e}")
    for ai in addr_infos:
        ip = ipaddress.ip_address(ai[4][0])
        # is_global covers loopback/private/link-local/unique-local; multicast
        # and reserved are global-routable in Python's taxonomy but never
        # legitimate fetch destinations — reject them explicitly.
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise LettaImageFetchError(
                url=url,
                reason=f"refusing to fetch from non-public address {ip} (SSRF guard; set LETTA_IMAGE_FETCH_ALLOW_PRIVATE=true for lab setups)",
            )


def _pin_host_to_ip(url: str) -> str | None:
    """Return a URL with the hostname replaced by its first resolved IP.

    The Host header is preserved via httpx's `headers` so virtual hosts keep
    working; the connection itself goes to the validated IP, which closes the
    DNS-rebinding window between validation and connect.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None
    # Validate EVERY resolved address, then connect to the first.
    for ai in addr_infos:
        ip = ipaddress.ip_address(ai[4][0])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved) and not _image_fetch_private_allowed():
            return None
    ip = addr_infos[0][4][0]
    # IPv6 literals need brackets in the URL
    ip_str = f"[{ip}]" if ":" in str(ip) else str(ip)
    port_part = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{ip_str}{port_part}{parsed.path}{'?' + parsed.query if parsed.query else ''}"


def _request_extensions(original_url: str) -> dict:
    """httpx request extensions for a pinned-IP fetch.

    v0.16.40 review V-2: httpx takes SNI and certificate hostname
    verification from the URL's host, NOT the Host header — a pinned-IP URL
    with a Host header alone fails TLS against every public cert (no IP
    SANs). Passing sni_hostname keeps the connection going to the validated
    IP while TLS asks for, and verifies, the ORIGINAL hostname.
    """
    parsed = urlparse(original_url)
    return {"sni_hostname": parsed.hostname} if parsed.hostname else {}


def _hop_headers(original_url: str, pinned_url: str, base_headers: dict) -> dict:
    """Headers + extensions for one request hop: Host header when pinned,
    SNI extension always (V-2)."""
    hop_headers = dict(base_headers)
    if pinned_url != original_url:
        parsed = urlparse(original_url)
        hop_headers["Host"] = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return hop_headers


async def _fetch_image_from_url(url: str, max_retries: int = 1, timeout_seconds: float = 5.0) -> tuple[bytes, str | None]:
    """
    Async helper to fetch image from URL without blocking the event loop.
    Retries once on timeout to handle transient network issues.

    v0.16.40 (U-5/L-10): the fetch is SSRF-guarded — destination addresses
    are validated before connecting, redirects are followed manually with
    re-validation at each hop (max 3), the content type must be image/*
    (a MISSING content type is rejected too — review should-fix), and the
    body is capped at 10 MB with a genuinely STREAMED abort (V-3:
    client.stream + Content-Length precheck + per-chunk counting — a
    multi-gigabyte or endless body aborts at the cap, never in memory).

    Args:
        url: URL of the image to fetch
        max_retries: Number of retry attempts (default: 1)
        timeout_seconds: Total timeout in seconds (default: 5.0)

    Returns:
        Tuple of (image_bytes, media_type)

    Raises:
        LettaImageFetchError: If image fetch fails after all retries, or is blocked by the SSRF guard
    """
    # Connect timeout is half of total timeout, capped at 3 seconds
    connect_timeout = min(timeout_seconds / 2, 3.0)
    timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout)
    base_headers = {"User-Agent": f"Letta/{__version__}"}

    # SSRF guard: validate the destination BEFORE any request
    _validate_image_fetch_address(url)
    # Pin the connection to the validated IP (rebinding-proof), keep Host
    # and SNI on the ORIGINAL hostname (V-2)
    pinned_url = _pin_host_to_ip(url) or url

    current_url, current_pinned = url, pinned_url
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=base_headers, follow_redirects=False) as client:
                hop_headers = _hop_headers(current_url, current_pinned, base_headers)
                extensions = _request_extensions(current_url)
                # V-3: ALL requests stream (stream=True) — headers are
                # consumed for decisions, the body is read only from the
                # final response, counted chunk by chunk. A hostile or
                # endless body never buffers through the client.
                hops = 0
                request = client.build_request(
                    "GET", current_pinned, headers=hop_headers, extensions=extensions
                )
                response = await client.send(request, stream=True)
                while response.has_redirect_location and hops < _IMAGE_FETCH_MAX_REDIRECTS:
                    location = response.headers.get("location", "")
                    if not location:
                        break
                    from urllib.parse import urljoin

                    next_url = urljoin(current_url, location)
                    _validate_image_fetch_address(next_url)
                    next_pinned = _pin_host_to_ip(next_url) or next_url
                    current_url, current_pinned = next_url, next_pinned
                    hop_headers = _hop_headers(current_url, current_pinned, base_headers)
                    extensions = _request_extensions(current_url)
                    await response.aclose()  # release before the next hop
                    request = client.build_request(
                        "GET", current_pinned, headers=hop_headers, extensions=extensions
                    )
                    response = await client.send(request, stream=True)
                    hops += 1
                try:
                    if response.has_redirect_location:
                        raise LettaImageFetchError(url=url, reason=f"Too many redirects (max {_IMAGE_FETCH_MAX_REDIRECTS})")
                    response.raise_for_status()
                    # Content type must be image/*; a MISSING content type
                    # is rejected (review should-fix: never accept unknown
                    # content blindly).
                    media_type = response.headers.get("content-type", "")
                    if not media_type:
                        raise LettaImageFetchError(url=url, reason="refusing response with no content type")
                    if not media_type.split(";")[0].strip().lower().startswith("image/"):
                        raise LettaImageFetchError(url=url, reason=f"refusing non-image content type: {media_type}")
                    # V-3: Content-Length precheck BEFORE reading
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > _IMAGE_FETCH_MAX_BYTES:
                        raise LettaImageFetchError(
                            url=url, reason=f"image exceeds {_IMAGE_FETCH_MAX_BYTES} byte cap (content-length)"
                        )
                    # V-3: streamed read with per-chunk counting
                    image_bytes = b""
                    async for chunk in response.aiter_bytes():
                        image_bytes += chunk
                        if len(image_bytes) > _IMAGE_FETCH_MAX_BYTES:
                            raise LettaImageFetchError(
                                url=url, reason=f"image exceeds {_IMAGE_FETCH_MAX_BYTES} byte cap"
                            )
                    return image_bytes, media_type
                finally:
                    await response.aclose()
        except httpx.TimeoutException as e:
            last_exception = e
            if attempt < max_retries:
                # Brief delay before retry
                await asyncio.sleep(0.5)
                continue
            # Final attempt failed
            raise LettaImageFetchError(url=url, reason=f"Timeout after {max_retries + 1} attempts: {e}")
        except LettaImageFetchError:
            # Guard rejections and policy failures are NOT retried
            raise
        except (httpx.RemoteProtocolError, httpx.HTTPStatusError) as e:
            # Don't retry on protocol errors or HTTP errors (4xx, 5xx)
            raise LettaImageFetchError(url=url, reason=str(e))
        except Exception as e:
            raise LettaImageFetchError(url=url, reason=f"Unexpected error: {e}")

    # Should never reach here, but just in case
    raise LettaImageFetchError(url=url, reason=f"Failed after {max_retries + 1} attempts: {last_exception}")


def _validate_image_source_url(url: str) -> str:
    """Validate supported image URL schemes before any fetch/resolve happens."""
    parsed_url = urlparse(url)
    scheme = parsed_url.scheme.lower()

    if scheme in ("http", "https", "data"):
        return scheme

    raise LettaInvalidArgumentError(
        message=(
            f"Unsupported image URL scheme '{parsed_url.scheme or '<missing>'}'. "
            "Only 'http', 'https', and 'data' image URLs are supported. "
            "Local file paths (file://) are not allowed."
        ),
        argument_name="url",
    )


def _parse_data_image_url(url: str) -> tuple[str, str]:
    """Parse a data URL and return its base64 payload and media type."""
    try:
        header, image_data = url.split(",", 1)
        header_parts = header.split(";")
        image_media_type = header_parts[0].replace("data:", "") or "image/jpeg"
        return image_data, image_media_type
    except ValueError as exc:
        raise LettaImageFetchError(url=url[:100] + "...", reason="Invalid data URL format") from exc


async def convert_message_creates_to_messages(
    message_creates: list[MessageCreate],
    agent_id: str,
    timezone: str,
    run_id: str,
    wrap_user_message: bool = True,
    wrap_system_message: bool = True,
) -> list[Message]:
    # Process all messages concurrently
    tasks = [
        _convert_message_create_to_message(
            message_create=create,
            agent_id=agent_id,
            timezone=timezone,
            run_id=run_id,
            wrap_user_message=wrap_user_message,
            wrap_system_message=wrap_system_message,
        )
        for create in message_creates
    ]
    return await asyncio.gather(*tasks)


async def _convert_message_create_to_message(
    message_create: MessageCreate,
    agent_id: str,
    timezone: str,
    run_id: str,
    wrap_user_message: bool = True,
    wrap_system_message: bool = True,
) -> Message:
    """Converts a MessageCreate object into a Message object, applying wrapping if needed."""
    if not isinstance(message_create, MessageCreate):
        raise ValueError(f"Expected MessageCreate, got {type(message_create).__name__}")

    # Extract message content
    if isinstance(message_create.content, str) and message_create.content != "":
        message_content = [TextContent(text=message_create.content)]
    elif isinstance(message_create.content, list) and len(message_create.content) > 0:
        message_content = message_create.content
    else:
        raise ValueError("Message content is empty or invalid")

    # Validate message role (assistant messages are allowed but won't be wrapped)
    assert message_create.role in {
        MessageRole.user,
        MessageRole.system,
        MessageRole.assistant,
    }, f"Invalid message role: {message_create.role}"

    for content in message_content:
        if isinstance(content, TextContent):
            # Apply wrapping only to user and system messages
            if message_create.role == MessageRole.user and wrap_user_message:
                content.text = system.package_user_message(user_message=content.text, timezone=timezone)
            elif message_create.role == MessageRole.system and wrap_system_message:
                content.text = system.package_system_message(system_message=content.text, timezone=timezone)
        elif isinstance(content, ImageContent):
            if content.source.type == ImageSourceType.url:
                # Convert URL image to Base64Image if needed
                url = content.source.url
                url_scheme = _validate_image_source_url(url)

                if url_scheme == "data":
                    image_data, image_media_type = _parse_data_image_url(url)
                    content.source = Base64Image(media_type=image_media_type, data=image_data)
                    continue

                image_bytes, image_media_type = await _fetch_image_from_url(url)
                if not image_media_type:
                    image_media_type, _ = mimetypes.guess_type(url)

                image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
                content.source = Base64Image(media_type=image_media_type, data=image_data)
            if content.source.type == ImageSourceType.letta and not content.source.data:
                # TODO: hydrate letta image with data from db
                pass

    return Message(
        agent_id=agent_id,
        role=message_create.role,
        content=message_content,
        name=message_create.name,
        model=None,  # assigned later?
        tool_calls=None,  # irrelevant
        tool_call_id=None,
        otid=message_create.otid,
        sender_id=message_create.sender_id,
        group_id=message_create.group_id,
        batch_item_id=message_create.batch_item_id,
        run_id=run_id,
    )


async def _resolve_url_to_base64(url: str) -> tuple[str, str]:
    """Resolve URL to base64 data and media type."""
    url_scheme = _validate_image_source_url(url)

    if url_scheme == "data":
        return _parse_data_image_url(url)

    image_bytes, media_type = await _fetch_image_from_url(url)
    media_type = media_type or mimetypes.guess_type(url)[0] or "image/png"

    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    return image_data, media_type


async def resolve_tool_return_images(func_response: str | list) -> str | list:
    """Resolve URL and LettaImage sources to base64 for tool returns."""
    if isinstance(func_response, str):
        return func_response

    resolved = []
    for part in func_response:
        if isinstance(part, ImageContent):
            if part.source.type == ImageSourceType.url:
                image_data, media_type = await _resolve_url_to_base64(part.source.url)
                part.source = Base64Image(media_type=media_type, data=image_data)
            elif part.source.type == ImageSourceType.letta and not part.source.data:
                pass
            resolved.append(part)
        elif isinstance(part, TextContent):
            resolved.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "image" and part.get("source", {}).get("type") == "url":
                url = part["source"].get("url")
                if url:
                    image_data, media_type = await _resolve_url_to_base64(url)
                    resolved.append(
                        ImageContent(
                            source=Base64Image(
                                media_type=media_type,
                                data=image_data,
                                detail=part.get("source", {}).get("detail"),
                            )
                        )
                    )
                else:
                    resolved.append(part)
            elif part.get("type") == "text":
                resolved.append(TextContent(text=part.get("text", "")))
            else:
                resolved.append(part)
        else:
            resolved.append(part)

    return resolved
