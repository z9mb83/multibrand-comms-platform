"""Resolve credential references without ever storing secrets in the database.

Supported refs:
  env:NAME                                  -> os.environ[NAME]   (local dev only)
  gsm:projects/P/secrets/S/versions/V       -> Google Secret Manager
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
    raise SecretResolutionError(f"Unsupported secret ref scheme: {ref.split(':', 1)[0]}")
