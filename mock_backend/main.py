"""
Mock IR backend — stands in for the real service layer that talks to the bureaus.
It does NOT call Equifax/TransUnion; it returns seeded, synthetic responses.
All data here is invented. Run on :9000.

Endpoints (the agent's tools map 1:1 to these):
  GET  /credit-requests/{id}            -> lender request + IR status
  GET  /credit-requests/{id}/bureaus    -> per-bureau integration status
  GET  /credit-requests/{id}/errors     -> error/status details
  POST /credit-requests/{id}/retry      -> sensitive: requires X-Approved-By header
"""
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException

from mock_backend.seed import CREDIT_REQUESTS

app = FastAPI(title="IR Mock Backend", version="0.1.0")


def _auth(authorization: str | None):
    if authorization != "Bearer dev-token":
        raise HTTPException(401, "invalid token")


def _get(req_id: str):
    r = CREDIT_REQUESTS.get(req_id)
    if r is None:
        raise HTTPException(404, f"credit request {req_id} not found")
    return r


@app.get("/credit-requests/{req_id}")
def get_credit_request(req_id: str, authorization: str | None = Header(default=None)):
    _auth(authorization)
    r = _get(req_id)
    return {k: r[k] for k in ("request_id", "lender_id", "loan_purpose", "requested_bureaus", "ir_status", "created_at", "retry_count", "borrower_ref")}


@app.get("/credit-requests/{req_id}/bureaus")
def get_bureau_status(req_id: str, authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {"request_id": req_id, "bureaus": _get(req_id)["bureaus"]}


@app.get("/credit-requests/{req_id}/errors")
def get_error_details(req_id: str, authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {"request_id": req_id, "errors": _get(req_id)["errors"]}


@app.post("/credit-requests/{req_id}/retry")
def retry_credit_request(req_id: str, bureau: str | None = None,
                         authorization: str | None = Header(default=None),
                         x_approved_by: str | None = Header(default=None)):
    """Sensitive action. The real service would enforce RBAC + audit; the mock enforces the approval header."""
    _auth(authorization)
    if not x_approved_by:
        raise HTTPException(403, "retry requires human approval (X-Approved-By)")
    r = _get(req_id)
    r["retry_count"] += 1
    r["ir_status"] = "RETRY_QUEUED"
    return {"request_id": req_id, "bureau": bureau or "ALL", "status": "RETRY_QUEUED",
            "approved_by": x_approved_by, "queued_at": datetime.now(timezone.utc).isoformat()}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
