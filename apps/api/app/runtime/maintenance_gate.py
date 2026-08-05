from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class MaintenanceGate:
    def __init__(self) -> None:
        self._draining = False
        self._active_mutations = 0
        self._lock = asyncio.Lock()
        self._idle = asyncio.Event()
        self._idle.set()

    async def enter_mutation(self) -> bool:
        async with self._lock:
            if self._draining:
                return False
            self._active_mutations += 1
            self._idle.clear()
            return True

    async def leave_mutation(self) -> None:
        async with self._lock:
            self._active_mutations -= 1
            if self._active_mutations == 0:
                self._idle.set()

    async def drain(self, timeout_seconds: float = 900.0) -> None:
        async with self._lock:
            self._draining = True
            if self._active_mutations == 0:
                self._idle.set()
        await asyncio.wait_for(self._idle.wait(), timeout=timeout_seconds)

    async def resume(self) -> None:
        async with self._lock:
            self._draining = False

    def status(self) -> dict[str, object]:
        return {
            "draining": self._draining,
            "active_mutations": self._active_mutations,
        }


class MaintenanceGateMiddleware:
    _MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: ASGIApp, gate: MaintenanceGate) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        internal = str(scope.get("path", "")).startswith("/internal/controller/")
        mutation = scope["type"] == "http" and scope.get("method") in (
            self._MUTATION_METHODS
        )
        entered = False
        if mutation and not internal:
            entered = await self.gate.enter_mutation()
            if not entered:
                response = JSONResponse(
                    {"detail": "maintenance operation is draining writes"},
                    status_code=503,
                    headers={"Retry-After": "5"},
                )
                await response(scope, receive, send)
                return
        try:
            await self.app(scope, receive, send)
        finally:
            if entered:
                await self.gate.leave_mutation()
