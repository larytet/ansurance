"""SQLAlchemy models (M0 platform_info, M1 identity, core data, audit)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.ids import uuid7


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformInfo(Base):
    __tablename__ = "platform_info"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = _now()


# ---- identity -----------------------------------------------------------------------
class Role(Base):
    __tablename__ = "role"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)


class Capability(Base):
    __tablename__ = "capability"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)


class RoleCapability(Base):
    __tablename__ = "role_capability"
    role: Mapped[str] = mapped_column(ForeignKey("role.name"), primary_key=True)
    capability: Mapped[str] = mapped_column(ForeignKey("capability.name"), primary_key=True)


class User(Base):
    __tablename__ = "user_account"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="local")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_last_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class UserRole(Base):
    __tablename__ = "user_role"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(ForeignKey("role.name"), primary_key=True)


class RecoveryCode(Base):
    __tablename__ = "recovery_code"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "session"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    last_seen: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class SsoIdentity(Base):
    __tablename__ = "sso_identity"
    __table_args__ = (UniqueConstraint("provider", "subject"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)


class SsoState(Base):
    __tablename__ = "sso_state"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _now()


class LoginEvent(Base):
    __tablename__ = "login_event"
    id: Mapped[uuid.UUID] = _pk()
    at: Mapped[datetime] = _now()
    email: Mapped[str | None] = mapped_column(String(254))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)


# ---- core data ----------------------------------------------------------------------
class Client(Base):
    __tablename__ = "client"
    __table_args__ = (
        Index(
            "ix_client_search_text",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_client_search_skel",
            "search_skel",
            postgresql_using="gin",
            postgresql_ops={"search_skel": "gin_trgm_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    id_number: Mapped[str] = mapped_column(String(9), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_translit_he: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    name_translit_lat: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_skel: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preferred_language: Mapped[str] = mapped_column(String(2), nullable=False, default="he")
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(254))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="lead")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class FamilyLink(Base):
    __tablename__ = "family_link"
    __table_args__ = (UniqueConstraint("head_client_id", "member_client_id"),)
    id: Mapped[uuid.UUID] = _pk()
    head_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False
    )
    member_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)


class ExternalId(Base):
    __tablename__ = "external_id"
    __table_args__ = (UniqueConstraint("system", "external_id"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Insurer(Base):
    __tablename__ = "insurer"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class Policy(Base):
    __tablename__ = "policy"
    __table_args__ = (UniqueConstraint("insurer_id", "policy_number"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    insurer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("insurer.id"), nullable=False)
    policy_number: Mapped[str] = mapped_column(String(64), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    premium_ils: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    renewal_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = _now()


# ---- audit --------------------------------------------------------------------------
class AuditEntry(Base):
    __tablename__ = "audit_entry"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


# ---- documents ----------------------------------------------------------------------
class Blob(Base):
    """One stored object. Content-addressed and write-once (database trigger)."""

    __tablename__ = "blob"
    id: Mapped[uuid.UUID] = _pk()
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    scan_detail: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _now()


class BlobRender(Base):
    """One sandbox run over one stored file: the page images users see (DESIGN 8.1).
    Keyed by the file's hash and the tool version, so a better engine adds a row and the
    original is never touched."""

    __tablename__ = "blob_render"
    __table_args__ = (UniqueConstraint("blob_id", "tool", name="uq_blob_render"),)
    id: Mapped[uuid.UUID] = _pk()
    blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blob.id"), nullable=False, index=True)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # ready | refused
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = _now()


class BlobPage(Base):
    __tablename__ = "blob_page"
    __table_args__ = (UniqueConstraint("render_id", "page_no", name="uq_blob_page"),)
    id: Mapped[uuid.UUID] = _pk()
    render_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blob_render.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    image_blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blob.id"), nullable=False)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blob.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_channel: Mapped[str] = mapped_column(String(16), nullable=False, default="upload")
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    received_at: Mapped[datetime] = _now()
    retention_until: Mapped[date] = mapped_column(Date, nullable=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DerivedArtifact(Base):
    """OCR text, page images, thumbnails: rebuildable from the original (M5 fills these)."""

    __tablename__ = "derived_artifact"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blob.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


# ---- inbox, consent, messaging (M3) -------------------------------------------------
class InboxItem(Base):
    __tablename__ = "inbox_item"
    __table_args__ = (
        Index("ix_inbox_role_status", "role", "status", "created_at"),
        Index("ix_inbox_assignee", "assignee_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(ForeignKey("role.name"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    urgency: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    urgent_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolution: Mapped[str | None] = mapped_column(Text)


class InboxEvent(Base):
    """Every change to an item. Nothing leaves the Inbox without a recorded reason."""

    __tablename__ = "inbox_event"
    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbox_item.id"), nullable=False, index=True
    )
    at: Mapped[datetime] = _now()
    actor: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    from_role: Mapped[str | None] = mapped_column(String(32))
    to_role: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ConsentEvent(Base):
    """Append-only consent registry. The latest event per client and channel wins."""

    __tablename__ = "consent_event"
    __table_args__ = (Index("ix_consent_client_channel", "client_id", "channel", "recorded_at"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = _now()
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OutboxMessage(Base):
    """The single send path. M8 adds the adapters that deliver `queued` messages."""

    __tablename__ = "outbox_message"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()
    provider_id: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivery: Mapped[str | None] = mapped_column(String(12))  # sent | delivered | read | failed
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaign.id"), index=True)


# ---- processes (M4) -----------------------------------------------------------------
class ProcessDef(Base):
    """A versioned process definition (stages, roles, dependencies, checks) as data."""

    __tablename__ = "process_def"
    __table_args__ = (UniqueConstraint("key", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()


class ProcessInstance(Base):
    __tablename__ = "process_instance"
    id: Mapped[uuid.UUID] = _pk()
    def_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("process_def.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    started_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    started_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)


class StageInstance(Base):
    __tablename__ = "stage_instance"
    __table_args__ = (
        UniqueConstraint("instance_id", "stage_key"),
        Index("ix_stage_role_status", "role", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("process_instance.id"), nullable=False, index=True
    )
    stage_key: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CheckInstance(Base):
    __tablename__ = "check_instance"
    __table_args__ = (UniqueConstraint("stage_id", "idx"),)
    id: Mapped[uuid.UUID] = _pk()
    stage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stage_instance.id"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ---- jobs and intake (M5) -----------------------------------------------------------
class Job(Base):
    """Background work. A Postgres table claimed with FOR UPDATE SKIP LOCKED (no broker)."""

    __tablename__ = "job"
    __table_args__ = (Index("ix_job_claim", "status", "run_at"),)
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_at: Mapped[datetime] = _now()
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntakeItem(Base):
    """One incoming item (a file, or a page range of a file) travelling the pipeline."""

    __tablename__ = "intake_item"
    __table_args__ = (Index("ix_intake_state", "state", "received_at"),)
    id: Mapped[uuid.UUID] = _pk()
    blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blob.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intake_item.id"))
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    sender: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    received_at: Mapped[datetime] = _now()
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="received")
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ocr_conf: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    type_conf: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    type_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    entities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    chosen_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"))
    attribution_conf: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    route: Mapped[str | None] = mapped_column(String(12))
    route_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), unique=True)
    inbox_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()


class IntakeFeedback(Base):
    """Every human correction. Stored to improve the rules and, later, a model."""

    __tablename__ = "intake_feedback"
    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("intake_item.id"), nullable=False, index=True
    )
    at: Mapped[datetime] = _now()
    actor: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    suggested_client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ---- audit archive (M7) -------------------------------------------------------------
class AuditSeal(Base):
    """A month of the audit chain sealed into a Parquet file held outside the database."""

    __tablename__ = "audit_seal"
    id: Mapped[uuid.UUID] = _pk()
    period: Mapped[str] = mapped_column(String(7), unique=True, nullable=False)  # YYYY-MM
    first_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entries: Mapped[int] = mapped_column(Integer, nullable=False)
    head_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    sealed_at: Mapped[datetime] = _now()


# ---- integrations (M8) --------------------------------------------------------------
class AdapterCall(Base):
    """Every call to an outside system is logged (REQUIREMENTS section 9)."""

    __tablename__ = "adapter_call"
    __table_args__ = (Index("ix_adapter_call_at", "at"),)
    id: Mapped[uuid.UUID] = _pk()
    at: Mapped[datetime] = _now()
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class InboundMessage(Base):
    """A message received from a client (WhatsApp now; SMS later)."""

    __tablename__ = "inbound_message"
    __table_args__ = (UniqueConstraint("channel", "provider_id"),)
    id: Mapped[uuid.UUID] = _pk()
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_address: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # text | media
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _now()


class SyncState(Base):
    """Cursor and last result per external system sync."""

    __tablename__ = "sync_state"
    system: Mapped[str] = mapped_column(String(32), primary_key=True)
    cursor: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MailSeen(Base):
    """Message-IDs already ingested, so a message is never filed twice."""

    __tablename__ = "mail_seen"
    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    at: Mapped[datetime] = _now()


# ---- M9 flows F1 to F5 ------------------------------------------------------------------
class ESignRequest(Base):
    """A document sent to the client for signature. `kind` says what the signature is for."""

    __tablename__ = "esign_request"
    __table_args__ = (Index("ix_esign_client_kind", "client_id", "kind"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    signed_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    provider_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="sent")
    signer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    legal_text_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sent_at: Mapped[datetime] = _now()
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[date | None] = mapped_column(Date)  # validity of the signed authorization
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))


class KycForm(Base):
    __tablename__ = "kyc_form"
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk: Mapped[str] = mapped_column(String(8), nullable=False, default="normal")
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    completed_at: Mapped[datetime] = _now()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdviceRecord(Base):
    __tablename__ = "advice_record"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    needs: Mapped[str] = mapped_column(Text, nullable=False)
    advice: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class Application(Base):
    """F2: an application for a new policy, from advice to the issued policy."""

    __tablename__ = "application"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    insurer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("insurer.id"), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    advice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("advice_record.id"))
    esign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("esign_request.id"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy.id"))
    confirmation_doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class RenewalOutcome(Base):
    """F3: what happened to a policy at a renewal date. One row per policy and cycle."""

    __tablename__ = "renewal_outcome"
    __table_args__ = (UniqueConstraint("policy_id", "cycle_date"),)
    id: Mapped[uuid.UUID] = _pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy.id"), nullable=False)
    cycle_date: Mapped[date] = mapped_column(Date, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    new_renewal_date: Mapped[date | None] = mapped_column(Date)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    recorded_at: Mapped[datetime] = _now()


class Claim(Base):
    __tablename__ = "claim"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy.id"), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    place: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    insurer_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    paid_ils: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    outcome_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _now()


class ClaimEvent(Base):
    __tablename__ = "claim_event"
    id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claim.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    at: Mapped[datetime] = _now()


class ClaimItem(Base):
    """One line of a claim's document checklist; done when a document is attached."""

    __tablename__ = "claim_item"
    __table_args__ = (UniqueConstraint("claim_id", "label"),)
    id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claim.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))


