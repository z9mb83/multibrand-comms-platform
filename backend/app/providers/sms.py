"""Provider-neutral SMS adapter for Indian DLT-compliant sending.

sender_config example:
  {
    "endpoint": "https://api.your-sms-vendor.example/v1/send",
    "dlt_entity_id": "1201XXXXXXXXXXXX",
    "header": "BRANDX",                  # DLT-registered sender header (6 chars)
    "auth_header": "Authorization",       # header name the vendor expects
    "auth_prefix": "Bearer ",
    "field_map": {"to": "to", "text": "message", "header": "sender",
                  "entity_id": "pe_id", "template_id": "template_id", "ref": "client_ref"}
  }
secret_ref -> vendor API key.

Every message must reference an approved DLT template id (template_params["dlt_template_id"]).

STATUS: NOT verified against a live SMS vendor.
"""
import httpx

from app.core.secrets import resolve_secret
from app.providers.base import HealthResult, SendRequest, SendResult

DEFAULT_MAP = {"to": "to", "text": "message", "header": "sender", "entity_id": "pe_id",
               "template_id": "template_id", "ref": "client_ref"}


def build_payload(req: SendRequest) -> dict:
    cfg = req.sender_config
    fmap = {**DEFAULT_MAP, **cfg.get("field_map", {})}
    dlt_template_id = req.template_params.get("dlt_template_id")
    if not dlt_template_id:
        raise ValueError("DLT template id is required for SMS")
    return {
        fmap["to"]: req.to_address.lstrip("+"),
        fmap["text"]: req.body or "",
        fmap["header"]: cfg.get("header", req.from_address),
        fmap["entity_id"]: cfg.get("dlt_entity_id"),
        fmap["template_id"]: dlt_template_id,
        fmap["ref"]: req.idempotency_key,
    }


class GenericSmsProvider:
    name = "sms_generic"

    def send(self, req: SendRequest) -> SendResult:
        cfg = req.sender_config
        try:
            payload = build_payload(req)
        except ValueError as exc:
            return SendResult(ok=False, error=str(exc), retryable=False)
        try:
            key = resolve_secret(req.secret_ref)
            resp = httpx.post(cfg["endpoint"], json=payload,
                              headers={cfg.get("auth_header", "Authorization"): f"{cfg.get('auth_prefix', 'Bearer ')}{key}"},
                              timeout=30)
        except Exception as exc:
            return SendResult(ok=False, error=str(exc), retryable=True)
        if resp.status_code >= 400:
            return SendResult(ok=False, error=f"sms {resp.status_code}: {resp.text[:300]}", retryable=resp.status_code >= 500)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        return SendResult(ok=True, provider_message_id=str(data.get(cfg.get("id_field", "message_id"), "")) or None)

    def check_health(self, address: str, sender_config: dict, secret_ref: str | None) -> HealthResult:
        try:
            resolve_secret(secret_ref)
            ok = bool(sender_config.get("endpoint") and sender_config.get("dlt_entity_id"))
            return HealthResult(ok, "config present" if ok else "endpoint/dlt_entity_id missing")
        except Exception as exc:
            return HealthResult(False, str(exc))
