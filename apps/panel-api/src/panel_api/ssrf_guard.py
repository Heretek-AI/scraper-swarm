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

import hashlib
import hmac
import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit

# Keys whose audit-detail values must be redacted to preserve the
# no-secrets-in-logs/audit invariant.
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "bearer", "cookie")

# Ticket #6 (gateway contract v1): input bounds and fail-closed resolver default.
#
# - URLs longer than MAX_URL_LENGTH are denied without DNS (fail-closed).
# - Unresolvable names / resolver exceptions fail closed by default so an
#   automated client cannot slip internal targets past the first layer.
#   Operators may opt back into delegation to Smokescreen (the second layer,
#   which denies unresolvable hosts at connection time) with
#   SWARM_SSRF_RESOLVER_FAIL_CLOSED=0. The opt-out is deliberate and logged.
MAX_URL_LENGTH = 2048


def resolver_fail_closed() -> bool:
    """True unless the operator explicitly opts into fail-open delegation."""
    raw = os.environ.get("SWARM_SSRF_RESOLVER_FAIL_CLOSED", "").strip().lower()
    return raw not in ("0", "false", "no", "off", "open")


# Phase 03 retry1 QA-B: search/research actions carry free-text queries, not
# URLs. Redacting them as URLs destroys forensics ("(invalid-url)").
_SEARCH_QUERY_ACTIONS = frozenset({"web_search", "deep_research"})


# Ticket #10 (gateway contract v1): audit query privacy modes. `verbatim`
# stores the scrubbed target as today; `hashed` stores a salted HMAC (equal
# queries correlate without being revealed); `redacted` stores only length
# and category. The hash chain always covers the STORED form, so it verifies
# in every mode.
AUDIT_QUERY_MODES = frozenset({"verbatim", "hashed", "redacted"})


def global_audit_query_mode() -> str:
    """The SWARM_AUDIT_QUERY_MODE default (verbatim unless configured)."""
    raw = os.environ.get("SWARM_AUDIT_QUERY_MODE", "verbatim").strip().lower()
    return raw if raw in AUDIT_QUERY_MODES else "verbatim"


def _redacted_query_form(action: str, target: str) -> str:
    if action in _SEARCH_QUERY_ACTIONS:
        kind = "query"
    elif "://" in target:
        kind = "url"
    else:
        kind = "other"
    return f"redacted:{kind}:len={len(target)}"


def apply_audit_query_mode(mode: str, action: str, target: str | None) -> str | None:
    """Maps a scrubbed audit target to its stored form for *mode*.

    Unknown modes fail closed to redacted (least disclosure). Hashed mode
    without SWARM_AUDIT_HMAC_SALT configured also falls back to redacted so
    a missing salt can never silently downgrade to verbatim.
    """
    if target is None or mode == "verbatim":
        return target
    if mode == "hashed":
        salt = os.environ.get("SWARM_AUDIT_HMAC_SALT", "")
        if salt:
            digest = hmac.new(
                salt.encode(),
                f"{action}\x00{target}".encode(),
                hashlib.sha256,
            ).hexdigest()
            return f"hmac-sha256:{digest}"
        return _redacted_query_form(action, target)
    return _redacted_query_form(action, target)


def is_expiry_passed(expires_at: str | None) -> bool:
    """Timezone-aware expiry check shared by gateway + panel-api sessions.

    ``None``/empty means no expiry (valid). Unparseable values fail closed
    (treated as expired) so a corrupt timestamp never grants access.

    NOTE: this None-means-valid semantic is for agent API keys only
    (explicitly-acknowledged never-expiring keys). Sessions MUST use
    :func:`is_session_expired_fail_closed` (NULL/empty/missing -> expired)
    so legacy rows cannot become immortal.
    """
    if not expires_at:
        return False
    try:
        from datetime import UTC, datetime

        exp = datetime.fromisoformat(str(expires_at))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp <= datetime.now(UTC)
    except Exception:
        return True


