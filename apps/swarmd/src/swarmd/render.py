"""Render a hardened docker compose document from catalog entries + validated intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from .catalog import PROFILES, ServiceEntry
from .policy import check_compose

PROJECT = "scraper-swarm"
PROXY_SERVICE = "egress-web"
PROXY_URL = f"http://{PROXY_SERVICE}:4750"


class IntentError(ValueError):
    """The panel asked for something the catalog does not allow."""


@dataclass
class Selection:
    profile: str = "standard"
    params: dict[str, Any] = field(default_factory=dict)


def _validate_selection(entry: ServiceEntry, sel: Selection) -> None:
    if sel.profile not in PROFILES or sel.profile not in entry.resources:
        raise IntentError(f"{entry.id}: profile '{sel.profile}' not available")
    try:
        jsonschema.validate(sel.params, entry.params_schema)
    except jsonschema.ValidationError as exc:
        raise IntentError(f"{entry.id}: invalid params: {exc.message}") from exc
    for key, val in sel.params.items():
        if isinstance(val, str) and ("\n" in val or "\r" in val):
            raise IntentError(f"{entry.id}: param '{key}' contains a newline")


def resolve(catalog: dict[str, ServiceEntry], wanted: dict[str, Selection]) -> dict[str, Selection]:
    """Return selections plus their transitive dependencies (deps use the standard profile)."""
    out = dict(wanted)
    stack = list(wanted)
    while stack:
        sid = stack.pop()
        if sid not in catalog:
            raise IntentError(f"unknown service '{sid}'")
        for dep in catalog[sid].requires:
            if dep not in out:
                out[dep] = Selection()
                stack.append(dep)
    # Any selection that reaches the internet needs the proxy.
    if any("egress-web" in catalog[s].networks for s in out) and PROXY_SERVICE not in out:
        out[PROXY_SERVICE] = Selection()
    return out


def render_service(
    entry: ServiceEntry, sel: Selection, stack_dir: Path, repo_root: Path, peers: list[str]
) -> dict[str, Any]:
    _validate_selection(entry, sel)
    res = entry.resources[sel.profile]
    seccomp_dir = stack_dir / "seccomp"

    env: dict[str, str] = dict(entry.env)
    for param, env_name in entry.param_env.items():
        if param in sel.params:
            env[env_name] = str(sel.params[param])
    if "egress-web" in entry.networks and entry.id != PROXY_SERVICE:
        # P1-live-smoke NO_PROXY fix (walkthrough.md::smoke-3-3): loopback must
        # NOT bypass Smokescreen, otherwise fetch_page(http://127.0.0.1/...)
        # skips the egress proxy entirely. In-stack peers stay direct (Redis
        # RESP and other non-HTTP peer traffic cannot traverse an HTTP proxy;
        # CRAWL4AI_ALLOW_INTERNAL_URLS delegation then covers only legitimate
        # peer DNS while the gateway pre-deny + Smokescreen stop loopback,
        # metadata, RFC1918/CGNAT, and encoded literals).
        no_proxy = ",".join(sorted(peers))
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[key] = PROXY_URL
        env["NO_PROXY"] = env["no_proxy"] = no_proxy

    svc: dict[str, Any] = {
        "container_name": f"{PROJECT}-{entry.id}",
        "restart": "unless-stopped",
        "user": entry.hardening.user,
        "read_only": entry.hardening.read_only,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": res.mem,
        "cpus": res.cpus,
        "pids_limit": res.pids,
        "networks": list(entry.networks),
        "labels": {"swarm.managed": "true", "swarm.service": entry.id},
    }
    if entry.image.build:
        svc["build"] = {"context": str(repo_root / entry.image.build)}
    svc["image"] = entry.image.ref(entry.id)
    if env:
        svc["environment"] = env
    if entry.secrets:
        svc["env_file"] = [str(stack_dir / "env" / f"{entry.id}.env")]
    if res.shm:
        svc["shm_size"] = res.shm
    if entry.hardening.tmpfs:
        svc["tmpfs"] = list(entry.hardening.tmpfs)
    if entry.hardening.seccomp == "chromium":
        svc["security_opt"].append(f"seccomp={seccomp_dir / 'chromium.json'}")
    if entry.volumes:
        svc["volumes"] = [f"swarm_{entry.id}_{v}" for v in entry.volumes]
    if entry.health.test:
        svc["healthcheck"] = {"test": entry.health.test, "interval": entry.health.interval}
    return svc


def render_stack(
    catalog: dict[str, ServiceEntry],
    wanted: dict[str, Selection],
    stack_dir: Path,
    repo_root: Path,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    selections = resolve(catalog, wanted)
    drafts = sorted(s for s in selections if not catalog[s].verified)
    if drafts and not allow_draft:
        raise IntentError(f"refusing unverified catalog entries: {', '.join(drafts)}")

    peers = [s for s in selections if s != PROXY_SERVICE]
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}
    for sid in sorted(selections):
        entry = catalog[sid]
        services[sid] = render_service(entry, selections[sid], stack_dir, repo_root, peers)
        for v in entry.volumes:
            volumes[f"swarm_{sid}_{v.split(':')[0]}"] = {}

    used = {n for s in services.values() for n in s["networks"]}
    networks: dict[str, Any] = {}
    for net in sorted(used):
        networks[net] = (
            {"name": f"swarm-{net}"}
            if net == "egress-out"
            else {"internal": True, "name": f"swarm-{net}"}
        )

    compose: dict[str, Any] = {"name": PROJECT, "services": services, "networks": networks}
    if volumes:
        compose["volumes"] = volumes

    proxies = {
        s for s in services if catalog[s].tier == "support" and "egress-out" in catalog[s].networks
    }
    check_compose(compose, stack_dir, proxies=proxies, seccomp_dir=stack_dir / "seccomp")
    return compose
