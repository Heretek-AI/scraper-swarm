"""Cryptographic vault for encrypting secrets at rest using AES-256-GCM.

The master key can be provided via environment variable (SWARM_MASTER_KEY)
or read from a file with strict 0600 permissions.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise VaultError("Master key must be exactly 32 bytes (256 bits).")
        self._key = key
        self._aesgcm = AESGCM(self._key)

    @classmethod
    def from_env(cls, env_var: str = "SWARM_MASTER_KEY") -> Vault:
        val = os.environ.get(env_var)
        if not val:
            raise VaultError(f"Environment variable {env_var} is not set.")
        try:
            key = base64.b64decode(val)
        except Exception as e:
            raise VaultError(f"Invalid base64 in {env_var}: {e}") from e
        return cls(key)

    @classmethod
    def from_file(cls, path: Path, *, auto_create: bool = False) -> Vault:
        if not path.exists():
            if auto_create:
                path.parent.mkdir(parents=True, exist_ok=True)
                key = AESGCM.generate_key(bit_length=256)
                # Create file with 0600 permissions
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(path, flags, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64encode(key))
                return cls(key)
            raise VaultError(f"Master key file {path} does not exist.")

        # Check permissions: must not be world/group readable
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise VaultError(
                f"Master key file {path} has unsafe permissions ({oct(mode)}). "
                "Must be 0600 or stricter."
            )

        content = path.read_text().strip()
        try:
            key = base64.b64decode(content)
        except Exception as e:
            raise VaultError(f"Failed to decode key from {path}: {e}") from e
        return cls(key)

    def encrypt(self, plaintext: str, associated_data: str | None = None) -> str:
        """Encrypts plaintext and returns base64-encoded nonce + ciphertext."""
        nonce = os.urandom(12)  # 96-bit nonce standard for GCM
        ad = associated_data.encode("utf-8") if associated_data else None
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), ad)
        return base64.b64encode(nonce + ciphertext).decode("utf-8")

    def decrypt(self, token: str, associated_data: str | None = None) -> str:
        """Decrypts a base64-encoded token back into plaintext."""
        try:
            data = base64.b64decode(token)
            if len(data) < 28:  # 12-byte nonce + 16-byte tag min
                raise VaultError("Token is too short.")
            nonce = data[:12]
            ciphertext = data[12:]
            ad = associated_data.encode("utf-8") if associated_data else None
            decrypted = self._aesgcm.decrypt(nonce, ciphertext, ad)
            return decrypted.decode("utf-8")
        except Exception as e:
            raise VaultError(f"Decryption failed: {e}") from e
