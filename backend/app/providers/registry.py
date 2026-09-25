from app.core.config import get_settings
from app.providers.base import Provider
from app.providers.mock import MockProvider


def get_provider(channel: str, provider_name: str) -> Provider:
    """PROVIDER_MODE: "mock" = everything is simulated; "live"/"auto" = accounts use their real provider
    (accounts whose provider is "mock", such as demo senders, always stay simulated)."""
    if get_settings().provider_mode == "mock" or provider_name == "mock":
        return MockProvider(channel)
    if channel == "email" and provider_name == "gmail":
        from app.providers.gmail import GmailProvider
        return GmailProvider()
    if channel == "whatsapp" and provider_name == "meta_cloud":
        from app.providers.whatsapp_cloud import WhatsAppCloudProvider
        return WhatsAppCloudProvider()
    if channel == "sms":
        from app.providers.sms import GenericSmsProvider
        return GenericSmsProvider()
    raise ValueError(f"No provider for channel={channel} provider={provider_name}")
