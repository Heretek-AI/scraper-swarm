"""Catalog models and loader.

The catalog is the single source of truth for what swarmd is willing to run. Entries are
strictly validated (unknown keys are rejected) so a typo can never silently weaken a service.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROFILES = ("lite", "standard", "heavy")
# egress-out is the only network with a route to the internet; only proxies may join it.
NETWORKS = ("svc", "egress-web", "egress-out")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class License(_Strict):
    spdx: str
    notice: str | None = None


class Image(_Strict):
    repo: str | None = None
    tag: str = "latest"
    digest: str | None = None
    # Build from a path under the repo (used for components with no published image).
    build: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Image:
        if bool(self.repo) == bool(self.build):
            raise ValueError("image needs exactly one of 'repo' or 'build'")
        if self.digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise ValueError("digest must look like sha256:<64 hex>")
        return self

    def ref(self, local_name: str) -> str:
        if self.build:
            return f"scraper-swarm/{local_name}:local"
        return f"{self.repo}@{self.digest}" if self.digest else f"{self.repo}:{self.tag}"


class Resources(_Strict):
    cpus: float = Field(gt=0, le=64)
    mem: str = Field(pattern=r"^\d+[mg]$")
    shm: str | None = Field(default=None, pattern=r"^\d+[mg]$")
    pids: int = Field(default=512, gt=0, le=65535)


class Health(_Strict):
    test: list[str] | None = None
    interval: str = "15s"


class Hardening(_Strict):
    # Explicit non-root uid:gid. Required: swarmd never trusts an image's default user.
    user: str = Field(pattern=r"^[1-9]\d*:\d+$")
    read_only: bool = False
    seccomp: Literal["default", "chromium"] = "default"
    tmpfs: list[str] = Field(default_factory=list)


class ServiceEntry(_Strict):
    id: str
    name: str
    tier: Literal["core", "optional", "advanced", "support"]
    license: License
    image: Image
    resources: dict[str, Resources]
    networks: list[Literal["svc", "egress-web", "egress-out"]]
    hardening: Hardening
    # False until an integration test has installed it from this entry. Draft entries are
    # refused unless the caller explicitly opts in (dev only).
    verified: bool = False
    requires: list[str] = Field(default_factory=list)
    port: int | None = Field(default=None, ge=1, le=65535)
    env: dict[str, str] = Field(default_factory=dict)
    # Secrets are delivered via a 0600 env_file under the stack dir, never inline.
    secrets: list[str] = Field(default_factory=list)
    # "name:/container/path" -> named volume swarm_<id>_<name>
    volumes: list[str] = Field(default_factory=list)
    health: Health = Field(default_factory=Health)
    params_schema: dict = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    param_env: dict[str, str] = Field(default_factory=dict)
    # May the "advanced native endpoint" toggle expose this service via the gateway auth?
    native_exposable: bool = False
    # Ticket #6: in-stack peers this engine may reach directly (NO_PROXY).
    # Default: none — every HTTP destination goes through the egress proxy
    # (Smokescreen denies internal ranges there). Declare a peer only for a
    # genuine direct need, e.g. gpt-researcher -> searxng retriever HTTP.
    # Non-HTTP peer traffic (e.g. Redis RESP) never consults proxy env vars.
    direct_peers: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise ValueError("id must match ^[a-z][a-z0-9-]{1,40}$")
        return v

    @field_validator("resources")
    @classmethod
    def _profiles(cls, v: dict[str, Resources]) -> dict[str, Resources]:
        if "standard" not in v or not set(v) <= set(PROFILES):
            raise ValueError(f"resources must include 'standard' and only use {PROFILES}")
        return v

    @model_validator(mode="after")
    def _egress_rules(self) -> ServiceEntry:
        is_proxy = self.tier == "support" and "egress-out" in self.networks
        if "egress-out" in self.networks and not is_proxy:
            raise ValueError("only support-tier proxies may join egress-out")
        if (
            self.port is None
            and any(n for n in self.networks if n == "svc")
            and self.tier != "support"
        ):
            raise ValueError("non-support services on 'svc' must declare a port")
        for p in self.param_env:
            if p not in self.params_schema.get("properties", {}):
                raise ValueError(f"param_env refers to unknown param '{p}'")
        return self


def load_catalog(directory: Path) -> dict[str, ServiceEntry]:
    entries: dict[str, ServiceEntry] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        entry = ServiceEntry.model_validate(data)
        if entry.id != path.stem:
            raise ValueError(f"{path.name}: id '{entry.id}' must match filename")
        if entry.id in entries:
            raise ValueError(f"duplicate service id {entry.id}")
        entries[entry.id] = entry
    for entry in entries.values():
        for dep in entry.requires:
            if dep not in entries:
                raise ValueError(f"{entry.id} requires unknown service '{dep}'")
        for peer in entry.direct_peers:
            if peer not in entries:
                raise ValueError(f"{entry.id} direct_peers unknown service '{peer}'")
            if peer == "egress-web":
                raise ValueError(f"{entry.id} direct_peers must never include the egress proxy")
    return entries