def is_session_expired_fail_closed(expires_at: str | None) -> bool:
    """Fail-closed session expiry: NULL/empty/missing/whitespace -> expired.

    Phase 03 retry2 QA-B P0-1: sessions must never be immortal. A missing
    ``expires_at`` (legacy row, old DB without the column, or empty string)
    is treated as expired (401) rather than valid. Valid future timestamps
    return False; past/naive-past/unparseable return True.
    """
    if expires_at is None:
        return True
    text = str(expires_at).strip()
    if not text:
        return True
    return is_expiry_passed(text)


# Phase 03 retry2 QA-B P0-2: secret-scrub patterns for audit forensics.
# Normal query text ("forensic query xyz123") must survive verbatim; only
# bearer-shaped material and key=value secrets are redacted.
_SWARM_SEC_RE = re.compile(r"swarm_sec_[A-Za-z0-9_\-]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.~\+/=]+")
_SECRET_KV_RE = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r"|token|secret|password|passwd|pwd|auth|bearer|session|cookie)"
    r"[_-]*\s*[:=]\s*)([^\s&;\"',]+)"
)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+")

_TRUNC_SUFFIX = "…[truncated]"


def _redact_embedded_urls(text: str) -> str:
    """Replaces every embedded http(s) URL with its redacted host-only form."""

    def _repl(m: re.Match) -> str:
        raw = m.group(0)
        # Preserve trailing punctuation that is not part of the URL.
        trail = ""
        while raw and raw[-1] in ".,;)]}!":
            trail = raw[-1] + trail
            raw = raw[:-1]
        if not raw:
            return m.group(0)
        try:
            redacted = redact_url_for_audit(raw)
        except Exception:
            redacted = "(invalid-url)"
        if redacted == "(invalid-url)":
            # Unparseable URL-like text: strip any secret KV inside it.
            return _SECRET_KV_RE.sub(r"\1***redacted***", m.group(0))
        return redacted + trail

    return _URL_IN_TEXT_RE.sub(_repl, text)


def scrub_secrets_from_text(text: str | None) -> str | None:
    """Scrubs bearer-shaped secrets from free text, preserving forensics.

    - embedded URLs -> host-only (query/fragment/userinfo stripped)
    - ``swarm_sec_*`` -> ``swarm_sec_***redacted***``
    - ``Bearer <tok>`` -> ``Bearer ***redacted***``
    - ``token=SECRET``/``secret: x`` style KV -> value redacted, key kept
    Normal prose without these patterns is returned unchanged.
    """
    if text is None:
        return None
    s = str(text)
    if not s:
        return s
    s = _redact_embedded_urls(s)
    s = _SWARM_SEC_RE.sub("swarm_sec_***redacted***", s)
    s = _BEARER_RE.sub("Bearer ***redacted***", s)
    s = _SECRET_KV_RE.sub(r"\1***redacted***", s)
    return s


def _truncate(text: str, limit: int = 2000) -> str:
    if len(text) > limit:
        return text[:limit] + _TRUNC_SUFFIX
    return text


def audit_target_for_action(action: str, target: str | None) -> str | None:
    """Preserves search/research query text; redacts only true URLs.

    Phase 03 retry2 QA-B P0-2: all returned targets are passed through
    :func:`scrub_secrets_from_text` so ``swarm_sec_*`` bearer material and
    ``?token=``/``secret=`` query/fragment/userinfo secrets never persist,
    while non-secret query forensics survive verbatim (truncated).

    For ``web_search``/``deep_research`` the target is a free-text query and
    must be kept verbatim (truncated) for forensics. If the query itself is
    exactly a URL, the redacted host-only form is stored; prose embedding a
    URL has that URL redacted inline. All other actions (fetch / scrape plus
    denied/unknown-tool method names) go through
    :func:`redact_url_for_action`, but a non-URL target that would otherwise
    become ``(invalid-url)`` is preserved verbatim (truncated, scrubbed) so
    denied rows keep their method/tool forensics instead of evidence-loss.
    """
    if target is None:
        return None
    text = str(target).strip()
    if not text:
        return None
    if action in _SEARCH_QUERY_ACTIONS:
        if "://" in text:
            redacted = redact_url_for_audit(text)
            if redacted != "(invalid-url)":
                return scrub_secrets_from_text(redacted)
        # Prose query: scrub embedded URLs + bearer/KV secrets, keep the rest.
        return _truncate(scrub_secrets_from_text(text) or "")
    redacted = redact_url_for_audit(text)
    if redacted != "(invalid-url)":
        return scrub_secrets_from_text(redacted)
    # Non-URL forensics (tool/method names like "nope_tool", "tools/list"):
    # preserve verbatim truncated (scrubbed) rather than destroying to invalid-url.
    return _truncate(scrub_secrets_from_text(text) or "")


