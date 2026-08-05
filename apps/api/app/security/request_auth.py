import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.unit_of_work import AuthenticatedUnitOfWork
from app.schemas.auth import AuthUser
from app.security.actor import ActorContext
from app.security.tokens import hash_opaque_token
from app.services.authentication import (
    AuthenticationUnavailable,
    InvalidSession,
)

SESSION_COOKIE = "rag_session"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SESSION_EXPIRED_DETAIL = {
    "code": "session_expired",
    "message": "Your session expired after 30 minutes of inactivity.",
}
AUTHENTICATION_REQUIRED_DETAIL = {
    "code": "authentication_required",
    "message": "Authentication is required.",
}


@dataclass(frozen=True, slots=True)
class AuthenticatedRequest:
    actor: ActorContext
    user: AuthUser
    session: AsyncSession
    session_token: str


def _require_mutation_proof(request: Request, csrf_token: str | None) -> None:
    if (
        request.headers.get("origin")
        not in request.app.state.settings.allowed_request_origins
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "request origin is not allowed")
    supplied = request.headers.get(CSRF_HEADER)
    if not csrf_token or not supplied or not hmac.compare_digest(supplied, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


@asynccontextmanager
async def authenticated_request(
    request: Request, *, mutation: bool = False
) -> AsyncIterator[AuthenticatedRequest]:
    session_token = request.cookies.get(SESSION_COOKIE)
    csrf_token = request.cookies.get(CSRF_COOKIE)
    if session_token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, AUTHENTICATION_REQUIRED_DETAIL
        )
    if mutation:
        _require_mutation_proof(request, csrf_token)
    try:
        view = await request.app.state.container.authentication.current(
            session_token, csrf_token
        )
    except InvalidSession as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL
        ) from exc
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication is not available",
        ) from exc
    if view is None or session_token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, AUTHENTICATION_REQUIRED_DETAIL
        )
    try:
        async with AuthenticatedUnitOfWork(
            request.app.state.container.database.session_factory,
            hash_opaque_token(session_token),
        ) as unit:
            if unit.actor is None or unit.session is None or unit.actor != view.actor:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL
                )
            yield AuthenticatedRequest(
                actor=unit.actor,
                user=view.user,
                session=unit.session,
                session_token=session_token,
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "28000":
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL
            ) from exc
        raise
