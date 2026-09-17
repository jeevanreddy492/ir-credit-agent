"""
HTTP client for the IR backend service layer. The agent never touches a borrower database —
every read/write goes through these approved endpoints, so authz, auditing, and data filtering
stay in the enterprise application. Locally this points at mock_backend/.
"""
import httpx

from app.config import settings


class IRBackendClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 10.0):
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.ir_backend_url,
            headers={"Authorization": f"Bearer {token or settings.ir_backend_token}"},
            timeout=timeout,
        )

    async def get_credit_request(self, request_id: str) -> dict:
        return await self._get(f"/credit-requests/{request_id}")

    async def get_bureau_status(self, request_id: str) -> dict:
        return await self._get(f"/credit-requests/{request_id}/bureaus")

    async def get_error_details(self, request_id: str) -> dict:
        return await self._get(f"/credit-requests/{request_id}/errors")

    async def retry_credit_request(self, request_id: str, approved_by: str, bureau: str | None = None) -> dict:
        """Only called by the workflow AFTER a human approves. Never exposed as an agent tool."""
        r = await self._client.post(f"/credit-requests/{request_id}/retry",
                                    params={"bureau": bureau} if bureau else None,
                                    headers={"X-Approved-By": approved_by})
        r.raise_for_status()
        return r.json()

    async def _get(self, path: str) -> dict:
        r = await self._client.get(path)
        if r.status_code == 404:
            return {"found": False, "detail": r.json().get("detail")}
        r.raise_for_status()
        return {"found": True, **r.json()}

    async def aclose(self):
        await self._client.aclose()


backend = IRBackendClient()
