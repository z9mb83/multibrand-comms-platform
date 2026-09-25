"""Resolve credential references without ever storing secrets in the database.

Supported refs:
  env:NAME                                  -> os.environ[NAME]   (local dev only)
  gsm:projects/P/secrets/S/versions/V       -> Google Secret Manager
  db:<sender_account_id>                    -> encrypted credential stored in the database (Google-connected
                                               mailboxes); returned as the JSON the Gmail adapter expects
"""
import os


class SecretResolutionError(RuntimeError):
    pass


def resolve_secret(ref: str | None) -> str:
    if not ref:
        raise SecretResolutionError("No secret reference configured")
    if ref.startswith("env:"):
        value = os.environ.get(ref[4:])
        if value is None:
            raise SecretResolutionError(f"Environment variable {ref[4:]} not set")
        return value
    if ref.startswith("gsm:"):
        try:
            from google.cloud import secretmanager  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise SecretResolutionError("google-cloud-secret-manager not installed") from exc
        client = secretmanager.SecretManagerServiceClient()
        resp = client.access_secret_version(name=ref[4:])
        return resp.payload.data.decode()
    if ref.startswith("db:"):
        import json

        from sqlalchemy import select

        from app.core.config import get_settings
        from app.core.db import session_factory
        from app.models import SenderCredential
        from app.services.crypto import decrypt
        with session_factory()() as db:
            cred = db.scalars(select(SenderCredential).where(SenderCredential.sender_account_id == int(ref[3:]))).first()
        if cred is None:
            raise SecretResolutionError("Mailbox is not connected")
        s = get_settings()
        data = json.loads(decrypt(cred.ciphertext))
        return json.dumps({"client_id": s.google_client_id, "client_secret": s.google_client_secret, **data})
    raise SecretResolutionError(f"Unsupported secret ref scheme: {ref.split(':', 1)[0]}")
