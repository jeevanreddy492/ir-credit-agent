import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import ActionStatus, InvestigationStatus


class InvestigationCreate(BaseModel):
    question: str = Field(min_length=5, max_length=2000, examples=["Why did credit request 12345 fail?"])
    requested_by: str = Field(min_length=1, max_length=120, description="IR ops user id (from SSO in prod)")


class ActionOut(BaseModel):
    id: uuid.UUID
    action_type: str
    target_id: str
    parameters: dict
    agent_reason: str
    status: ActionStatus
    decided_by: str | None
    decision_notes: str | None
    execution_result: dict | None


class InvestigationOut(BaseModel):
    id: uuid.UUID
    status: InvestigationStatus
    question: str
    answer: str | None
    citations: list
    tool_trace: list
    actions: list[ActionOut]
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ActionDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=120)
    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    notes: str | None = None
