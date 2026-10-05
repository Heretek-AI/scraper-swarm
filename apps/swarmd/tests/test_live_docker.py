"""Tests for live Docker orchestration in swarmd using real local docker."""

from __future__ import annotations

from pathlib import Path

import pytest
from panel_api.swarmd_client import SwarmdClient
from swarmd.catalog import load_catalog
from swarmd.server import SwarmdServer


@pytest.mark.asyncio
async def test_live_docker_orchestration(tmp_path: Path):
    socket_path = tmp_path / "swarmd.sock"
    repo_root = Path(__file__).resolve().parents[3]
    catalog = load_catalog(repo_root / "catalog" / "services")
    stack_dir = tmp_path / "stack"

    server = SwarmdServer(
        socket_path=socket_path,
        catalog=catalog,
        stack_dir=stack_dir,
        repo_root=repo_root,
        allow_draft=True,
    )
    await server.start()

    client = SwarmdClient(socket_path)

    # 1. Apply stack with valkey (lightweight container)
    apply_res = await client.send_intent(
        "apply_stack",
        {"wanted": {"valkey": {"profile": "standard"}}},
    )
    assert apply_res is not None
    assert "compose" in apply_res
    assert (stack_dir / "docker-compose.yml").exists()

    # 2. Query get_ps
    ps_res = await client.send_intent("get_ps", {})
    containers = ps_res.get("containers", [])
    # Valkey container should be tracked
    assert any("valkey" in c.get("Service", "") or "valkey" in c.get("Name", "") for c in containers)

    # 3. Teardown stack via down_stack
    down_res = await client.send_intent("down_stack", {})
    assert down_res is not None

    await server.stop()
