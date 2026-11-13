from __future__ import annotations

import re
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_VIDEO_PATH = re.compile(r"/(?:reels?|videos?)/([A-Za-z0-9_-]+)(?:/|$)", re.IGNORECASE)
_SHARE_PATH = re.compile(r"/share/([rv])/([A-Za-z0-9_-]+)(?:/|$)", re.IGNORECASE)
_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "failed to resolve",
    "max retries exceeded",
    "remote end closed connection",
)

DEFAULT_NETWORK_PREFLIGHT_TIMEOUT = 12


class FacebookNetworkUnavailable(RuntimeError):
    pass


class FacebookLoginRequired(RuntimeError):
    pass


def facebook_auth_block_reason(current_url: str, page_source: str = "") -> str | None:
    try:
        parts = urlsplit(str(current_url or ""))
    except ValueError:
        return None
    if not _facebook_host(parts.hostname or ""):
        return None
    path = (parts.path or "").lower()
    if "/checkpoint" in path or "/challenge" in path or "/captcha" in path:
        return "checkpoint"
    if "/recover" in path:
        return "recovery"
    if re.search(r"<form[^>]*(?:captcha|challenge|checkpoint)[^>]*>", page_source or "", re.IGNORECASE):
        return "checkpoint"
    if "/login" in path or re.search(
        r"<form[^>]*id=[\"']login_form[\"']", page_source or "", re.IGNORECASE
    ):
        return "login"
    return None


def facebook_login_required(current_url: str, page_source: str = "") -> bool:
    return facebook_auth_block_reason(current_url, page_source) is not None


def _resolve_bounded(host: str, port: int, family: int, timeout: float):
    """يحلّ الاسم ضمن مهلة معلنة؛ getaddrinfo لا تقبل مهلة وقد تعلّق دقائق."""
    outcome: dict = {}

    def resolve():
        try:
            outcome["value"] = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError as error:
            outcome["error"] = error

    worker = threading.Thread(target=resolve, daemon=True, name="facebook-dns-probe")
    worker.start()
    worker.join(max(0.0, timeout))
    if worker.is_alive():
        return []
    return outcome.get("value") or []


def facebook_network_route(
    proxy_url: str = "",
    force_ipv4: bool = True,
    timeout: float = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT,
) -> str | None:
    """Return the working route; IPv4 and IPv6 each receive the full timeout."""
    timeout = max(1.0, min(float(timeout), 15.0))
    if proxy_url:
        try:
            proxy = urlsplit(proxy_url.strip())
            scheme = proxy.scheme.lower()
            port = proxy.port
            if (
                scheme not in {"http", "https", "socks5", "socks5h"}
                or not proxy.hostname
                or proxy.username is not None
                or proxy.password is not None
                or proxy.path not in {"", "/"}
                or proxy.query
                or proxy.fragment
                or (port is not None and not 1 <= port <= 65535)
            ):
                return None
            port = port or (443 if scheme == "https" else 1080 if scheme.startswith("socks5") else 80)
            with socket.create_connection((proxy.hostname, port), timeout=timeout):
                return "proxy"
        except (OSError, ValueError):
            return None

    requested_families = (socket.AF_INET, socket.AF_INET6) if force_ipv4 else (socket.AF_UNSPEC,)
    grouped = {}
    deadlines = {}
    resolver_order = []
    seen = set()
    for family in requested_families:
        family_deadline = time.monotonic() + timeout
        resolved = _resolve_bounded(
            "www.facebook.com", 443, family, max(0.0, family_deadline - time.monotonic())
        )
        if not resolved:
            continue
        deadlines[family] = family_deadline
        for address_family, socktype, proto, _, address in resolved:
            key = (address_family, tuple(address))
            if key in seen:
                continue
            seen.add(key)
            if address_family not in grouped:
                grouped[address_family] = []
                resolver_order.append(address_family)
            grouped[address_family].append((socktype, proto, address))

    if force_ipv4:
        family_order = [family for family in (socket.AF_INET, socket.AF_INET6) if grouped.get(family)]
    else:
        family_order = [family for family in resolver_order if grouped.get(family)]
    if not family_order:
        return None

    for address_family in family_order:
        addresses = grouped[address_family]
        family_deadline = deadlines.get(address_family, time.monotonic() + timeout)
        for address_index, (socktype, proto, address) in enumerate(addresses):
            remaining = family_deadline - time.monotonic()
            if remaining <= 0:
                break
            address_timeout = remaining / (len(addresses) - address_index)
            try:
                with socket.socket(address_family, socktype, proto) as connection:
                    connection.settimeout(address_timeout)
                    if force_ipv4 and address_family == socket.AF_INET:
                        connection.bind(("0.0.0.0", 0))
                    connection.connect(address)
                    if address_family == socket.AF_INET6:
                        return "ipv6"
                    return "ipv4" if address_family == socket.AF_INET else "direct"
            except OSError:
                continue
    return None


def facebook_network_reachable(
    proxy_url: str = "",
    force_ipv4: bool = True,
    timeout: float = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT,
) -> bool:
    return facebook_network_route(proxy_url, force_ipv4, timeout) is not None


def _facebook_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in ("facebook.com", "fb.com", "fb.watch"))


def facebook_media_id(url: str) -> str | None:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.lower() not in {"http", "https"}
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port not in {80, 443})
        or not _facebook_host(parts.hostname or "")
    ):
        return None
    match = _VIDEO_PATH.search(parts.path or "")
    if match:
        return match.group(1)
    share_match = _SHARE_PATH.search(parts.path or "")
    if share_match:
        return f"facebook-share:{share_match.group(1).lower()}:{share_match.group(2)}"
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key in ("v", "video_id", "story_fbid"):
        value = str(query.get(key, "")).strip()
        if value and re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return value
    if (parts.hostname or "").lower().endswith("fb.watch"):
        short_id = (parts.path or "").strip("/").split("/", 1)[0]
        return f"fb.watch:{short_id}" if short_id else None
    return None


def canonicalize_facebook_video_url(url: str) -> str | None:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.lower() not in {"http", "https"}
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port not in {80, 443})
        or not _facebook_host(host)
    ):
        return None
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    match = _VIDEO_PATH.search(path)
    if match:
        video_id = match.group(1)
        path = "/watch/"
        query = urlencode({"v": video_id})
        canonical_host = "www.facebook.com"
    elif (share_match := _SHARE_PATH.search(path)):
        # Preserve the share URL; yt-dlp can follow its normal redirect using the configured session.
        path = f"/share/{share_match.group(1).lower()}/{share_match.group(2)}/"
        query = ""
        canonical_host = "www.facebook.com"
    elif path.rstrip("/").lower() in {"/watch", "/watch.php", "/video.php"}:
        query_values = dict(parse_qsl(parts.query, keep_blank_values=True))
        video_id = next((str(query_values.get(key, "")).strip() for key in ("v", "video_id") if query_values.get(key)), "")
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]+", video_id):
            return None
        path = "/watch/"
        query = urlencode({"v": video_id})
        canonical_host = "www.facebook.com"
    elif host == "fb.watch" or host.endswith(".fb.watch"):
        short_id = (path or "").strip("/").split("/", 1)[0]
        if not short_id:
            return None
        path = f"/{short_id}"
        query = ""
        canonical_host = "fb.watch"
    else:
        return None
    return urlunsplit(("https", canonical_host, path, query, ""))


def is_transient_facebook_error(error: BaseException) -> bool:
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = re.sub(r"[_-]+", " ", str(current).lower())
        if any(marker in message for marker in _TRANSIENT_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False