# ---- M10 campaigns (F10) ------------------------------------------------------------------
class Campaign(Base):
    __tablename__ = "campaign"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)  # service or marketing
    benefit: Mapped[str] = mapped_column(Text, nullable=False)  # the stated client benefit
    segment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    base_language: Mapped[str] = mapped_column(String(2), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(18), nullable=False, default="draft")
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignTranslation(Base):
    """A translation of the campaign text. Nothing is sent in a language until a person has
    reviewed it (F10 step 4)."""

    __tablename__ = "campaign_translation"
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(2), primary_key=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    reviewed_at: Mapped[datetime] = _now()


class CampaignRecipient(Base):
    """The audience snapshot: who was selected, who was excluded and why (evidence)."""

    __tablename__ = "campaign_recipient"
    __table_args__ = (UniqueConstraint("campaign_id", "client_id"),)
    id: Mapped[uuid.UUID] = _pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # selected excluded queued blocked
    channel: Mapped[str | None] = mapped_column(String(10))
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    outbox_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outbox_message.id"))


# ---- M10 manager: complaints and compliance (F8), commissions (F7) -----------------------------
class Complaint(Base):
    __tablename__ = "complaint"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy.id"))
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="complaint")
    channel: Mapped[str] = mapped_column(String(12), nullable=False, default="other")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    opened_at: Mapped[datetime] = _now()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)


