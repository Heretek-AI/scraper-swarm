"""Live Docker orchestration engine executing allowlisted compose operations."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("swarmd.docker")


class DockerExecutionError(Exception):
    pass


class DockerOrchestrator:
    def __init__(self, stack_dir: Path, project_name: str = "scraper-swarm"):
        self.stack_dir = stack_dir
        self.project_name = project_name
        self.compose_file = stack_dir / "docker-compose.yml"

    async def _run_command(self, args: list[str]) -> str:
        cmd = ["docker", "compose", "-p", self.project_name, "-f", str(self.compose_file)] + args
        logger.info("Executing: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.stack_dir),
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8").strip() or stdout.decode("utf-8").strip()
            raise DockerExecutionError(f"Docker command failed (exit {proc.returncode}): {err}")

        return stdout.decode("utf-8").strip()

    async def write_and_apply(self, compose_dict: dict[str, Any]) -> str:
        """Writes the validated compose dictionary to disk and triggers docker compose up."""
        self.stack_dir.mkdir(parents=True, exist_ok=True)
        # Write docker-compose.yml with 0600 permissions
        raw_yaml = yaml.dump(compose_dict, sort_keys=False)
        self.compose_file.write_text(raw_yaml)
        self.compose_file.chmod(0o600)

        # Execute compose up with remove-orphans
        return await self._run_command(["up", "-d", "--remove-orphans"])

    async def down(self) -> str:
        """Tears down all containers in the stack."""
        if not self.compose_file.exists():
            return "No compose file exists."
        return await self._run_command(["down", "--remove-orphans"])

    async def get_ps(self) -> list[dict[str, Any]]:
        """Returns live container status in JSON format."""
        if not self.compose_file.exists():
            return []
        try:
            output = await self._run_command(["ps", "--format", "json"])
            if not output:
                return []
            containers = []
            for line in output.splitlines():
                if line.strip():
                    containers.append(json.loads(line))
            return containers
        except Exception as e:
            logger.warning("Failed to get container status: %s", e)
            return []

    async def ps_all(self) -> list[dict[str, Any]]:
        """Returns ALL containers (running + stopped) for drift detection.

        Phase 02-infra-reconcile (evidence:
        file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json):
        plain ``ps`` hides exited containers, so drift between the DB
        ``running`` rows and Docker is invisible. ``ps -a`` distinguishes
        ``missing`` from ``exited``/``dead`` for the reconciler.
        """
        if not self.compose_file.exists():
            return []
        output = await self._run_command(["ps", "-a", "--format", "json"])
        if not output:
            return []
        containers = []
        for line in output.splitlines():
            if line.strip():
                containers.append(json.loads(line))
        return containers

    async def ensure_up(self) -> str:
        """Idempotent repair: recreates missing/stopped containers.

        Phase 02-infra-reconcile: the reconciler calls this after drift is
        detected. ``up -d --remove-orphans`` leaves healthy containers
        untouched and never includes secrets on the command line (they stay
        in 0600 env_files under the stack dir).
        """
        return await self._run_command(["up", "-d", "--remove-orphans"])

    async def restart(self, service: str) -> str:
        """Health-driven restart of one engine-stack service.

        Phase 02-infra-reconcile: ``up -d`` does not recycle running-but-
        ``unhealthy`` containers, so the reconciler restarts them
        explicitly. ``docker compose restart`` only ever touches services
        of this project, and the name is allowlisted to container-name
        characters (defense in depth; argv is never shelled).
        """
        import re

        if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", service):
            raise DockerExecutionError(f"Refusing to restart invalid service name: {service!r}")
        return await self._run_command(["restart", service])

    async def get_logs(self, service_name: str, lines: int = 100) -> str:
        """Safely fetches logs for a specific service."""
        return await self._run_command(["logs", "--tail", str(lines), service_name])

    def get_compose_content(self) -> str:
        """Returns the raw YAML content of docker-compose.yml if present."""
        if not self.compose_file.exists():
            return ""
        return self.compose_file.read_text(encoding="utf-8")
