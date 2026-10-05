"""Security-focused tests for the catalog, renderer and policy checker."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from swarmd.catalog import ServiceEntry, load_catalog
from swarmd.policy import PolicyViolation, check_compose
from swarmd.render import IntentError, Selection, render_stack

REPO = Path(__file__).resolve().parents[3]
STACK = Path("/var/lib/scraper-swarm")
SECCOMP = STACK / "seccomp"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(REPO / "catalog" / "services")


def _render(catalog, wanted):
    return render_stack(catalog, wanted, STACK, REPO, allow_draft=True)


def test_real_catalog_loads_and_ids_match_filenames(catalog):
    assert {"searxng", "crawl4ai", "valkey", "egress-web"} <= set(catalog)


def test_unverified_entries_are_refused_by_default(catalog):
    with pytest.raises(IntentError, match="unverified"):
        render_stack(catalog, {"searxng": Selection()}, STACK, REPO)


def test_dependencies_and_proxy_are_pulled_in(catalog):
    compose = _render(catalog, {"searxng": Selection()})
    assert {"searxng", "valkey", "egress-web"} <= set(compose["services"])


def test_every_service_is_hardened(catalog):
    compose = _render(catalog, {"searxng": Selection(), "crawl4ai": Selection()})
    for name, svc in compose["services"].items():
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert not str(svc["user"]).startswith("0"), name
        assert "ports" not in svc and "privileged" not in svc, name
        assert {"mem_limit", "cpus", "pids_limit"} <= set(svc), name


def test_internet_route_exists_only_on_the_proxy(catalog):
    compose = _render(catalog, {"searxng": Selection(), "crawl4ai": Selection()})
    for name, svc in compose["services"].items():
        assert ("egress-out" in svc["networks"]) == (name == "egress-web"), name
    nets = compose["networks"]
    assert nets["svc"]["internal"] is True and nets["egress-web"]["internal"] is True
    assert "internal" not in nets["egress-out"]


def test_web_facing_services_get_proxy_env_and_peers_bypass(catalog):
    compose = _render(catalog, {"crawl4ai": Selection(), "searxng": Selection()})
    env = compose["services"]["crawl4ai"]["environment"]
    assert env["HTTPS_PROXY"] == "http://egress-web:4750"
    assert "egress-web" not in env["NO_PROXY"].split(",")  # never bypass the proxy itself
    assert "valkey" in env["NO_PROXY"].split(",")  # in-stack peers are reached directly
    assert "environment" not in compose["services"]["valkey"]  # internal-only: no proxy env


def test_chromium_seccomp_profile_is_a_file_in_our_dir(catalog):
    compose = _render(catalog, {"crawl4ai": Selection()})
    opts = compose["services"]["crawl4ai"]["security_opt"]
    assert f"seccomp={SECCOMP / 'chromium.json'}" in opts


def test_params_are_schema_validated_and_mapped_to_env(catalog):
    ok = _render(catalog, {"crawl4ai": Selection(params={"llm_provider": "openai/gpt-4o"})})
    assert ok["services"]["crawl4ai"]["environment"]["LLM_PROVIDER"] == "openai/gpt-4o"
    for bad in ({"llm_provider": "x\nPRIVILEGED=1"}, {"llm_provider": "a b"}, {"surprise": "1"}):
        with pytest.raises(IntentError):
            _render(catalog, {"crawl4ai": Selection(params=bad)})


def test_unknown_service_and_profile_are_rejected(catalog):
    with pytest.raises(IntentError, match="unknown service"):
        _render(catalog, {"nope": Selection()})
    with pytest.raises(IntentError, match="profile"):
        _render(catalog, {"valkey": Selection(profile="heavy")})


def test_secrets_use_env_file_inside_stack_dir_only(catalog):
    compose = _render(catalog, {"searxng": Selection()})
    assert compose["services"]["searxng"]["env_file"] == [str(STACK / "env" / "searxng.env")]


# ---- Policy checker: assert it catches tampering, independent of the renderer ----------------


@pytest.fixture
def good(catalog):
    return _render(catalog, {"crawl4ai": Selection()})


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda s: s.update(privileged=True), "privileged"),
        (lambda s: s.update(cap_add=["SYS_ADMIN"]), "cap_add"),
        (lambda s: s.update(network_mode="host"), "network_mode"),
        (lambda s: s.update(pid="host"), "'pid'"),
        (lambda s: s.update(ports=["80:80"]), "ports"),
        (lambda s: s.update(devices=["/dev/kmem"]), "devices"),
        (lambda s: s.update(user="0:0"), "non-root"),
        (lambda s: s.update(user="root"), "non-root"),
        (lambda s: s.update(cap_drop=[]), "cap_drop"),
        (lambda s: s.update(security_opt=[]), "no-new-privileges"),
        (
            lambda s: s.update(security_opt=["no-new-privileges:true", "seccomp=unconfined"]),
            "seccomp",
        ),
        (
            lambda s: s.update(security_opt=["no-new-privileges:true", "seccomp=/tmp/evil.json"]),
            "seccomp",
        ),
        (
            lambda s: s.update(volumes=["/var/run/docker.sock:/var/run/docker.sock"]),
            "docker socket",
        ),
        (lambda s: s.update(volumes=["/etc:/host-etc"]), "bind mounts"),
        (lambda s: s.update(volumes=["../x:/x"]), "bind mounts"),
        (lambda s: s.update(networks=["svc", "egress-out"]), "egress-out"),
        (lambda s: s.update(networks=["host"]), "not allowed"),
        (lambda s: s.update(env_file=["/etc/shadow"]), "env_file"),
        (lambda s: s.update(environment={"A": "x\ny"}), "newline"),
        (lambda s: s.pop("mem_limit"), "mem_limit"),
        (lambda s: s.pop("pids_limit"), "pids_limit"),
    ],
)
def test_policy_rejects_tampered_service(good, mutation, needle):
    compose = copy.deepcopy(good)
    mutation(compose["services"]["crawl4ai"])
    with pytest.raises(PolicyViolation) as exc:
        check_compose(compose, STACK, proxies={"egress-web"}, seccomp_dir=SECCOMP)
    assert needle in str(exc.value)


def test_policy_rejects_non_internal_network(good):
    compose = copy.deepcopy(good)
    compose["networks"]["svc"]["internal"] = False
    with pytest.raises(PolicyViolation, match="internal"):
        check_compose(compose, STACK, proxies={"egress-web"}, seccomp_dir=SECCOMP)


# ---- Catalog validation -----------------------------------------------------------------------


def _entry(**over):
    base = yaml.safe_load((REPO / "catalog/services/crawl4ai.yaml").read_text())
    base.update(over)
    return base


@pytest.mark.parametrize(
    "over",
    [
        {"typo_field": 1},
        {"hardening": {"user": "0:0"}},
        {"hardening": {"user": "root"}},
        {"networks": ["svc", "egress-out"]},  # non-support service on the internet network
        {"id": "Bad_ID"},
        {"resources": {"lite": {"cpus": 1, "mem": "1g"}}},  # no 'standard'
        {"image": {"repo": "a/b", "build": "x"}},
        {"image": {"repo": "a/b", "digest": "sha256:short"}},
        {"param_env": {"ghost": "X"}},
    ],
)
def test_catalog_rejects_unsafe_or_malformed_entries(over):
    with pytest.raises(ValueError):  # pydantic.ValidationError subclasses ValueError
        ServiceEntry.model_validate(_entry(**over))
