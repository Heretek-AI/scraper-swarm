"""Unit tests for the gateway-side fail-closed SSRF guard (Phase 01-live-smoke).

Covers the literals the gateway must pre-deny before delegating to Crawl4AI:
loopback, RFC1918, CGNAT, link-local/metadata, unspecified, IPv6 loopback,
``localhost``, and decimal/hex/octal/dword encodings that Smokescreen's Go
resolver cannot parse.

Evidence:
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/walkthrough.md::smoke-3-3
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/workbench_fix_walkthrough.md::mcp-proof

All tests are deterministic: literal checks never touch the network (DNS
resolution is stubbed out and asserted unused), and DNS-name checks stub the
resolver explicitly.
"""

from __future__ import annotations

import ipaddress

import pytest
from panel_api import ssrf_guard
from panel_api.ssrf_guard import (
    deny_reason_for_url,
    normalize_ip_literal,
    redact_url_for_audit,
    sanitize_details,
)


def test_dword_encodings_match_canonical_loopback_and_metadata():
    assert normalize_ip_literal("2130706433") == ipaddress.IPv4Address("127.0.0.1")
    assert normalize_ip_literal("2852039166") == ipaddress.IPv4Address("169.254.169.254")
    assert normalize_ip_literal("0x7f000001") == ipaddress.IPv4Address("127.0.0.1")
    assert normalize_ip_literal("0xA9.0xFE.0xA9.0xFE") == ipaddress.IPv4Address("169.254.169.254")
    assert normalize_ip_literal("0177.0.0.1") == ipaddress.IPv4Address("127.0.0.1")


def test_dns_names_are_not_literals():
    assert normalize_ip_literal("example.com") is None
    assert normalize_ip_literal("searxng") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://localhost/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://0xA9.0xFE.0xA9.0xFE/",
        "http://2852039166/",
    ],
)
def test_gateway_pre_deny_covers_literal_and_encoded_targets(url, monkeypatch):
    """Every literal/encoded target is denied without any DNS resolution."""

    def _no_dns(_host, timeout=3.0):
        raise AssertionError("resolver must not be consulted for literals")

    monkeypatch.setattr(ssrf_guard, "resolve_host", _no_dns)
    assert deny_reason_for_url(url) is not None


def test_gateway_denies_names_resolving_only_to_private(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["10.1.2.3"])
    assert deny_reason_for_url("http://internal.example/") is not None


def test_gateway_allows_names_resolving_to_global(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["93.184.216.34"])
    assert deny_reason_for_url("http://example.com/") is None


def test_gateway_defers_unresolvable_names_to_smokescreen(monkeypatch):
    """Opt-out path: with SWARM_SSRF_RESOLVER_FAIL_CLOSED=0, unresolvable names
    still delegate to Smokescreen (default since #6 is fail-closed)."""
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: [])
    monkeypatch.setenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", "0")
    assert deny_reason_for_url("http://example.com/") is None


def test_audit_redaction_preserves_no_secrets_invariant():
    details = sanitize_details({"token": "abc", "limit": 5, "Authorization": "Bearer x"})
    assert details["token"] == "***redacted***"
    assert details["Authorization"] == "***redacted***"
    assert details["limit"] == 5
    target = redact_url_for_audit("https://user:pass@example.com/p?q=1#frag")
    assert "pass" not in target and "q=1" not in target
    assert "example.com" in target


def test_audit_target_preserves_search_query_not_invalid_url():
    """Retry1 P0-3: search/research queries preserved; fetch URLs redacted; denied kept."""
    from panel_api.ssrf_guard import audit_target_for_action

    assert audit_target_for_action("web_search", "forensic query hello") == "forensic query hello"
    assert audit_target_for_action("deep_research", "what is XYZ?") == "what is XYZ?"
    # true URLs still redacted (no userinfo/query/fragment)
    redacted = audit_target_for_action("fetch_page", "https://user:pass@example.com/p?q=1#frag")
    assert "example.com" in redacted and "pass" not in redacted and "q=1" not in redacted
    # denied method/tool names preserved (not invalid-url)
    assert audit_target_for_action("unknown_tool", "nope_tool_xyz") == "nope_tool_xyz"
    assert audit_target_for_action("auth_denied", "tools/list") == "tools/list"


