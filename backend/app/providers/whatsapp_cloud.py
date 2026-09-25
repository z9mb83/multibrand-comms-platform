"""Meta WhatsApp Cloud API adapter.

sender_config: {"phone_number_id": "...", "waba_id": "...", "api_version": "v20.0"}
secret_ref -> permanent system-user access token.

STATUS: implemented against Meta's documented Cloud API but NOT verified with live
credentials. Marketing/notification messages outside the 24h window must use
approved templates.
"""
import hashlib
import hmac

import httpx

from app.core.secrets import resolve_secret
from app.providers.base import HealthResult, SendRequest, SendResult

GRAPH = "https://graph.facebook.com"


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def build_payload(req: SendRequest) -> dict:
    to = req.to_address.lstrip("+")
    if req.template_name:
        components = []
        params = req.template_params.get("body", [])
        if params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]})
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": req.template_name,
                "language": {"code": req.template_params.get("language", "en")},
                "components": components,
            },
        }
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": req.body or ""}}


class WhatsAppCloudProvider:
    name = "meta_cloud"

    def send(self, req: SendRequest) -> SendResult:
        cfg = req.sender_config
        version = cfg.get("api_version", "v20.0")
        try:
            token = resolve_secret(req.secret_ref)
            resp = httpx.post(
                f"{GRAPH}/{version}/{cfg['phone_number_id']}/messages",
                json=build_payload(req),
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except Exception as exc:
            return SendResult(ok=False, error=str(exc), retryable=True)
        if resp.status_code >= 400:
            return SendResult(ok=False, error=f"whatsapp {resp.status_code}: {resp.text[:300]}",
                              retryable=resp.status_code in (429, 500, 502, 503))
        msgs = resp.json().get("messages", [])
        return SendResult(ok=True, provider_message_id=msgs[0]["id"] if msgs else None)

    def check_health(self, address: str, sender_config: dict, secret_ref: str | None) -> HealthResult:
        try:
            token = resolve_secret(secret_ref)
            version = sender_config.get("api_version", "v20.0")
            resp = httpx.get(f"{GRAPH}/{version}/{sender_config['phone_number_id']}",
                             params={"fields": "display_phone_number,quality_rating,messaging_limit_tier"},
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)
            return HealthResult(resp.status_code == 200, resp.text[:200])
        except Exception as exc:
            return HealthResult(False, str(exc))
