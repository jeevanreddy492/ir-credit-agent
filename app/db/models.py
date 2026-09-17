"""
Agent-service persistence. Deliberately contains NO borrower or credit data —
that lives behind the IR backend service layer. We store only:
  Investigation  : the user's question, the agent's answer, and the full tool trace (audit)
  PendingAction  : an action the agent proposed that needs human approval (HITL gate)
  KnowledgeChunk : RAG corpus (internal troubleshooting docs) with pgvector embeddings
"""
import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


class InvestigationStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"           # answer ready, no action needed
    AWAITING_APPROVAL = "AWAITING_APPROVAL"   # answer ready + a proposed action needs a human
    FAILED = "FAILED"


class ActionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requested_by: Mapped[str] = mapped_column(String(120))          # ops user id
    question: Mapped[str] = mapped_column(Text)
    credit_request_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[InvestigationStatus] = mapped_column(Enum(InvestigationStatus), default=InvestigationStatus.QUEUED)
    answer: Mapped[str | None] = mapped_column(Text)
    tool_trace: Mapped[list] = mapped_column(JSONB, default=list)   # every tool call + response
    citations: Mapped[list] = mapped_column(JSONB, default=list)    # knowledge chunks used
    model: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    actions: Mapped[list["PendingAction"]] = relationship(back_populates="investigation", lazy="selectin")


class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id"))
    action_type: Mapped[str] = mapped_column(String(64))            # e.g. RETRY_CREDIT_REQUEST
    target_id: Mapped[str] = mapped_column(String(64))              # credit request id
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)   # e.g. {"bureau": "TRANSUNION"}
    agent_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), default=ActionStatus.PROPOSED)
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    execution_result: Mapped[dict | None] = mapped_column(JSONB)

    investigation: Mapped[Investigation] = relationship(back_populates="actions")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(200))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))
