"""Offline tests of provider request construction. These do NOT prove the live integrations work;
they must still be verified with real credentials (see docs/provider-verification.md)."""
import hashlib
import hmac

from app.providers.base import SendRequest
from app.providers.gmail import build_mime
from app.providers.sms import build_payload as sms_payload
from app.providers.whatsapp_cloud import build_payload as wa_payload
from app.providers.whatsapp_cloud import verify_signature


def test_gmail_mime_threading_and_unsubscribe_headers():
    req = SendRequest(channel="email", from_address="a@brand.test", from_name="Brand", to_address="c@x.test",
                      subject="Re: Offer", body="Hi", idempotency_key="k1", in_reply_to="<orig@brand.test>",
                      list_unsubscribe="<mailto:a@brand.test?subject=unsubscribe>")
    mime, mid = build_mime(req)
    assert mime["In-Reply-To"] == "<orig@brand.test>" and mime["References"] == "<orig@brand.test>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mime["Message-ID"] == mid and mid.endswith("@brand.test>")


def test_whatsapp_template_payload():
    p = wa_payload(SendRequest(channel="whatsapp", from_address="+91", to_address="+919800000000",
                               template_name="daily_report", template_params={"body": ["x", 2], "language": "en"}))
    assert p["to"] == "919800000000" and p["type"] == "template"
    assert p["template"]["components"][0]["parameters"][1] == {"type": "text", "text": "2"}


def test_whatsapp_signature():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, sig)
    assert not verify_signature("secret", body, "sha256=bad")


def test_sms_requires_dlt_template_and_maps_fields():
    req = SendRequest(channel="sms", from_address="BRANDX", to_address="+919800000000", body="Hi",
                      idempotency_key="k", template_params={"dlt_template_id": "1107"},
                      sender_config={"header": "BRANDX", "dlt_entity_id": "1201", "field_map": {"text": "msg"}})
    p = sms_payload(req)
    assert p == {"to": "919800000000", "msg": "Hi", "sender": "BRANDX", "pe_id": "1201", "template_id": "1107",
                 "client_ref": "k"}
    req.template_params = {}
    import pytest
    with pytest.raises(ValueError):
        sms_payload(req)


def test_platform_config_normalises_db_url_and_derives_secrets(monkeypatch):
    from app.core.config import LOCAL_JWT, Settings
    monkeypatch.setenv("VERCEL", "1")
    url = "postgres://u:p@host/db?sslmode=require"
    s = Settings(app_env="local", database_url=url, jwt_secret=LOCAL_JWT)
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"
    assert s.app_env == "production" and s.jwt_secret != LOCAL_JWT and len(s.jwt_secret) == 64
    assert s.internal_token != s.jwt_secret
    assert Settings(app_env="local", database_url=url).jwt_secret == s.jwt_secret  # stable per database
    assert Settings(app_env="local", database_url="postgres://u:p@other/db").jwt_secret != s.jwt_secret


def test_placeholder_env_values_are_treated_as_unset(monkeypatch):
    from app.core.config import LOCAL_JWT, Settings
    monkeypatch.setenv("VERCEL", "1")
    s = Settings(app_env="", provider_mode="", task_backend="", database_url="postgres://u:p@h/db",
                 jwt_secret="replace-with-a-long-random-string-at-least-32-chars", internal_token="replace-with-a-random-token")
    assert s.app_env == "production" and s.provider_mode == "mock" and s.task_backend == "inline"
    assert s.jwt_secret not in (LOCAL_JWT, "replace-with-a-long-random-string-at-least-32-chars") and len(s.jwt_secret) == 64
    assert not s.internal_token.startswith("replace")
