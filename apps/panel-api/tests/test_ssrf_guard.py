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
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: [])
    assert deny_reason_for_url("http://example.com/") is None


def test_audit_redaction_preserves_no_secrets_invariant():
    details = sanitize_details({"token": "abc", "limit": 5, "Authorization": "Bearer x"})
    assert details["token"] == "***redacted***"
    assert details["Authorization"] == "***redacted***"
    assert details["limit"] == 5
    target = redact_url_for_audit("https://user:pass@example.com/p?q=1#frag")
    assert "pass" not in target and "q=1" not in target
    assert "example.com" in target
