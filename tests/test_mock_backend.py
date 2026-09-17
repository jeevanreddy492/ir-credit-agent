from fastapi.testclient import TestClient

from mock_backend.main import app

H = {"Authorization": "Bearer dev-token"}
c = TestClient(app)


def test_get_request():
    r = c.get("/credit-requests/12345", headers=H)
    assert r.status_code == 200 and r.json()["ir_status"] == "PARTIAL_FAILURE"


def test_unknown_request_404():
    assert c.get("/credit-requests/99999", headers=H).status_code == 404


def test_retry_requires_approval_header():
    assert c.post("/credit-requests/12345/retry", headers=H).status_code == 403
    r = c.post("/credit-requests/12345/retry", headers={**H, "X-Approved-By": "ops.user"})
    assert r.status_code == 200 and r.json()["status"] == "RETRY_QUEUED"


def test_auth_required():
    assert c.get("/credit-requests/12345").status_code == 401
