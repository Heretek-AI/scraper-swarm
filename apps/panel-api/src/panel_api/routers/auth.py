"""Auth router supporting bootstrap token, session cookies, TOTP 2FA, and passkeys."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Literal

import pyotp
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.vault import Vault
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

BOOTSTRAP_STATE_KEY = "bootstrap_token"
SETUP_COMPLETED_KEY = "setup_completed"
SESSION_COOKIE_NAME = "swarm_session"

# In-memory session store: session_token -> {"user_id": ..., "role": ..., "username": ...}
SESSIONS: dict[str, dict[str, str]] = {}


class BootstrapInitRequest(BaseModel):
    token: str
    admin_username: str = Field(min_length=3, max_length=32)
    # Generates a TOTP secret; returns provisioning URI and backup codes


class SetupAdminResponse(BaseModel):
    totp_secret: str
    totp_uri: str
    message: str


class TOTPVerifyRequest(BaseModel):
    username: str
    code: str


class SessionInfo(BaseModel):
    user_id: str
    username: str
    role: Literal["admin", "operator", "viewer"]


def get_db(response: Response) -> Database:
    # Set standard security headers on all auth responses
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
        "frame-ancestors 'none'; object-src 'none';"
    )
    # Inject via app state in real runtime
    from panel_api.main import app_state
    return app_state.db


def get_vault() -> Vault:
    from panel_api.main import app_state
    return app_state.vault


async def get_current_user(
    swarm_session: Annotated[str | None, Cookie()] = None,
) -> SessionInfo:
    if not swarm_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    if swarm_session in SESSIONS:
        data = SESSIONS[swarm_session]
        return SessionInfo(
            user_id=data["user_id"],
            username=data["username"],
            role=data["role"],  # type: ignore
        )

    # Check persistent database
    from panel_api.main import app_state
    if app_state and app_state.db and app_state.db.conn:
        try:
            async with app_state.db.conn.execute(
                "SELECT user_id, username, role FROM sessions WHERE token = ?",
                (swarm_session,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    SESSIONS[swarm_session] = {
                        "user_id": row["user_id"],
                        "username": row["username"],
                        "role": row["role"],
                    }
                    return SessionInfo(
                        user_id=row["user_id"],
                        username=row["username"],
                        role=row["role"],  # type: ignore
                    )
        except Exception as e:
            logger.debug("Database session lookup error: %s", e)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def require_role(*allowed_roles: str):
    async def _role_checker(user: SessionInfo = Depends(get_current_user)) -> SessionInfo:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Role '{user.role}' not permitted.",
            )
        return user
    return _role_checker


@router.get("/status")
async def get_auth_status(db: Database = Depends(get_db)):
    async with db.conn.execute(
        "SELECT value FROM system_state WHERE key = ?", (SETUP_COMPLETED_KEY,)
    ) as cur:
        row = await cur.fetchone()
        is_completed = bool(row and row["value"] == "true")
    return {"setup_completed": is_completed}


@router.post("/bootstrap-init", response_model=SetupAdminResponse)
async def bootstrap_init(
    req: BootstrapInitRequest,
    db: Database = Depends(get_db),
    vault: Vault = Depends(get_vault),
):
    """Initializes the admin account using the one-time bootstrap token generated at install."""
    async with db.conn.execute(
        "SELECT value FROM system_state WHERE key = ?", (SETUP_COMPLETED_KEY,)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(status_code=400, detail="Setup has already been completed.")

    async with db.conn.execute(
        "SELECT value FROM system_state WHERE key = ?", (BOOTSTRAP_STATE_KEY,)
    ) as cur:
        row = await cur.fetchone()
        if not row or not secrets.compare_digest(row["value"], req.token):
            raise HTTPException(status_code=403, detail="Invalid bootstrap token.")

    # Generate TOTP secret for mandatory admin 2FA
    totp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(totp_secret)
    totp_uri = totp.provisioning_uri(name=req.admin_username, issuer_name="Scraper Swarm")

    user_id = secrets.token_hex(16)
    encrypted_secret = vault.encrypt(totp_secret, associated_data=f"totp:{user_id}")

    await db.conn.execute(
        """
        INSERT INTO users (id, username, role, totp_secret, totp_enabled)
        VALUES (?, ?, 'admin', ?, 0)
        """,
        (user_id, req.admin_username, encrypted_secret),
    )
    await db.conn.commit()

    audit_logger = AuditLogger(db.conn)
    await audit_logger.log(
        actor="bootstrap",
        action="admin_initialized",
        target=req.admin_username,
        details={"user_id": user_id},
    )

    return SetupAdminResponse(
        totp_secret=totp_secret,
        totp_uri=totp_uri,
        message=(
            "Scan QR code with your authenticator app, "
            "then verify with /auth/verify-totp to complete setup."
        ),
    )


@router.post("/verify-totp")
async def verify_totp(
    req: TOTPVerifyRequest,
    response: Response,
    db: Database = Depends(get_db),
    vault: Vault = Depends(get_vault),
):
    """Verifies TOTP and establishes an authenticated session cookie."""
    async with db.conn.execute(
        "SELECT id, username, role, totp_secret, totp_enabled FROM users WHERE username = ?",
        (req.username,),
    ) as cur:
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid username or code.")

    user_id = row["id"]
    totp_secret = vault.decrypt(row["totp_secret"], associated_data=f"totp:{user_id}")
    totp = pyotp.TOTP(totp_secret)

    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid TOTP code.")

    # Enable TOTP and mark setup complete if not already enabled
    if not row["totp_enabled"]:
        await db.conn.execute(
            "UPDATE users SET totp_enabled = 1 WHERE id = ?", (user_id,)
        )
        await db.conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, 'true')",
            (SETUP_COMPLETED_KEY,),
        )
        # Delete one-time bootstrap token after successful activation
        await db.conn.execute("DELETE FROM system_state WHERE key = ?", (BOOTSTRAP_STATE_KEY,))
        await db.conn.commit()

    # Generate secure random session token
    session_token = secrets.token_urlsafe(32)
    SESSIONS[session_token] = {
        "user_id": user_id,
        "username": row["username"],
        "role": row["role"],
    }

    await db.conn.execute(
        "INSERT OR REPLACE INTO sessions (token, user_id, username, role) VALUES (?, ?, ?, ?)",
        (session_token, user_id, row["username"], row["role"]),
    )
    await db.conn.commit()

    # Strict HttpOnly, SameSite=Strict session cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=True,
        max_age=86400,  # 24h session
    )

    logger = AuditLogger(db.conn)
    await logger.log(actor=row["username"], action="login_success", target=user_id)

    return {"ok": True, "username": row["username"], "role": row["role"]}


@router.post("/logout")
async def logout(
    response: Response,
    swarm_session: Annotated[str | None, Cookie()] = None,
    db: Database = Depends(get_db),
):
    if swarm_session:
        if swarm_session in SESSIONS:
            del SESSIONS[swarm_session]
        try:
            await db.conn.execute("DELETE FROM sessions WHERE token = ?", (swarm_session,))
            await db.conn.commit()
        except Exception as e:
            logger.debug("Database session deletion failed: %s", e)
    response.delete_cookie(key=SESSION_COOKIE_NAME, httponly=True, samesite="strict", secure=True)
    return {"ok": True}


@router.get("/me", response_model=SessionInfo)
async def get_me(user: SessionInfo = Depends(get_current_user)):
    return user
