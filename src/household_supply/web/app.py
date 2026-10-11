from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from importlib.resources import files
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from household_supply.application import PlanAsgiApp

from .api import HouseholdWebJsonApi


Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _StaticAsset:
    resource_name: str
    content_type: bytes
    cache_control: bytes


def _header_values(scope: Mapping[str, Any], name: bytes) -> tuple[bytes, ...]:
    values = []
    for raw_key, raw_value in scope.get("headers", []):
        key = bytes(raw_key).lower()
        if key == name:
            values.append(bytes(raw_value))
    return tuple(values)


def _host_authority(raw: bytes) -> tuple[str, int | None] | None:
    try:
        value = raw.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return None
    if not value or any(character.isspace() for character in value) or "@" in value:
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1:
            return None
        host = value[1:closing]
        suffix = value[closing + 1 :]
        if not suffix:
            return host, None
        if not suffix.startswith(":") or not suffix[1:].isdigit():
            return None
        port = int(suffix[1:])
        return (host, port) if 1 <= port <= 65_535 else None
    if value.count(":") > 1:
        return None
    host, separator, port_raw = value.partition(":")
    host = host.rstrip(".")
    if not host:
        return None
    if not separator:
        return host, None
    if not port_raw.isdigit():
        return None
    port = int(port_raw)
    return (host, port) if 1 <= port <= 65_535 else None


def _is_loopback_host_name(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _safe_origin(
    raw: bytes,
    *,
    request_host: str,
    request_port: int | None,
    request_scheme: str,
    allow_non_loopback_hosts: bool,
) -> bool:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return False
    if value == "null":
        return False
    target = urlsplit(value)
    if target.scheme not in {"http", "https"} or not target.hostname:
        return False
    origin_host = target.hostname.lower().rstrip(".")
    if not allow_non_loopback_hosts and not _is_loopback_host_name(origin_host):
        return False
    if origin_host != request_host:
        return False
    if target.scheme != request_scheme:
        return False
    try:
        origin_port = target.port
    except ValueError:
        return False
    resolved_origin_port = origin_port or _default_port(target.scheme)
    resolved_request_port = request_port or _default_port(request_scheme)
    return resolved_origin_port == resolved_request_port


_ASSETS = {
    "/": _StaticAsset("index.html", b"text/html; charset=utf-8", b"no-store"),
    "/index.html": _StaticAsset("index.html", b"text/html; charset=utf-8", b"no-store"),
    "/assets/app.js": _StaticAsset(
        "app.js", b"text/javascript; charset=utf-8", b"no-cache"
    ),
    "/assets/reset.js": _StaticAsset(
        "reset.js", b"text/javascript; charset=utf-8", b"no-cache"
    ),
    "/assets/mixed_receipt_review.js": _StaticAsset(
        "mixed_receipt_review.js", b"text/javascript; charset=utf-8", b"no-cache"
    ),
    "/assets/mixed_shopping_workflow.js": _StaticAsset(
        "mixed_shopping_workflow.js", b"text/javascript; charset=utf-8", b"no-cache"
    ),
    "/assets/recipe_recommendations.js": _StaticAsset(
        "recipe_recommendations.js", b"text/javascript; charset=utf-8", b"no-cache"
    ),
    "/assets/styles.css": _StaticAsset(
        "styles.css", b"text/css; charset=utf-8", b"no-cache"
    ),
}


@dataclass(frozen=True, slots=True)
class HouseholdLocalWebApp:
    """Same-origin local web shell over the existing M10 JSON application.

    Static routes are fixed package resources; arbitrary filesystem paths are never
    accepted. Every non-static HTTP request is delegated to ``PlanAsgiApp`` and
    therefore preserves the existing JSON body limits and application semantics.
    """

    api: HouseholdWebJsonApi
    max_body_bytes: int = 1_048_576
    max_query_bytes: int = 4_096
    allow_non_loopback_hosts: bool = False
    _api_app: PlanAsgiApp = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_api_app",
            PlanAsgiApp(
                self.api,
                max_body_bytes=self.max_body_bytes,
                max_query_bytes=self.max_query_bytes,
            ),
        )

    async def __call__(
        self, scope: Mapping[str, Any], receive: Receive, send: Send
    ) -> None:
        if scope.get("type") != "http":
            raise RuntimeError("HouseholdLocalWebApp supports only ASGI HTTP scopes")

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))

        hosts = _header_values(scope, b"host")
        if len(hosts) != 1:
            await self._send_static_error(send, 400, b"Invalid Host header\n")
            return
        authority = _host_authority(hosts[0])
        if authority is None:
            await self._send_static_error(send, 400, b"Invalid Host header\n")
            return
        host, port = authority
        if not self.allow_non_loopback_hosts and not _is_loopback_host_name(host):
            await self._send_static_error(send, 400, b"Invalid Host header\n")
            return

        origins = _header_values(scope, b"origin")
        if len(origins) > 1 or (
            method not in {"GET", "HEAD", "OPTIONS"}
            and origins
            and not _safe_origin(
                origins[0],
                request_host=host,
                request_port=port,
                request_scheme=str(scope.get("scheme", "http")).lower(),
                allow_non_loopback_hosts=self.allow_non_loopback_hosts,
            )
        ):
            await self._send_static_error(send, 403, b"Cross-origin request rejected\n")
            return

        asset = _ASSETS.get(path)
        if asset is None:
            await self._api_app(scope, receive, send)
            return

        if method not in {"GET", "HEAD"}:
            await self._send_static_error(send, 405, b"Method Not Allowed\n")
            return

        query_string = scope.get("query_string", b"")
        if not isinstance(query_string, (bytes, bytearray)):
            raise RuntimeError("ASGI query_string must be bytes")
        if query_string:
            await self._send_static_error(send, 400, b"Invalid query\n")
            return

        body = (
            files("household_supply.web")
            .joinpath("assets")
            .joinpath(asset.resource_name)
            .read_bytes()
        )
        headers = [
            (b"content-type", asset.content_type),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", asset.cache_control),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
        ]
        if asset.resource_name == "index.html":
            headers.append(
                (
                    b"content-security-policy",
                    b"default-src 'self'; script-src 'self'; style-src 'self'; "
                    b"connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                    b"form-action 'self'; frame-ancestors 'none'",
                )
            )
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send(
            {
                "type": "http.response.body",
                "body": b"" if method == "HEAD" else body,
            }
        )

    @staticmethod
    async def _send_static_error(send: Send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
