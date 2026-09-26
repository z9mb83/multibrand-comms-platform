from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class SetupIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1)
    password: str = Field(min_length=10)
    create_brands: bool = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginOut(BaseModel):
    """Either a token, or (when the user opted into email codes) a challenge to complete."""
    access_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    challenge_id: str | None = None
    email_hint: str | None = None


class OtpVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=6)


class EmailStartIn(BaseModel):
    email: EmailStr


class SignupVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=6)


class SignupCompleteIn(BaseModel):
    signup_token: str
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=10, max_length=200)


class EmailVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=6)
    full_name: str | None = None  # used only when creating the owner account


class ChallengeIn(BaseModel):
    challenge_id: str


class MfaStartIn(BaseModel):
    action: str = Field(pattern="^(enable|disable)$")


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    is_super_admin: bool


class MeOut(UserOut):
    brands: list[dict]
    mfa_email_enabled: bool = False
    google_linked: bool = False


class UserCreate(BaseModel):
    """Invite a person. Without a password they sign up with Google or an email code using this address."""
    email: EmailStr
    full_name: str
    password: str | None = Field(default=None, min_length=8)
    is_super_admin: bool = False
    phone: str | None = None
    brand_id: int | None = None
    role: str | None = None


class MembershipIn(BaseModel):
    user_id: int
    role: str = "agent"


# ---- brands
class BrandIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,64}$")
    name: str = Field(min_length=1, max_length=160)
    master_email: EmailStr | None = None
    timezone: str = "Asia/Kolkata"
    primary_color: str | None = None
    # Brand profile (used in the email signature and as the AI's approved facts)
    description: str | None = Field(default=None, max_length=4000)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=255)
    signature_name: str | None = Field(default=None, max_length=120)
    signature_title: str | None = Field(default=None, max_length=120)


class BrandProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    primary_color: str | None = None
    timezone: str | None = None
    description: str | None = Field(default=None, max_length=4000)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=255)
    cc_email: str | None = None
    signature_name: str | None = Field(default=None, max_length=120)
    signature_title: str | None = Field(default=None, max_length=120)


class BrandDeleteIn(BaseModel):
    confirm_name: str


class BrandUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    timezone: str | None = None
    primary_color: str | None = None
    settings: dict | None = None


class BrandOut(ORM):
    id: int
    slug: str
    name: str
    master_email: str | None = None
    status: str
    timezone: str
    primary_color: str | None


# ---- sender accounts
class SenderIn(BaseModel):
    channel: str = Field(pattern="^(email|whatsapp|sms)$")
    address: str
    display_name: str | None = None
    provider: str = "mock"
    status: str = "inactive"
    connection_status: str = "pending"
    daily_limit: int = Field(default=100, ge=0)
    hourly_limit: int = Field(default=30, ge=0)
    provider_daily_limit: int | None = None
    rotation_order: int = 0
    secret_ref: str | None = None
    provider_config: dict = {}
    is_notification_sender: bool = False


class SenderUpdate(BaseModel):
    display_name: str | None = None
    status: str | None = None
    connection_status: str | None = None
    paused_reason: str | None = None
    daily_limit: int | None = None
    hourly_limit: int | None = None
    provider_daily_limit: int | None = None
    rotation_order: int | None = None
    secret_ref: str | None = None
    provider_config: dict | None = None


class SenderOut(ORM):
    id: int
    brand_id: int
    channel: str
    address: str
    display_name: str | None
    provider: str
    status: str
    connection_status: str
    paused_reason: str | None
    daily_limit: int
    hourly_limit: int
    provider_daily_limit: int | None
    sent_today: int
    sent_this_hour: int
    rotation_order: int
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None
    is_notification_sender: bool


# ---- contacts
class ContactIn(BaseModel):
    email: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    company_name: str | None = None
    notes: str | None = None
    consent: dict[str, bool] = {}
    consent_source: str | None = None


class ContactOut(ORM):
    id: int
    contact_id: int
    brand_id: int
    contact_name: str | None
    company_name: str | None
    email: str | None
    phone: str | None
    status: str
    owner_user_id: int | None
    sticky_email_sender_id: int | None
    created_at: datetime


class ConsentIn(BaseModel):
    channel: str
    granted: bool
    source: str | None = None


class UnsubscribeIn(BaseModel):
    channel: str | None = None


# ---- templates / campaigns / messages
class TemplateIn(BaseModel):
    channel: str
    name: str
    body: str
    subject: str | None = None
    purpose: str = "marketing"
    external_template_id: str | None = None
    approval_status: str = "draft"


class TemplateOut(ORM):
    id: int
    brand_id: int
    channel: str
    name: str
    subject: str | None
    body: str
    purpose: str
    external_template_id: str | None
    approval_status: str


class CampaignIn(BaseModel):
    name: str
    channel: str
    kind: str = "bulk"
    subject: str | None = None
    body: str | None = None
    template_id: int | None = None
    default_routing_mode: str = "AUTO"
    allowed_sender_ids: list[int] = []
    scheduled_at: datetime | None = None
    segment: dict = {}


