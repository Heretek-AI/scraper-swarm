"""Unix domain socket server in swarmd to process intents from panel-api.

Phase 02-infra-reconcile (evidence:
file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json,
file:///home/john/Projects/scraper-swarm/deploy/docker-compose.control.yml,
file:///home/john/Projects/scraper-swarm/apps/swarmd/src/swarmd/server.py):
DB-vs-Docker drift (DB ``running`` vs 0 containers) is repaired by a
background reconciler loop plus ``reconcile_now`` / ``get_reconcile_status``
intents. Desired state (``wanted.json``) persists on the stack volume so
repair survives swarmd restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from .catalog import ServiceEntry
from .docker_engine import DockerOrchestrator
from .reconciler import (
    Reconciler,
    clear_wanted,
    load_last_report,
    load_wanted,
    save_wanted,
)
from .render import Selection, render_stack

logger = logging.getLogger("swarmd.server")


def _reconcile_interval_s() -> int:
    try:
        return max(0, int(os.environ.get("SWARM_RECONCILE_INTERVAL_S", "60")))
    except ValueError:
        return 60


def _reconcile_boot_delay_s() -> float:
    try:
        return max(0.0, float(os.environ.get("SWARM_RECONCILE_BOOT_DELAY_S", "15")))
    except ValueError:
        return 15.0


class SwarmdServer:
    def __init__(
        self,
        socket_path: Path,
        catalog: dict[str, ServiceEntry],
        stack_dir: Path,
        repo_root: Path,
        *,
        allow_draft: bool = False,
    ):
        self.socket_path = socket_path
        self.catalog = catalog
        self.stack_dir = stack_dir
        self.repo_root = repo_root
        self.allow_draft = allow_draft
        self.orchestrator = DockerOrchestrator(stack_dir=stack_dir)
        self.reconciler = Reconciler(
            stack_dir=stack_dir,
            orchestrator=self.orchestrator,
            interval_s=_reconcile_interval_s(),
        )
        self._reconcile_lock = asyncio.Lock()
        self._reconcile_task: asyncio.Task[None] | None = None
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.socket_path)
        )
        # Strict permissions on the IPC socket: 0660 or 0600
        os.chmod(self.socket_path, 0o660)
        logger.info("swarmd socket server listening on %s", self.socket_path)
        # Phase 02-infra-reconcile: background drift repair. The first pass
        # runs after a short boot delay so a daemon/host restart recovers
        # the engine stack without operator action.
        if self.reconciler.interval_s > 0:
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())
            logger.info("reconcile loop started (interval %ss)", self.reconciler.interval_s)
        else:
            logger.info("reconcile loop disabled (SWARM_RECONCILE_INTERVAL_S=0)")

    async def stop(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            if self.socket_path.exists():
                self.socket_path.unlink()

    async def _reconcile_loop(self) -> None:
        await asyncio.sleep(_reconcile_boot_delay_s())
        while True:
            try:
                async with self._reconcile_lock:
                    report = await self.reconciler.run_pass()
                if report.get("drift"):
                    logger.warning(
                        "reconcile pass: drift=%s action=%s degraded=%s",
                        report.get("drift"),
                        report.get("action"),
                        report.get("degraded"),
                    )
                else:
                    logger.info("reconcile pass: no drift")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reconcile loop pass failed")
            await asyncio.sleep(self.reconciler.interval_s or 60)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return

            req = json.loads(line.decode("utf-8"))
            intent = req.get("intent")
            payload = req.get("payload", {})

            result = await self.process_intent(intent, payload)
            resp = json.dumps({"ok": True, "data": result}) + "\n"
            writer.write(resp.encode("utf-8"))
            await writer.drain()
        except Exception as e:
            logger.exception("Error processing intent")
            err_resp = json.dumps({"ok": False, "error": str(e)}) + "\n"
            writer.write(err_resp.encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def process_intent(self, intent: str, payload: dict[str, Any]) -> dict[str, Any]:
        if intent == "render_stack":
            wanted_raw = payload.get("wanted", {})
            wanted = {
                sid: Selection(
                    profile=data.get("profile", "standard"),
                    params=data.get("params", {}),
                )
                for sid, data in wanted_raw.items()
            }
            compose = render_stack(
                self.catalog,
                wanted,
                self.stack_dir,
                self.repo_root,
                allow_draft=self.allow_draft,
            )
            return {"compose": compose}
        elif intent == "apply_stack":
            wanted_raw = payload.get("wanted", {})
            secrets_payload = payload.get("secrets", {})
            wanted = {
                sid: Selection(
                    profile=data.get("profile", "standard"),
                    params=data.get("params", {}),
                )
                for sid, data in wanted_raw.items()
            }

            # 1. Ensure seccomp profile directory and chromium.json exist
            seccomp_dir = self.stack_dir / "seccomp"
            seccomp_dir.mkdir(parents=True, exist_ok=True)
            chromium_seccomp = seccomp_dir / "chromium.json"
            if not chromium_seccomp.exists():
                chromium_seccomp.write_text(
                    json.dumps(
                        {
                            "defaultAction": "SCMP_ACT_ALLOW",
                            "architectures": [
                                "SCMP_ARCH_X86_64",
                                "SCMP_ARCH_X86",
                                "SCMP_ARCH_X32",
                                "SCMP_ARCH_ARM",
                                "SCMP_ARCH_AARCH64",
                            ],
                            "syscalls": [],
                        },
                        indent=2,
                    )
                    + "\n"
                )
                chromium_seccomp.chmod(0o644)

            # 2. Ensure env directory and env files for services with secrets exist
            env_dir = self.stack_dir / "env"
            env_dir.mkdir(parents=True, exist_ok=True)
            for sid, entry in self.catalog.items():
                if entry.secrets:
                    env_file = env_dir / f"{sid}.env"
                    service_secrets = secrets_payload.get(sid, {})
                    lines = []
                    for s_name in entry.secrets:
                        val = service_secrets.get(s_name)
                        if not val:
                            if any(k in s_name for k in ("SECRET", "TOKEN", "KEY")):
                                val = secrets.token_hex(32)
                            else:
                                val = ""
                        lines.append(f"{s_name}={val}\n")
                    env_file.write_text("".join(lines))
                    env_file.chmod(0o600)

            compose = render_stack(
                self.catalog,
                wanted,
                self.stack_dir,
                self.repo_root,
                allow_draft=self.allow_draft,
            )
            output = await self.orchestrator.write_and_apply(compose)
            # Phase 02-infra-reconcile: persist desired state so the
            # background loop (and post-restart boot pass) can repair drift.
            save_wanted(
                self.stack_dir,
                {
                    sid: {"profile": sel.profile, "params": sel.params}
                    for sid, sel in wanted.items()
                },
            )
            return {"compose": compose, "output": output}
        elif intent == "down_stack":
            output = await self.orchestrator.down()
            # Phase 02-infra-reconcile: an explicit teardown clears desired
            # state so the loop does not resurrect what the operator stopped.
            clear_wanted(self.stack_dir)
            return {"output": output}
        elif intent == "get_ps":
            containers = await self.orchestrator.get_ps()
            return {"containers": containers}
        elif intent == "get_ps_all":
            # Phase 02-infra-reconcile: running + stopped rows so callers can
            # tell `exited` from `missing` when explaining drift.
            containers = await self.orchestrator.ps_all()
            return {"containers": containers}
        elif intent == "reconcile_now":
            # Phase 02-infra-reconcile: on-demand drift repair (serialized
            # with the background loop; may take ~1-2 min on image pulls).
            async with self._reconcile_lock:
                report = await self.reconciler.run_pass()
            return {"reconcile": report}
        elif intent == "get_reconcile_status":
            # Phase 02-infra-reconcile: fast read-only status for
            # GET /services/status (no docker calls, no repair).
            last = load_last_report(self.stack_dir)
            wanted_doc = load_wanted(self.stack_dir)
            return {
                "reconcile": {
                    "enabled": wanted_doc is not None and self.reconciler.interval_s > 0,
                    "interval_s": self.reconciler.interval_s,
                    "wanted_updated_at": (wanted_doc or {}).get("updated_at"),
                    "last": last,
                }
            }
        elif intent == "restart_service":
            # Phase 02-infra-reconcile: health-driven single-service restart
            # used by the reconciler and POST /services/reconcile flows.
            service = payload.get("service")
            if not service:
                raise ValueError("Missing 'service' in payload for restart_service")
            output = await self.orchestrator.restart(service)
            return {"output": output}
        elif intent == "get_logs":
            service = payload.get("service")
            if not service:
                raise ValueError("Missing 'service' in payload for get_logs")
            logs = await self.orchestrator.get_logs(service, lines=payload.get("lines", 100))
            return {"logs": logs}
        elif intent == "get_compose":
            content = self.orchestrator.get_compose_content()
            return {"compose_yaml": content}
        elif intent == "ping":
            return {"status": "pong"}
        else:
            raise ValueError(f"Unknown intent: {intent}")
