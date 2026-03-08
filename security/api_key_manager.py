"""
API Key Manager — encrypted storage for LLM provider API keys.

README §4.2: Secure API key management using OS keyring with
Fernet encryption fallback.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.security.api_key_manager")

_KEYS_FILE = Path(os.path.expanduser("~")) / ".inkoctobot" / "keys.enc"


class APIKeyManager:
    """Manage encrypted API keys."""

    def __init__(self, keys_file: str | Path | None = None):
        self._keys_file = Path(keys_file) if keys_file else _KEYS_FILE
        self._fernet = None
        self._cache: dict[str, str] = {}

    def _ensure_fernet(self) -> Any:
        if self._fernet is not None:
            return self._fernet
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            logger.warning("cryptography not installed, keys stored in plaintext")
            return None
        key_path = self._keys_file.parent / ".fkey"
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            os.chmod(str(key_path), 0o600)
        self._fernet = Fernet(key)
        return self._fernet

    def _load(self) -> dict[str, str]:
        if not self._keys_file.exists():
            return {}
        raw = self._keys_file.read_bytes()
        fernet = self._ensure_fernet()
        if fernet:
            try:
                decrypted = fernet.decrypt(raw)
                return json.loads(decrypted)
            except Exception:
                logger.error("Failed to decrypt keys file")
                return {}
        return json.loads(raw)

    def _save(self, keys: dict[str, str]) -> None:
        self._keys_file.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(keys, ensure_ascii=False).encode()
        fernet = self._ensure_fernet()
        if fernet:
            data = fernet.encrypt(data)
        self._keys_file.write_bytes(data)
        os.chmod(str(self._keys_file), 0o600)

    def set_key(self, provider: str, api_key: str) -> None:
        """Store an API key for a provider."""
        keys = self._load()
        keys[provider] = api_key
        self._save(keys)
        self._cache[provider] = api_key
        logger.info("API key stored for %s", provider)

    def get_key(self, provider: str) -> str | None:
        """Retrieve an API key for a provider."""
        if provider in self._cache:
            return self._cache[provider]
        # Try OS environment first
        env_key = os.environ.get(f"INKOCTOBOT_{provider.upper()}_API_KEY")
        if env_key:
            return env_key
        # Try keyring
        try:
            import keyring
            kr_key = keyring.get_password("inkoctobot", provider)
            if kr_key:
                self._cache[provider] = kr_key
                return kr_key
        except ImportError:
            pass
        # Fernet-encrypted file
        keys = self._load()
        key = keys.get(provider)
        if key:
            self._cache[provider] = key
        return key

    def delete_key(self, provider: str) -> bool:
        """Delete an API key."""
        keys = self._load()
        if provider in keys:
            del keys[provider]
            self._save(keys)
            self._cache.pop(provider, None)
            return True
        return False

    def list_providers(self) -> list[str]:
        """List providers with stored keys."""
        return list(self._load().keys())

    def has_key(self, provider: str) -> bool:
        return self.get_key(provider) is not None
