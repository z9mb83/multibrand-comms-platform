"""Functional mock providers for local development and tests.

Behaves like an idempotent provider: sending twice with the same idempotency key
returns the original message id and does not record a second delivery.
"""
import threading
import uuid
from dataclasses import dataclass, field

from app.providers.base import HealthResult, SendRequest, SendResult


@dataclass
class MockOutbox:
    sent: list[SendRequest] = field(default_factory=list)
    by_key: dict[str, str] = field(default_factory=dict)
    fail_next: int = 0
    fail_retryable: bool = True
    fail_addresses: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self.lock:
            self.sent.clear()
            self.by_key.clear()
            self.fail_next = 0
            self.fail_retryable = True
            self.fail_addresses.clear()


OUTBOX = MockOutbox()


class MockProvider:
    def __init__(self, channel: str):
        self.name = f"mock_{channel}"
        self.channel = channel

    def send(self, req: SendRequest) -> SendResult:
        with OUTBOX.lock:
            if req.idempotency_key and req.idempotency_key in OUTBOX.by_key:
                return SendResult(ok=True, provider_message_id=OUTBOX.by_key[req.idempotency_key], thread_id=req.thread_id)
            if OUTBOX.fail_next > 0:
                OUTBOX.fail_next -= 1
                return SendResult(ok=False, error="mock transient failure", retryable=OUTBOX.fail_retryable)
            if req.to_address in OUTBOX.fail_addresses:
                return SendResult(ok=False, error="mock permanent failure", retryable=False)
            msg_id = f"{self.channel}-{uuid.uuid4().hex[:16]}"
            thread_id = req.thread_id or f"thr-{uuid.uuid4().hex[:12]}"
            OUTBOX.sent.append(req)
            if req.idempotency_key:
                OUTBOX.by_key[req.idempotency_key] = msg_id
            return SendResult(ok=True, provider_message_id=msg_id, thread_id=thread_id, message_ref=f"<{msg_id}@mock.local>")

    def check_health(self, address: str, sender_config: dict, secret_ref: str | None) -> HealthResult:
        return HealthResult(healthy=True, detail="mock provider")
