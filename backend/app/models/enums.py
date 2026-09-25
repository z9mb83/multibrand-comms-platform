"""String constants used in status columns (kept as plain strings for easy migration)."""
from enum import StrEnum


class Channel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"


class RoutingMode(StrEnum):
    AUTO = "AUTO"
    FIXED = "FIXED"
    STICKY = "STICKY"


class Role(StrEnum):
    BRAND_ADMIN = "brand_admin"
    MANAGER = "manager"
    AGENT = "agent"
    VIEWER = "viewer"
    BLOCKED = "blocked"


class SenderStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PAUSED = "paused"
    DISABLED = "disabled"


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    PENDING = "pending"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class MessageStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    HELD_FOR_REVIEW = "held_for_review"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPPRESSED = "suppressed"
    UNKNOWN = "unknown"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    EMERGENCY_STOPPED = "emergency_stopped"
    CANCELLED = "cancelled"


class ConsentStatus(StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class ConversationStatus(StrEnum):
    OPEN = "open"
    NEEDS_REPLY = "needs_reply"
    REPLIED = "replied"
    CLOSED = "closed"


class FollowUpMode(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    REMINDER = "reminder"
    APPROVAL_SEND = "approval_send"


class FollowUpStatus(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SENT = "sent"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PAUSED_ON_REPLY = "paused_on_reply"
    PAUSED = "paused"


class TaskStatus(StrEnum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    BLOCKED = "Blocked"
    OVERDUE = "Overdue"
    CANCELLED = "Cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ReplyClass(StrEnum):
    INTERESTED = "Interested"
    NEEDS_INFO = "Needs information"
    CALLBACK = "Callback requested"
    NOT_INTERESTED = "Not interested"
    UNSUBSCRIBE = "Unsubscribe"
    OTHER = "Other"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    RETRYING = "retrying"
    FAILED = "failed"
