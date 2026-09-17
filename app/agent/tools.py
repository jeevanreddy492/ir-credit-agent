"""
ADK tools. Docstrings + type hints become the schema the model sees.

Design rules enforced here:
  * Read tools call the IR backend service layer, never a database.
  * There is NO execute-retry tool. `propose_action` only records intent; the workflow
    persists it as a PendingAction and a human decides. The backend call happens in
    app/workflows/actions.py after approval.
"""
from google.adk.tools import ToolContext

from app.backend.client import backend
from app.rag import retriever


async def get_credit_request(request_id: str) -> dict:
    """Get an IR credit request: lender, loan purpose, requested bureaus, overall IR status, retry_count.

    Args:
        request_id: The IR credit request id, e.g. "12345".
    """
    return await backend.get_credit_request(request_id)


async def get_bureau_status(request_id: str) -> dict:
    """Get the per-bureau integration outcome (EQUIFAX, TRANSUNION) for a credit request:
    status (COMPLETED / FAILED / PENDING), whether a report was received, and any error_code.

    Args:
        request_id: The IR credit request id.
    """
    return await backend.get_bureau_status(request_id)


async def get_error_details(request_id: str) -> dict:
    """Get the error records for a credit request: bureau, error code, message, and whether the
    backend marks it retryable. Call this when any bureau status is FAILED.

    Args:
        request_id: The IR credit request id.
    """
    return await backend.get_error_details(request_id)


async def search_knowledge_base(query: str, tool_context: ToolContext) -> dict:
    """Search IR's internal troubleshooting documentation. Use it to find what an error code means
    and the approved procedure for it. Always cite the returned source in your answer.

    Args:
        query: An error code or a short description, e.g. "TU-IDM-402" or "bureau gateway timeout retry policy".
    """
    results = await retriever.search(query, k=4)
    cites = tool_context.state.get("citations", [])
    cites.extend({"chunk_id": r["chunk_id"], "source": r["source"], "score": r["score"]} for r in results)
    tool_context.state["citations"] = cites
    return {"results": results}


async def propose_action(action_type: str, request_id: str, bureau: str, reason: str, tool_context: ToolContext) -> dict:
    """Propose a sensitive action for human approval. This does NOT execute anything.
    An authorized IR operations user must approve it before the backend performs it.

    Args:
        action_type: Currently only "RETRY_CREDIT_REQUEST" is supported.
        request_id: The IR credit request id to act on.
        bureau: Which bureau to retry: "EQUIFAX", "TRANSUNION", or "ALL".
        reason: One or two sentences explaining why, citing the procedure you followed.
    """
    if action_type != "RETRY_CREDIT_REQUEST":
        return {"accepted": False, "detail": "unsupported action_type"}
    proposals = tool_context.state.get("proposed_actions", [])
    proposals.append({"action_type": action_type, "target_id": request_id,
                      "parameters": {"bureau": bureau.upper()}, "reason": reason})
    tool_context.state["proposed_actions"] = proposals
    return {"accepted": True, "status": "PENDING_HUMAN_APPROVAL"}
