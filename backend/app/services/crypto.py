"""Symmetric encryption for stored provider credentials (Fernet: AES-128-CBC + HMAC-SHA256).

Key: ENCRYPTION_KEY if set (recommended; keep it stable), otherwise derived from the server secret.
If the key changes, stored credentials can no longer be decrypted and mailboxes must be reconnected.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    s = get_settings()
    material = (s.encryption_key or s.jwt_secret).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(b"comms-credential-key|" + material).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential can't be decrypted (encryption key changed); reconnect the mailbox") from exc
