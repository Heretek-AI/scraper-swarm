"""Tests for panel_api vault, audit log, and swarmd IPC."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import pytest
import aiosqlite

from panel_api.vault import Vault, VaultError
from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.swarmd_client import SwarmdClient, SwarmdClientError
from swarmd.catalog import load_catalog
from swarmd.server import SwarmdServer


@pytest.fixture
def tmp_path_test(tmp_path: Path) -> Path:
    return tmp_path


def test_vault_encrypt_decrypt():
    key = os.urandom(32)
    vault = Vault(key)
    secret = "my-super-secret-key-12345"

    token = vault.encrypt(secret)
    assert token != secret

    decrypted = vault.decrypt(token)
    assert decrypted == secret


def test_vault_with_aad():
    key = os.urandom(32)
    vault = Vault(key)
    secret = "scoped-secret"

    token = vault.encrypt(secret, associated_data="user:123")
    assert vault.decrypt(token, associated_data="user:123") == secret

    with pytest.raises(VaultError):
        vault.decrypt(token, associated_data="user:456")


def test_vault_file_permissions(tmp_path: Path):
    key_file = tmp_path / "master.key"
    vault = Vault.from_file(key_file, auto_create=True)
    assert (key_file.stat().st_mode & 0o777) == 0o600

    secret = "hello"
    enc = vault.encrypt(secret)
    assert vault.decrypt(enc) == secret

    # Tamper with file mode to make it group-readable
    os.chmod(key_file, 0o644)
    with pytest.raises(VaultError, match="unsafe permissions"):
        Vault.from_file(key_file)


@pytest.mark.asyncio
async def test_audit_logger_chain(tmp_path: Path):
    db_file = tmp_path / "panel.db"
    db = Database(db_file)
    await db.connect()

    logger = AuditLogger(db.conn)
    h1 = await logger.log("admin", "login", details={"ip": "127.0.0.1"})
    h2 = await logger.log("admin", "install_service", target="searxng", details={"profile": "standard"})
    h3 = await logger.log("operator", "restart_service", target="searxng")

    assert await logger.verify_chain() is True

    # Tamper with an audit entry in DB
    await db.conn.execute("UPDATE audit_log SET action = 'tampered' WHERE id = 2")
    await db.conn.commit()

    assert await logger.verify_chain() is False
    await db.close()


@pytest.mark.asyncio
async def test_swarmd_ipc(tmp_path: Path):
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
    res = await client.send_intent("ping", {})
    assert res == {"status": "pong"}

    res_render = await client.send_intent(
        "render_stack",
        {"wanted": {"searxng": {"profile": "standard"}}},
    )
    assert "compose" in res_render
    assert "searxng" in res_render["compose"]["services"]
    assert "valkey" in res_render["compose"]["services"]

    await server.stop()
