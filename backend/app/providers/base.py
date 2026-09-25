from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SendRequest:
    channel: str
    from_address: str
    to_address: str
    body: str | None = None
    subject: str | None = None
    from_name: str | None = None
    template_name: str | None = None
    template_params: dict = field(default_factory=dict)
    idempotency_key: str = ""
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    list_unsubscribe: str | None = None
    sender_config: dict = field(default_factory=dict)
    secret_ref: str | None = None
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)  # (filename, data, mime)


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    thread_id: str | None = None
    message_ref: str | None = None  # RFC822 Message-ID for email threading
    error: str | None = None
    retryable: bool = False


@dataclass
class HealthResult:
    healthy: bool
    detail: str = ""


class Provider(Protocol):
    name: str

    def send(self, req: SendRequest) -> SendResult: ...

    def check_health(self, address: str, sender_config: dict, secret_ref: str | None) -> HealthResult: ...


class ProviderError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