def test_is_expiry_passed_timezone_aware_fail_closed():
    from panel_api.ssrf_guard import is_expiry_passed

    assert is_expiry_passed(None) is False
    assert is_expiry_passed("2000-01-01T00:00:00+00:00") is True
    assert is_expiry_passed("2099-01-01T00:00:00+00:00") is False
    # naive timestamps treated as UTC
    assert is_expiry_passed("2000-01-01T00:00:00") is True
    # unparseable fails closed
    assert is_expiry_passed("not-a-date") is True


def test_session_expiry_fail_closed_null_empty():
    """Retry2 P0-1: sessions NULL/''/whitespace fail closed; valid future passes."""
    from panel_api.ssrf_guard import is_session_expired_fail_closed

    assert is_session_expired_fail_closed(None) is True
    assert is_session_expired_fail_closed("") is True
    assert is_session_expired_fail_closed("   ") is True
    assert is_session_expired_fail_closed("2000-01-01T00:00:00") is True
    assert is_session_expired_fail_closed("2000-01-01T00:00:00+00:00") is True
    assert is_session_expired_fail_closed("not-a-date") is True
    assert is_session_expired_fail_closed("2099-01-01T00:00:00+00:00") is False


def test_audit_scrubs_bearer_and_token_query_preserves_forensics():
    """Retry2 P0-2: swarm_sec_* in query redacted; ?token= in prose redacted; normal kept."""
    from panel_api.ssrf_guard import audit_target_for_action, sanitize_details

    leaked = audit_target_for_action("web_search", "lookup swarm_sec_deadbeef123 for x")
    assert "swarm_sec_deadbeef123" not in leaked
    assert "***redacted***" in leaked
    assert "for x" in leaked
    prose = audit_target_for_action(
        "web_search", "see https://example.com/p?token=SECRET123&x=1 for details"
    )
    assert "SECRET123" not in prose
    assert "?token=" not in prose
    assert "example.com" in prose
    assert "for details" in prose
    # normal query preserved verbatim
    assert audit_target_for_action("web_search", "forensic query xyz123") == "forensic query xyz123"
    # sanitize_details covers query/url keys + nested values
    d = sanitize_details({"query": "hi swarm_sec_abc123 bye", "limit": 5})
    assert "swarm_sec_abc123" not in d["query"]
    assert d["limit"] == 5
    d = sanitize_details({"url": "https://example.com/p?token=abc123"})
    assert "abc123" not in str(d["url"])
    assert "example.com" in str(d["url"])
    d = sanitize_details({"q": "normal forensic text"})
    assert d["q"] == "normal forensic text"


# ---- Ticket #6 (gateway contract v1): fail-closed resolver, URL length bound ----


def test_gateway_unresolvable_fails_closed_by_default(monkeypatch):
    """#6: unresolvable names are denied by default (fail-closed for automated keys)."""
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: [])
    monkeypatch.delenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", raising=False)
    assert deny_reason_for_url("http://nonexistent-peer.invalid/") is not None


def test_gateway_unresolvable_opt_out_delegates_to_smokescreen(monkeypatch):
    """#6: operators can opt back into delegation via env (documented choice)."""
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: [])
    monkeypatch.setenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", "0")
    assert deny_reason_for_url("http://example.com/") is None


def test_gateway_resolver_exception_fails_closed_by_default(monkeypatch):
    """#6: a raising resolver fails closed by default."""

    def _boom(_host, timeout=3.0):
        raise TimeoutError("dns timed out")

    monkeypatch.setattr(ssrf_guard, "resolve_host", _boom)
    monkeypatch.delenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", raising=False)
    assert deny_reason_for_url("http://example.com/") is not None


def test_gateway_url_over_length_bound_denied(monkeypatch):
    """#6: URLs longer than 2048 chars are denied without DNS."""
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["93.184.216.34"])
    long_url = "http://example.com/" + "a" * 2048
    assert len(long_url) > 2048
    assert deny_reason_for_url(long_url) is not None
    ok_url = "http://example.com/" + "a" * 100
    assert deny_reason_for_url(ok_url) is None


def test_gateway_url_with_control_chars_denied():
    """#6 adversarial: tab/newline tricks (WHATWG strips them, urlsplit does
    not) must not smuggle a loopback host past the parser."""
    assert deny_reason_for_url("http://127.0.0.1\t@example.com/") is not None
    assert deny_reason_for_url("http://127.0.0.1\n@example.com/") is not None
    assert deny_reason_for_url("http://example.com/\r\nX: 1") is not None
