import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.db.models import ActionStatus, Investigation, InvestigationStatus, PendingAction
from app.db.session import get_session
from app.rag import retriever
from app.workflows.actions import execute_approved_action
from app.workflows.investigation import run_investigation

router = APIRouter()


@router.post("/investigations", status_code=status.HTTP_202_ACCEPTED)
async def create_investigation(body: schemas.InvestigationCreate, bg: BackgroundTasks, s: AsyncSession = Depends(get_session)):
    """Ask the agent a question. Returns immediately; poll GET /investigations/{id}."""
    inv = Investigation(question=body.question, requested_by=body.requested_by)
    s.add(inv)
    await s.commit()
    bg.add_task(run_investigation, inv.id)
    return {"investigation_id": str(inv.id), "status": InvestigationStatus.QUEUED}


@router.get("/investigations/{inv_id}", response_model=schemas.InvestigationOut)
async def get_investigation(inv_id: uuid.UUID, s: AsyncSession = Depends(get_session)):
    inv = (await s.execute(select(Investigation).where(Investigation.id == inv_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(404, "investigation not found")
    return schemas.InvestigationOut.model_validate(inv, from_attributes=True)


@router.get("/actions/pending", response_model=list[schemas.ActionOut])
async def pending_actions(s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(PendingAction).where(PendingAction.status == ActionStatus.PROPOSED))).scalars().all()
    return [schemas.ActionOut.model_validate(a, from_attributes=True) for a in rows]


@router.post("/actions/{action_id}/decision", response_model=schemas.ActionOut)
async def decide_action(action_id: uuid.UUID, body: schemas.ActionDecision, bg: BackgroundTasks, s: AsyncSession = Depends(get_session)):
    """HITL gate. A human approves or rejects; approval triggers execution through the backend service layer."""
    a = (await s.execute(select(PendingAction).where(PendingAction.id == action_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "action not found")
    if a.status != ActionStatus.PROPOSED:
        raise HTTPException(409, f"action already {a.status}")
    a.decided_by, a.decision_notes, a.decided_at = body.decided_by, body.notes, datetime.now(timezone.utc)
    a.status = ActionStatus.APPROVED if body.decision == "APPROVE" else ActionStatus.REJECTED
    await s.commit()
    if a.status == ActionStatus.APPROVED:
        bg.add_task(execute_approved_action, a.id)
    return schemas.ActionOut.model_validate(a, from_attributes=True)


@router.get("/knowledge/search")
async def knowledge_search(q: str, k: int = 4):
    """Debug retrieval quality independent of the agent."""
    return await retriever.search(q, k=k)
