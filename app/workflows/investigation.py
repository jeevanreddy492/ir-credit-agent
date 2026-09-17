"""Async investigation workflow: API returns 202, this runs in the background, client polls."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.agent.runner import investigate
from app.config import settings
from app.db.models import Investigation, InvestigationStatus, PendingAction
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


async def run_investigation(investigation_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        inv = (await s.execute(select(Investigation).where(Investigation.id == investigation_id))).scalar_one()
        inv.status = InvestigationStatus.RUNNING
        await s.commit()
        question, user = inv.question, inv.requested_by

    try:
        result = await investigate(question, user_id=user)
    except Exception as e:  # noqa: BLE001
        log.exception("investigation failed id=%s", investigation_id)
        async with SessionLocal() as s:
            inv = (await s.execute(select(Investigation).where(Investigation.id == investigation_id))).scalar_one()
            inv.status, inv.error = InvestigationStatus.FAILED, str(e)[:2000]
            await s.commit()
        return

    async with SessionLocal() as s:
        inv = (await s.execute(select(Investigation).where(Investigation.id == investigation_id))).scalar_one()
        inv.answer, inv.tool_trace, inv.citations, inv.model = result["answer"], result["trace"], result["citations"], settings.llm_model
        inv.completed_at = datetime.now(timezone.utc)
        for p in result["proposed_actions"]:
            s.add(PendingAction(investigation_id=inv.id, action_type=p["action_type"], target_id=p["target_id"],
                                parameters=p["parameters"], agent_reason=p["reason"]))
        inv.status = InvestigationStatus.AWAITING_APPROVAL if result["proposed_actions"] else InvestigationStatus.COMPLETED
        await s.commit()
        log.info("investigation done id=%s status=%s tools=%d", investigation_id, inv.status,
                 sum(1 for t in result["trace"] if t["type"] == "tool_call"))