class AgentLicence(Base):
    __tablename__ = "agent_licence"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    licence_no: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    cpd_required_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpd_done_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CommissionTerm(Base):
    """Expected commission: a percentage of the premium, per insurer and product."""

    __tablename__ = "commission_term"
    __table_args__ = (UniqueConstraint("insurer_id", "product"),)
    id: Mapped[uuid.UUID] = _pk()
    insurer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("insurer.id"), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)


class CommissionStatement(Base):
    __tablename__ = "commission_statement"
    __table_args__ = (UniqueConstraint("insurer_id", "period"),)
    id: Mapped[uuid.UUID] = _pk()
    insurer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("insurer.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # 2026-10
    declared_total_ils: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    imported_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    imported_at: Mapped[datetime] = _now()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommissionLine(Base):
    __tablename__ = "commission_line"
    id: Mapped[uuid.UUID] = _pk()
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commission_statement.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_number: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_ils: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy.id"))
    expected_ils: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    # matched | wrong_amount | unknown_policy | no_terms | clawback | missing (not on the statement)


# ---- M10 tax (F15), translation, client portal ---------------------------------------------------
class TaxCertificate(Base):
    """F15 A: the yearly certificate of deposits, per client, policy and tax year."""

    __tablename__ = "tax_certificate"
    __table_args__ = (UniqueConstraint("policy_id", "tax_year"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy.id"), nullable=False)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="to_request")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))


class TaxResidency(Base):
    """F15 C: the self-declaration of tax residency the insurers ask for."""

    __tablename__ = "tax_residency"
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client.id"), primary_key=True)
    residency: Mapped[str] = mapped_column(String(10), nullable=False)  # israel | us | foreign
    form_received: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    declared_at: Mapped[datetime] = _now()


class TaxDeadline(Base):
    """F15 D: the agency's own tax dates. One may not pass without a manager Inbox item."""

    __tablename__ = "tax_deadline"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))


class Task(Base):
    """An agent's or clerk's to-do, optionally about one client. Delegating changes the owner
    and records who delegated it; the creator never changes."""

    __tablename__ = "task"
    __table_args__ = (Index("ix_task_owner_open", "owner_id", "done_at", "due_on"),)
    id: Mapped[uuid.UUID] = _pk()
    text: Mapped[str] = mapped_column(String(200), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    delegated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = _now()
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Meeting(Base):
    """A meeting with a client, kept on the client file with its summary."""

    __tablename__ = "meeting"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    held_on: Mapped[date] = mapped_column(Date, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class TranslationRecord(Base):
    """Original and translation kept side by side in the client timeline (REQUIREMENTS 5)."""

    __tablename__ = "translation_record"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("client.id"), index=True)
    source_lang: Mapped[str] = mapped_column(String(2), nullable=False)
    target_lang: Mapped[str] = mapped_column(String(2), nullable=False)
    original: Mapped[str] = mapped_column(Text, nullable=False)
    translated: Mapped[str] = mapped_column(Text, nullable=False)
    flagged: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class PortalLink(Base):
    """A read-only view for the client. Only the hash of the token is stored."""

    __tablename__ = "portal_link"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = _now()
