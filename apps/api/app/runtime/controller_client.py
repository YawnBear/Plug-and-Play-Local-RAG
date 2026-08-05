from __future__ import annotations

from uuid import UUID

import httpx


class ControllerUnavailable(RuntimeError):
    pass


class ControllerClient:
    """Loopback client whose payload matches the fixed supervisor protocol."""

    def __init__(
        self, base_url: str, service_token: str, *, timeout_seconds: float = 5.0
    ) -> None:
        if len(service_token) < 32:
            raise ValueError(
                "controller service token must contain at least 32 characters"
            )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {service_token}"},
        )

    async def apply_configuration(self, change_id: UUID, nonce: str) -> None:
        await self._command("apply_configuration", change_id, nonce)

    async def create_backup(self, backup_run_id: UUID, nonce: str) -> None:
        await self._command("create_backup", backup_run_id, nonce)

    async def _command(self, action: str, change_id: UUID, nonce: str) -> None:
        try:
            response = await self._client.post(
                "/v1/commands",
                json={
                    "action": action,
                    "change_id": str(change_id),
                    "nonce": nonce,
                },
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise ControllerUnavailable("local controller is unavailable") from exc

    async def close(self) -> None:
        await self._client.aclose()