class CampaignOut(ORM):
    id: int
    brand_id: int
    name: str
    channel: str
    kind: str
    status: str
    subject: str | None
    body: str | None
    scheduled_at: datetime | None
    approved_at: datetime | None
    created_at: datetime


class CampaignAction(BaseModel):
    action: str
    reason: str | None = None


class RecipientsIn(BaseModel):
    contact_ids: list[int] = []
    segment: dict = {}  # {"tag": "...", "has_consent": true}


class ComposeIn(BaseModel):
    contact_id: int
    channel: str
    subject: str | None = None
    body: str | None = None
    template_id: int | None = None
    routing_mode: str = "AUTO"
    sender_id: int | None = None
    scheduled_at: datetime | None = None
    idempotency_key: str | None = None
    send_now: bool = True


class OutboundOut(ORM):
    id: int
    brand_id: int
    campaign_id: int | None
    contact_id: int
    channel: str
    to_address: str
    subject: str | None
    status: str
    hold_reason: str | None
    routing_mode: str
    sender_account_id: int | None
    requested_sender_id: int | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    attempt_count: int
    last_error: str | None
    created_at: datetime


# ---- inbox
class ConversationOut(ORM):
    id: int
    brand_id: int
    contact_id: int
    channel: str
    sender_account_id: int | None
    campaign_id: int | None
    subject: str | None
    status: str
    owner_user_id: int | None
    followups_paused: bool
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    unread_count: int


class InboundOut(ORM):
    id: int
    conversation_id: int | None
    channel: str
    from_address: str
    subject: str | None
    body: str | None
    received_at: datetime
    classification: str | None
    classification_source: str | None
    is_unsubscribe: bool


class ClassifyIn(BaseModel):
    classification: str


class ReplyIn(BaseModel):
    body: str
    subject: str | None = None


class ConversationUpdate(BaseModel):
    status: str | None = None
    owner_user_id: int | None = None


# ---- follow-ups & tasks
class FollowUpRuleIn(BaseModel):
    name: str
    campaign_id: int | None = None
    steps: list[dict] = Field(default_factory=lambda: [{"after_days": 2, "mode": "scheduled"},
                                                       {"after_days": 6, "mode": "scheduled"}])
    max_attempts: int = 2
    stop_on_reply: bool = True
    default_assignee_id: int | None = None


class FollowUpRuleOut(ORM):
    id: int
    brand_id: int
    campaign_id: int | None
    name: str
    steps: list
    max_attempts: int
    stop_on_reply: bool
    active: bool


class FollowUpTaskOut(ORM):
    id: int
    brand_id: int
    conversation_id: int | None
    contact_id: int
    campaign_id: int | None
    step_index: int
    mode: str
    due_at: datetime
    status: str
    assigned_user_id: int | None
    cancel_reason: str | None


class ApproveFollowUpIn(BaseModel):
    body: str | None = None
    subject: str | None = None


class TaskIn(BaseModel):
    description: str
    contact_id: int | None = None
    company_name: str | None = None
    assigned_user_id: int | None = None
    priority: str = "medium"
    due_at: datetime | None = None
    notes: str | None = None
    conversation_id: int | None = None


class TaskUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    assigned_user_id: int | None = None
    priority: str | None = None
    due_at: datetime | None = None


class TaskOut(ORM):
    id: int
    task_code: str
    brand_id: int
    contact_id: int | None
    company_name: str | None
    assigned_user_id: int | None
    description: str
    priority: str
    due_at: datetime | None
    status: str
    completed_at: datetime | None
    completed_by: int | None
    completed_source: str | None
    notes: str | None
    conversation_id: int | None
    source: str
    carry_count: int


# ---- master settings / reports
class MasterSettingsIO(ORM):
    master_email: str | None = None
    master_mobile: str | None = None
    mobile_opt_in_at: datetime | None = None
    notification_channel: str = "email"
    morning_time: str = Field(default="09:30", pattern=r"^\d{2}:\d{2}$")
    evening_time: str = Field(default="18:30", pattern=r"^\d{2}:\d{2}$")
    timezone: str = "Asia/Kolkata"
    working_days: list[int] = [0, 1, 2, 3, 4, 5]
    brand_preferences: dict = {}
    emergency_alerts_enabled: bool = True
    report_recipients: list[str] = []
    attach_spreadsheet: bool = False
    notification_email_sender_id: int | None = None
    notification_whatsapp_sender_id: int | None = None
    notification_sms_sender_id: int | None = None
    whatsapp_report_template: str | None = None
    task_carry_forward: bool = True


class ReportOut(ORM):
    id: int
    kind: str
    report_date: datetime | str
    status: str
    generated_at: datetime
    data: dict


class DeliveryOut(ORM):
    id: int
    report_id: int | None
    channel: str
    recipient: str
    status: str
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    next_attempt_at: datetime | None
