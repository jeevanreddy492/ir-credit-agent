"""HITL execution. The ONLY place a sensitive backend action is invoked, and only after a human approved it."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.backend.client import backend
from app.db.models import ActionStatus, PendingAction
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


async def execute_approved_action(action_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        a = (await s.execute(select(PendingAction).where(PendingAction.id == action_id))).scalar_one()
        if a.status != ActionStatus.APPROVED:
            return
        try:
            if a.action_type == "RETRY_CREDIT_REQUEST":
                bureau = a.parameters.get("bureau")
                a.execution_result = await backend.retry_credit_request(
                    a.target_id, approved_by=a.decided_by, bureau=None if bureau == "ALL" else bureau)
                a.status = ActionStatus.EXECUTED
            else:
                a.status, a.execution_result = ActionStatus.EXECUTION_FAILED, {"error": "unsupported action"}
        except Exception as e:  # noqa: BLE001
            log.exception("action execution failed id=%s", action_id)
            a.status, a.execution_result = ActionStatus.EXECUTION_FAILED, {"error": str(e)[:500]}
        a.decided_at = a.decided_at or datetime.now(timezone.utc)
        await s.commit()
