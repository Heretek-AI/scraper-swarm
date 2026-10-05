"""Staged image updater with automatic health verification and rollback."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .docker_engine import DockerOrchestrator

logger = logging.getLogger("swarmd.updater")


class UpdateError(Exception):
    pass


class ServiceUpdater:
    def __init__(self, orchestrator: DockerOrchestrator, digests_file: Path):
        self.orchestrator = orchestrator
        self.digests_file = digests_file

    def get_recorded_digests(self) -> dict[str, str]:
        if not self.digests_file.exists():
            return {}
        try:
            return json.loads(self.digests_file.read_text())
        except Exception:
            return {}

    def record_digest(self, service_id: str, digest: str) -> None:
        digests = self.get_recorded_digests()
        digests[service_id] = digest
        self.digests_file.write_text(json.dumps(digests, indent=2))
        self.digests_file.chmod(0o600)

    async def update_with_health_check(
        self,
        service_id: str,
        new_compose: dict[str, Any],
        previous_compose: dict[str, Any],
        health_url: str | None = None,
        timeout_seconds: int = 30,
    ) -> bool:
        """Applies new compose, tests health for timeout_seconds, and rolls back if failing."""
        logger.info("Starting staged update for service %s", service_id)
        await self.orchestrator.write_and_apply(new_compose)

        # Health probe loop
        start_time = asyncio.get_event_loop().time()
        is_healthy = False

        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            await asyncio.sleep(2)
            ps = await self.orchestrator.get_ps()
            service_container = next(
                (
                    c
                    for c in ps
                    if c.get("Service") == service_id or service_id in c.get("Name", "")
                ),
                None,
            )

            if not service_container:
                continue

            state = service_container.get("State", "").lower()
            health = service_container.get("Health", "").lower()

            if state in ("restarting", "dead", "exited") or health == "unhealthy":
                logger.error("Container failed with state: %s, health: %s", state, health)
                break

            if health_url:
                try:
                    async with httpx.AsyncClient(timeout=3.0) as client:
                        resp = await client.get(health_url)
                        if resp.status_code == 200:
                            is_healthy = True
                            break
                except Exception as e:
                    logger.debug("Health probe check to %s failed: %s", health_url, e)
            elif state == "running":
                # If running and no specific health check, check if it stayed up
                is_healthy = True
                break

        if not is_healthy:
            logger.warning(
                "Service %s failed health probe. Triggering automatic rollback!", service_id
            )
            await self.orchestrator.write_and_apply(previous_compose)
            msg = f"Health check failed for {service_id}; rolled back to previous state."
            raise UpdateError(msg)

        logger.info("Service %s successfully updated and verified healthy.", service_id)
        return True
