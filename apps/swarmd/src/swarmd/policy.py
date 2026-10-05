"""Independent policy check over a *rendered* compose document.

The renderer builds hardened services, but this module does not trust it: it re-validates the
final output against an allow-list, so a renderer bug or template change cannot silently emit
an unsafe container. Used both before applying a stack and in tests.
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_NETWORKS = {"svc", "egress-web", "egress-out"}
FORBIDDEN_KEYS = (
    "privileged",
    "cap_add",
    "devices",
    "device_cgroup_rules",
    "network_mode",
    "pid",
    "ipc",
    "uts",
    "userns_mode",
    "cgroup_parent",
    "extra_hosts",
    "ports",  # publishing ports is reserved for the edge compose, never catalog services
    "expose_host",
    "sysctls",
    "volumes_from",
    "group_add",
)


class PolicyViolation(Exception):  # noqa: N818 - domain term, not an error suffix
    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def _is_named_volume(src: str) -> bool:
    return not (src.startswith(("/", ".", "~")) or "/" in src)


def check_service(
    name: str, svc: dict, stack_dir: Path, *, is_proxy: bool, seccomp_dir: Path
) -> list[str]:
    v: list[str] = []

    def bad(msg: str) -> None:
        v.append(f"{name}: {msg}")

    for key in FORBIDDEN_KEYS:
        if key in svc:
            bad(f"forbidden key '{key}'")

    if svc.get("cap_drop") != ["ALL"]:
        bad("cap_drop must be exactly ['ALL']")

    sec = svc.get("security_opt", [])
    if "no-new-privileges:true" not in sec:
        bad("missing no-new-privileges:true")
    for opt in sec:
        if opt.startswith("seccomp="):
            target = opt.removeprefix("seccomp=")
            if target == "unconfined" or Path(target).parent != seccomp_dir:
                bad(f"seccomp profile must be a file in {seccomp_dir} (got {target})")
        elif opt.startswith(("apparmor=unconfined", "label=disable", "label:disable")):
            bad(f"disallowed security_opt '{opt}'")

    user = str(svc.get("user", ""))
    uid = user.split(":")[0]
    if not uid.isdigit() or int(uid) == 0:
        bad(f"must run as an explicit non-root uid (got '{user}')")

    for limit in ("mem_limit", "cpus", "pids_limit"):
        if limit not in svc:
            bad(f"missing resource limit '{limit}'")

    nets = svc.get("networks", [])
    nets = list(nets) if not isinstance(nets, dict) else list(nets)
    for n in nets:
        if n not in ALLOWED_NETWORKS:
            bad(f"network '{n}' is not allowed")
    if "egress-out" in nets and not is_proxy:
        bad("only egress proxies may join egress-out")
    if not nets:
        bad("must join at least one network")

    for vol in svc.get("volumes", []):
        src = str(vol).split(":")[0]
        if "docker.sock" in str(vol):
            bad("must never mount the docker socket")
        elif not _is_named_volume(src):
            bad(f"bind mounts are not allowed (got '{vol}')")

    for ef in svc.get("env_file", []):
        p = Path(ef)
        if p.parent != stack_dir / "env":
            bad(f"env_file must live in {stack_dir / 'env'} (got {ef})")

    for key, value in svc.get("environment", {}).items():
        if "\n" in str(value) or "\r" in str(value):
            bad(f"environment value for {key} contains a newline")

    if svc.get("restart") not in ("unless-stopped", "on-failure", "no"):
        bad("restart policy must be set")

    return v


def check_compose(compose: dict, stack_dir: Path, *, proxies: set[str], seccomp_dir: Path) -> None:
    violations: list[str] = []
    for net, spec in compose.get("networks", {}).items():
        if net not in ALLOWED_NETWORKS:
            violations.append(f"network '{net}' is not allowed")
        elif net != "egress-out" and spec.get("internal") is not True:
            violations.append(f"network '{net}' must be internal: true (fail-closed egress)")
    for name, svc in compose.get("services", {}).items():
        violations += check_service(
            name, svc, stack_dir, is_proxy=name in proxies, seccomp_dir=seccomp_dir
        )
    if violations:
        raise PolicyViolation(violations)
