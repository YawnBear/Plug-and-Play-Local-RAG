import ipaddress
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.schemas.setup import (
    SetupChallengeRequest,
    SetupChallengeResponse,
    SetupOwnerRequest,
    SetupOwnerResponse,
    SetupStatusResponse,
)
from app.services.setup import (
    SetupCodeExpired,
    SetupCodeLocked,
    SetupCodeRejected,
    SetupError,
    SetupState,
    SetupUnavailable,
)

router = APIRouter(prefix="/api/setup", tags=["owner setup"])

PREAUTH_COOKIE = "rag_preauth"
SETUP_CHALLENGE_COOKIE = "rag_setup_challenge"
CSRF_HEADER = "X-CSRF-Token"


def _require_setup_client(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.setup_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from exc
    if settings.product_profile == "personal" and address.is_loopback:
        return
    if (
        settings.product_profile == "team_lan_preview_unsigned"
        and address == settings.rag_lan_ipv4
    ):
        return
    raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
def _require_origin(request: Request) -> None:
    allowed = request.app.state.settings.allowed_request_origins
    if request.headers.get("origin") not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "request origin is not allowed")


def _require_preauth_csrf(request: Request) -> None:
    if not request.app.state.preauth_csrf.valid(
        request.cookies.get(PREAUTH_COOKIE), request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


def _issue_preauth_csrf(request: Request, response: Response) -> None:
    binding, csrf_token = request.app.state.preauth_csrf.issue()
    response.set_cookie(
        PREAUTH_COOKIE,
        binding,
        max_age=request.app.state.settings.preauth_csrf_ttl_seconds,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
        path="/api",
    )
    response.headers[CSRF_HEADER] = csrf_token


def _clear_setup_cookies(request: Request, response: Response) -> None:
    secure = request.app.state.settings.cookie_secure
    response.delete_cookie(
        SETUP_CHALLENGE_COOKIE,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/api/setup",
    )
    response.delete_cookie(
        PREAUTH_COOKIE,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/api",
    )


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status(request: Request, response: Response) -> SetupStatusResponse:
    _require_setup_client(request)
    try:
        current = await request.app.state.container.setup.status()
    except SetupError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "owner setup is unavailable"
        ) from exc
    if current.state is SetupState.REQUIRED:
        _issue_preauth_csrf(request, response)
    now = datetime.now(UTC)
    code_issued = bool(
        current.state is SetupState.REQUIRED
        and current.code_expires_at
        and current.code_expires_at > now
        and current.attempts_remaining > 0
    )
    return SetupStatusResponse(
        state=current.state.value,
        code_issued=code_issued,
        code_expires_at=current.code_expires_at if code_issued else None,
        attempts_remaining=(
            current.attempts_remaining if current.state is SetupState.REQUIRED else 0
        ),
    )


@router.post("/challenge", response_model=SetupChallengeResponse)
async def setup_challenge(
    request: Request,
    response: Response,
    payload: SetupChallengeRequest,
) -> SetupChallengeResponse:
    _require_setup_client(request)
    _require_origin(request)
    _require_preauth_csrf(request)
    try:
        challenge = await request.app.state.container.setup.challenge(payload.code)
    except SetupCodeLocked as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "setup code attempts are exhausted; issue a new local setup code",
        ) from exc
    except SetupCodeExpired as exc:
        raise HTTPException(
            status.HTTP_410_GONE,
            "setup code expired; issue a new local setup code",
        ) from exc
    except SetupCodeRejected as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "setup code was not accepted"
        ) from exc
    except SetupUnavailable as exc:
        raise HTTPException(status.HTTP_410_GONE, "owner setup is unavailable") from exc
    remaining = challenge.expires_at - datetime.now(UTC)
    maximum_age = max(1, int(remaining.total_seconds()))
    response.set_cookie(
        SETUP_CHALLENGE_COOKIE,
        challenge.token,
        max_age=maximum_age,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/setup",
    )
    return SetupChallengeResponse(expires_at=challenge.expires_at)


@router.post("/owner", response_model=SetupOwnerResponse)
async def setup_owner(
    request: Request,
    response: Response,
    payload: SetupOwnerRequest,
) -> SetupOwnerResponse:
    _require_setup_client(request)
    _require_origin(request)
    _require_preauth_csrf(request)
    challenge_token = request.cookies.get(SETUP_CHALLENGE_COOKIE)
    if not challenge_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "setup challenge is required")
    try:
        await request.app.state.container.setup.complete_owner(
            challenge_token=challenge_token,
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SetupUnavailable as exc:
        raise HTTPException(status.HTTP_410_GONE, "owner setup is unavailable") from exc
    _clear_setup_cookies(request, response)
    return SetupOwnerResponse()
