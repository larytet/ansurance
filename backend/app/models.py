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
