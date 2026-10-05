"""Client for communicating with the privileged swarmd sidecar over Unix Domain Socket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class SwarmdClientError(Exception):
    pass


class SwarmdClient:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path

    async def send_intent(self, intent: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Sends an intent to swarmd via Unix domain socket and awaits response."""
        if not self.socket_path.exists():
            raise SwarmdClientError(f"swarmd socket {self.socket_path} does not exist.")

        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        except Exception as e:
            raise SwarmdClientError(f"Failed to connect to swarmd socket: {e}") from e

        try:
            message = json.dumps({"intent": intent, "payload": payload}) + "\n"
            writer.write(message.encode("utf-8"))
            await writer.drain()

            line = await reader.readline()
            if not line:
                raise SwarmdClientError("swarmd closed connection without responding.")

            response = json.loads(line.decode("utf-8"))
            if not response.get("ok"):
                error_msg = response.get("error", "Unknown swarmd error")
                raise SwarmdClientError(f"swarmd rejected intent: {error_msg}")

            return response.get("data", {})
        finally:
            writer.close()
            await writer.wait_closed()
