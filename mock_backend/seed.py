from datetime import datetime, timedelta, timezone

def _minutes_ago(m: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")

"""SYNTHETIC DATA. No real borrowers, lenders, or bureau payloads. Error codes are invented for this project."""

CREDIT_REQUESTS: dict[str, dict] = {
    "12345": {
        "request_id": "12345", "lender_id": "LND-001", "loan_purpose": "PURCHASE",
        "requested_bureaus": ["EQUIFAX", "TRANSUNION"], "ir_status": "PARTIAL_FAILURE",
        "created_at": "2026-09-10T14:02:11Z", "retry_count": 0, "borrower_ref": "BRW-7f3a",
        "bureaus": [
            {"bureau": "EQUIFAX", "status": "COMPLETED", "report_received": True, "latency_ms": 1840},
            {"bureau": "TRANSUNION", "status": "FAILED", "report_received": False, "latency_ms": 2210, "error_code": "TU-IDM-402"},
        ],
        "errors": [{"bureau": "TRANSUNION", "code": "TU-IDM-402", "message": "Identifying information mismatch: name/address combination not found", "retryable": True}],
    },
    "12346": {
        "request_id": "12346", "lender_id": "LND-002", "loan_purpose": "REFINANCE",
        "requested_bureaus": ["EQUIFAX", "TRANSUNION"], "ir_status": "FAILED",
        "created_at": "2026-09-11T09:15:44Z", "retry_count": 2, "borrower_ref": "BRW-91c0",
        "bureaus": [
            {"bureau": "EQUIFAX", "status": "FAILED", "report_received": False, "latency_ms": 30000, "error_code": "EQ-TMO-504"},
            {"bureau": "TRANSUNION", "status": "FAILED", "report_received": False, "latency_ms": 30000, "error_code": "TU-TMO-504"},
        ],
        "errors": [
            {"bureau": "EQUIFAX", "code": "EQ-TMO-504", "message": "Gateway timeout waiting for bureau response", "retryable": True},
            {"bureau": "TRANSUNION", "code": "TU-TMO-504", "message": "Gateway timeout waiting for bureau response", "retryable": True},
        ],
    },
    "12347": {
        "request_id": "12347", "lender_id": "LND-001", "loan_purpose": "PURCHASE",
        "requested_bureaus": ["EQUIFAX"], "ir_status": "FAILED",
        "created_at": "2026-09-12T16:40:03Z", "retry_count": 0, "borrower_ref": "BRW-22be",
        "bureaus": [{"bureau": "EQUIFAX", "status": "FAILED", "report_received": False, "latency_ms": 900, "error_code": "EQ-FRZ-301"}],
        "errors": [{"bureau": "EQUIFAX", "code": "EQ-FRZ-301", "message": "Consumer file is frozen; report cannot be released", "retryable": False}],
    },
    "12348": {
        "request_id": "12348", "lender_id": "LND-003", "loan_purpose": "HELOC",
        "requested_bureaus": ["EQUIFAX", "TRANSUNION"], "ir_status": "IN_PROGRESS",
        "created_at": "2026-09-14T08:01:19Z", "retry_count": 0, "borrower_ref": "BRW-5d17",
        "bureaus": [
            {"bureau": "EQUIFAX", "status": "COMPLETED", "report_received": True, "latency_ms": 1500},
            {"bureau": "TRANSUNION", "status": "PENDING", "report_received": False, "latency_ms": None},
        ],
        "errors": [],
    },
    "12349": {
        "request_id": "12349", "lender_id": "LND-002", "loan_purpose": "PURCHASE",
        "requested_bureaus": ["EQUIFAX", "TRANSUNION"], "ir_status": "COMPLETED",
        "created_at": "2026-09-13T11:22:05Z", "retry_count": 0, "borrower_ref": "BRW-a0c4",
        "bureaus": [
            {"bureau": "EQUIFAX", "status": "COMPLETED", "report_received": True, "latency_ms": 1200},
            {"bureau": "TRANSUNION", "status": "COMPLETED", "report_received": True, "latency_ms": 1350},
        ],
        "errors": [],
    },
}
