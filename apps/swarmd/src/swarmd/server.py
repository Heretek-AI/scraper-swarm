"""Unix domain socket server in swarmd to process intents from panel-api."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from .catalog import ServiceEntry
from .render import Selection, render_stack

logger = logging.getLogger("swarmd.server")


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

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            if self.socket_path.exists():
                self.socket_path.unlink()

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
        elif intent == "ping":
            return {"status": "pong"}
        else:
            raise ValueError(f"Unknown intent: {intent}")
