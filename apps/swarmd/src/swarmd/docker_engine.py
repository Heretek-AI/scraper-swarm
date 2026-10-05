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

    async def get_logs(self, service_name: str, lines: int = 100) -> str:
        """Safely fetches logs for a specific service."""
        return await self._run_command(["logs", "--tail", str(lines), service_name])
