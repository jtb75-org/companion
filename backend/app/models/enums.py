import enum


class RelationshipType(enum.StrEnum):
    FAMILY = "family"
    CASE_WORKER = "case_worker"
    SUPPORT_COORDINATOR = "support_coordinator"
    GROUP_HOME_STAFF = "group_home_staff"
    PAID_SUPPORT = "paid_support"


class AccessTier(enum.StrEnum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


class SourceChannel(enum.StrEnum):
    CAMERA_SCAN = "camera_scan"
    EMAIL = "email"
    MAIL_STATION = "mail_station"


class DocumentClassification(enum.StrEnum):
    BILL = "bill"
    LEGAL = "legal"
    GOVERNMENT = "government"
    MEDICAL = "medical"
    INSURANCE = "insurance"
    FORM = "form"
    JUNK = "junk"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


class UrgencyLevel(enum.StrEnum):
    ROUTINE = "routine"
    NEEDS_ATTENTION = "needs_attention"
    ACT_TODAY = "act_today"
    URGENT = "urgent"


class RoutingDestination(enum.StrEnum):
    HOME = "home"
    MY_HEALTH = "my_health"
    BILLS = "bills"
    PLANS = "plans"


class DocumentStatus(enum.StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    CLASSIFIED = "classified"
    SUMMARIZED = "summarized"
    ROUTED = "routed"
    PENDING_REVIEW = "pending_review"
    ACKNOWLEDGED = "acknowledged"
    HANDLED = "handled"
    FAILED = "failed"  # terminal: pipeline errored or an admin cancelled it


class RetentionPhase(enum.StrEnum):
    FULL = "full"
    IMPORTANT_ONLY = "important_only"
    METADATA_ONLY = "metadata_only"


class MemoryCategory(enum.StrEnum):
    MEDICATION = "medication"
    PROVIDER = "provider"
    APPOINTMENT = "appointment"
    BILL = "bill"
    PREFERENCE = "preference"
    CONTACT_INFO = "contact_info"
    OTHER = "other"


class MemorySource(enum.StrEnum):
    USER_INPUT = "user_input"
    DOCUMENT_EXTRACTION = "document_extraction"
    ONBOARDING = "onboarding"
    SYSTEM = "system"


class PaymentStatus(enum.StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    PAID = "paid"
    OVERDUE = "overdue"


class TodoCategory(enum.StrEnum):
    ERRAND = "errand"
    SHOPPING = "shopping"
    TASK = "task"
    GENERAL = "general"


class TodoSource(enum.StrEnum):
    USER = "user"
    ARLO_SUGGESTION = "arlo_suggestion"
    DOCUMENT = "document"


class QuestionContextType(enum.StrEnum):
    MEDICATION = "medication"
    BILL = "bill"
    DOCUMENT = "document"
    FORM = "form"
    TRAVEL = "travel"
    CHECKIN = "checkin"


class QuestionStatus(enum.StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class CareModel(enum.StrEnum):
    SELF_DIRECTED = "self_directed"
    MANAGED = "managed"


class AccountStatus(enum.StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DEACTIVATED = "deactivated"
    PENDING_DELETION = "pending_deletion"


class InvitationStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


class AssignmentRequestStatus(enum.StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class CaregiverAction(enum.StrEnum):
    VIEWED_DASHBOARD = "viewed_dashboard"
    RECEIVED_ALERT = "received_alert"
    TIER3_SESSION = "tier3_session"


class DeletionReason(enum.StrEnum):
    USER_REQUEST = "user_request"
    ADMIN_REQUEST = "admin_request"
    TTL_EXPIRY = "ttl_expiry"
    RETENTION_POLICY = "retention_policy"


class ReviewStatus(enum.StrEnum):
    PENDING = "pending"
    PRESENTED = "presented"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    AUTO_CREATED = "auto_created"


class RecommendedAction(enum.StrEnum):
    ADD_BILL = "add_bill"
    ADD_APPOINTMENT = "add_appointment"
    REVIEW_WITH_CONTACT = "review_with_contact"
    FILE_ONLY = "file_only"
    DISCARD = "discard"


class ConfigCategory(enum.StrEnum):
    DD_PERSONA = "dd_persona"
    DD_VOICE = "dd_voice"
    PIPELINE_THRESHOLD = "pipeline_threshold"
    ESCALATION_THRESHOLD = "escalation_threshold"
    NOTIFICATION_DEFAULT = "notification_default"
    EMAIL_PREFILTER = "email_prefilter"
    SUMMARIZATION_PROMPT = "summarization_prompt"
    EXTRACTION_PROMPT = "extraction_prompt"
    FEATURE_FLAG = "feature_flag"
    DELETION_SETTINGS = "deletion_settings"
