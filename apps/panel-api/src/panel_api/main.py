"""FastAPI application entrypoint for Scraper Swarm Web Panel."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from panel_api.db import Database
from panel_api.routers import agents, auth, security, services
from panel_api.swarmd_client import SwarmdClient
from panel_api.vault import Vault
from swarmd.catalog import ServiceEntry, load_catalog


@dataclass
class AppState:
    db: Database
    vault: Vault
    swarmd: SwarmdClient
    catalog: dict[str, ServiceEntry]


app_state: AppState = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_state

    base_dir = Path(os.environ.get("SWARM_DATA_DIR", "/var/lib/scraper-swarm"))
    repo_root = Path(os.environ.get("SWARM_REPO_ROOT", Path(__file__).resolve().parents[4]))

    db_path = base_dir / "panel.db"
    key_path = base_dir / "master.key"
    socket_path = base_dir / "swarmd.sock"

    db = Database(db_path)
    await db.connect()

    env_boot_token = os.environ.get("SWARM_BOOTSTRAP_TOKEN")
    if env_boot_token:
        await db.conn.execute(
            "INSERT OR IGNORE INTO system_state (key, value) VALUES (?, ?)",
            ("bootstrap_token", env_boot_token),
        )
        await db.conn.commit()

    vault = Vault.from_file(key_path, auto_create=True)
    swarmd = SwarmdClient(socket_path)
    catalog = load_catalog(repo_root / "catalog" / "services")

    app_state = AppState(
        db=db,
        vault=vault,
        swarmd=swarmd,
        catalog=catalog,
    )

    yield

    await db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Scraper Swarm Panel API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(services.router)
    app.include_router(agents.router)
    app.include_router(security.router)

    return app


app = create_app()