def _scrub_detail_value(value):
    """Recursively scrubs secret-bearing strings inside audit detail values."""
    if isinstance(value, str):
        return scrub_secrets_from_text(value)
    if isinstance(value, list):
        return [_scrub_detail_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_detail_value(v) for v in value)
    if isinstance(value, dict):
        return {k: _scrub_detail_value(v) for k, v in value.items()}
    return value


def sanitize_details(details: dict | None) -> dict:
    """Redacts secret-looking values from an audit details mapping.

    Phase 03 retry2 QA-B P0-2: every string value (including ``query``/``url``
    keys and nested lists/dicts) is passed through
    :func:`scrub_secrets_from_text` so bearer-shaped query text and
    ``?token=``-in-prose secrets never persist, while non-secret forensics
    (limits, scopes, method names) survive.
    """
    redacted: dict = {}
    for key, value in (details or {}).items():
        lowered = str(key).lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            redacted[key] = "***redacted***"
        else:
            redacted[key] = _scrub_detail_value(value)
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
        addrs: set[str] = {str(info[4][0]) for info in infos}
        return sorted(addrs)
    except Exception:
        return []
    finally:
        socket.setdefaulttimeout(None)


def deny_reason_for_url(
    url: str, timeout: float = 3.0, fail_closed_on_unresolvable: bool | None = None
) -> str | None:
    """Fail-closed gateway pre-check; returns a denial reason or ``None`` to allow.

    Denies non-http(s) schemes, empty hosts, over-long URLs, every
    non-globally-routable IP literal (loopback, RFC1918, CGNAT 100.64/10,
    link-local/metadata 169.254/16, multicast, reserved, unspecified -- in
    decimal, octal, hex, dword, or shortened encodings), ``localhost``, and
    DNS names that resolve exclusively to non-global addresses.

    Unresolvable names and resolver exceptions fail closed by default
    (``SWARM_SSRF_RESOLVER_FAIL_CLOSED``, see :func:`resolver_fail_closed`);
    pass ``fail_closed_on_unresolvable=False`` (or set the env opt-out) to
    delegate those to Smokescreen, which denies unresolvable hosts at
    connection time.
    """
    if len(url) > MAX_URL_LENGTH:
        return f"URL longer than {MAX_URL_LENGTH} characters (fail-closed)"
    # WHATWG URL parsers strip ASCII tabs/newlines before parsing while
    # urlsplit does not — a tab can smuggle a loopback host past the parser
    # (e.g. http://127.0.0.1\t@example.com/). WHATWG browsers also treat
    # backslash as a path delimiter while urlsplit treats it as userinfo
    # (e.g. http://169.254.169.254\@example.com/ connects to 169.254.169.254
    # per browser but urlsplit sees host example.com). Deny control
    # characters and backslash outright so parser differentials cannot
    # bypass the pre-check.
    if any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in url):
        return "URL contains forbidden characters (fail-closed)"
    fail_closed = (
        resolver_fail_closed()
        if fail_closed_on_unresolvable is None
        else fail_closed_on_unresolvable
    )
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

    try:
        resolved = resolve_host(host, timeout=timeout)
    except Exception:
        # Resolver unavailable/timed out: fail closed by default so an
        # automated client cannot slip targets past the first layer.
        if fail_closed:
            return f"host '{host}' DNS resolution failed (fail-closed)"
        return None
    if not resolved:
        # Cannot resolve here: fail closed by default; Smokescreen remains
        # the per-connection enforcement point when the operator opts out.
        if fail_closed:
            return f"host '{host}' unresolvable (fail-closed)"
        return None
    parsed = []
    for candidate in resolved:
        try:
            parsed.append(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    if parsed and any(not addr.is_global for addr in parsed):
        return f"host '{host}' resolves to non-routable addresses (fail-closed)"
    return None
