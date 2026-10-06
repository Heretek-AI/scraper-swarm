"""Shared fail-closed SSRF guard for the P1 live engine stack (Phase 01-live-smoke).

Single enforcement helper used by the MCP Gateway *before* it delegates to
Crawl4AI (``CRAWL4AI_ALLOW_INTERNAL_URLS=true`` stays only for legitimate
in-stack peer DNS such as searxng/crawl4ai/valkey) and covered live by the
panel-api smoke harness via authenticated ``/mcp`` ``fetch_page`` calls.

Evidence:
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/walkthrough.md::smoke-3-3
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/workbench_fix_walkthrough.md::mcp-proof

Security notes:
- Smokescreen (Go resolver) only parses strict dotted-decimal/IPv6, so
  decimal/hex/octal-encoded literals never resolve there; the gateway
  normalizes every encoding with :mod:`ipaddress` semantics and denies them
  deterministically. Smokescreen remains the per-connection enforcement point
  for plain DNS names (including DNS-rebinding races the gateway cannot win).
- Nothing here ever logs bearer tokens or secret values; denial reasons echo
  the normalized host only, never userinfo/query/fragment.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Keys whose audit-detail values must be redacted to preserve the
# no-secrets-in-logs/audit invariant.
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "bearer", "cookie")


def sanitize_details(details: dict | None) -> dict:
    """Redacts secret-looking values from an audit details mapping."""
    redacted: dict = {}
    for key, value in (details or {}).items():
        lowered = str(key).lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            redacted[key] = "***redacted***"
        else:
            redacted[key] = value
    return redacted


def redact_url_for_audit(url: str) -> str:
    """Strips userinfo, query, and fragment so audit targets carry no secrets."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            return "(invalid-url)"
        text = f"{parts.scheme or 'http'}://{host}"
        if parts.path and parts.path != "/":
            text += parts.path
        return text
    except Exception:
        return "(invalid-url)"


def _part_to_int(part: str) -> int | None:
    """Parses one dotted-quad part accepting decimal, octal (leading 0), hex (0x)."""
    try:
        lowered = part.lower()
        if lowered.startswith("0x") and len(lowered) > 2:
            value = int(lowered[2:], 16)
        elif len(part) > 1 and part.startswith("0") and part.isdigit():
            value = int(part, 8)
        elif part.isdigit():
            value = int(part, 10)
        else:
            return None
    except ValueError:
        return None
    return value if 0 <= value <= 255 else None


def normalize_ip_literal(host: str):
    """Normalizes an IP literal in any common encoding to an ipaddress object.

    Handles strict dotted-decimal, IPv6 (with/without brackets), full-dword
    decimal (``2852030506``), single hex word (``0xa9fea9fe``), per-quad hex
    (``0xA9.0xFE.0xA9.0xFE``), per-quad octal (``0251.0376.0251.0376``), and
    shortened ``a.b.c``/``a.b`` inet_aton forms. Returns ``None`` when *host*
    is a DNS name rather than a numeric literal.
    """
    cleaned = host.strip().rstrip(".")
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    if not cleaned:
        return None
    # Fast path: strict literals (covers ::1, ::ffff:127.0.0.1, 0.0.0.0, ...).
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        pass
    if ":" in cleaned:
        return None
    # Single dword: decimal, hex, or octal.
    if "." not in cleaned:
        lowered = cleaned.lower()
        try:
            if lowered.startswith("0x") and len(lowered) > 2:
                dword = int(lowered[2:], 16)
            elif len(cleaned) > 1 and cleaned.startswith("0") and cleaned.isdigit():
                dword = int(cleaned, 8)
            elif cleaned.isdigit():
                dword = int(cleaned, 10)
            else:
                return None
        except ValueError:
            return None
        if 0 <= dword <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(dword)
        return None
    # Dotted forms with per-part encodings.
    parts = cleaned.split(".")
    if len(parts) == 4:
        values = [_part_to_int(p) for p in parts]
        if any(v is None for v in values):
            return None
        return ipaddress.IPv4Address(bytes(values))  # type: ignore[arg-type]
    if len(parts) in (2, 3):
        # inet_aton shortened forms: last part widens (a.b:c16 / a.b24).
        width = 16 if len(parts) == 3 else 24
        head = [_part_to_int(p) for p in parts[:-1]]
        if any(v is None for v in head):
            return None
        try:
            tail_raw = parts[-1].lower()
            if tail_raw.startswith("0x"):
                tail = int(tail_raw[2:], 16)
            elif len(parts[-1]) > 1 and parts[-1].startswith("0") and parts[-1].isdigit():
                tail = int(parts[-1], 8)
            elif parts[-1].isdigit():
                tail = int(parts[-1], 10)
            else:
                return None
        except ValueError:
            return None
        if not 0 <= tail <= (1 << width) - 1:
            return None
        tail_bytes = tail.to_bytes(width // 8, "big")
        return ipaddress.IPv4Address(bytes(head) + tail_bytes)  # type: ignore[arg-type]
    return None


def resolve_host(host: str, timeout: float = 3.0) -> list[str]:
    """Resolves a DNS hostname to string IPs (empty list when unresolvable)."""
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC)
        return sorted({info[4][0] for info in infos})
    except Exception:
        return []
    finally:
        socket.setdefaulttimeout(None)


def deny_reason_for_url(url: str, timeout: float = 3.0) -> str | None:
    """Fail-closed gateway pre-check; returns a denial reason or ``None`` to allow.

    Denies non-http(s) schemes, empty hosts, every non-globally-routable IP
    literal (loopback, RFC1918, CGNAT 100.64/10, link-local/metadata
    169.254/16, multicast, reserved, unspecified -- in decimal, octal, hex,
    dword, or shortened encodings), ``localhost``, and DNS names that resolve
    exclusively to non-global addresses. Unresolvable names return ``None`` so
    Smokescreen decides at connection time (it denies unresolvable hosts).
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return "unparseable URL (fail-closed)"
    if parts.scheme.lower() not in ("http", "https"):
        return f"unsupported scheme '{parts.scheme or '(none)'}' (fail-closed)"
    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        return "empty hostname (fail-closed)"
    if host.lower() == "localhost":
        return "localhost resolves to loopback (fail-closed)"

    literal = normalize_ip_literal(host)
    if literal is not None:
        if not literal.is_global:
            return f"host '{literal.compressed}' is not globally routable (fail-closed)"
        return None

    resolved = resolve_host(host, timeout=timeout)
    if not resolved:
        # Cannot resolve here: delegate to Smokescreen, which denies
        # unresolvable hosts at connection time.
        return None
    parsed = []
    for candidate in resolved:
        try:
            parsed.append(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    if parsed and all(not addr.is_global for addr in parsed):
        return f"host '{host}' resolves only to non-routable addresses (fail-closed)"
    return None
