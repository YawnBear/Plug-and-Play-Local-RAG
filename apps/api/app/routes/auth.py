import hashlib
import hmac
import re

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.schemas.auth import (
    ActivationRequest,
    AuthMeResponse,
    AuthSessionResponse,
    LoginRequest,
    PasswordChangeRequest,
)
from app.services.authentication import (
    AuthenticationError,
    AuthenticationUnavailable,
    InvalidActivation,
    InvalidCredentials,
    InvalidSession,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])

SESSION_COOKIE = "rag_session"
PREAUTH_COOKIE = "rag_preauth"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
COOKIE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}")
SESSION_EXPIRED_DETAIL = {
    "code": "session_expired",
    "message": "Your session expired after 30 minutes of inactivity.",
}
AUTHENTICATION_REQUIRED_DETAIL = {
    "code": "authentication_required",
    "message": "Authentication is required.",
}


def _validated_cookie_token(token: str) -> str:
    if COOKIE_TOKEN_PATTERN.fullmatch(token) is None:
        raise RuntimeError("authentication service returned an invalid cookie token")
    return token


def _set_session_cookie(
    response: Response, token: str, *, maximum_age: int, secure: bool
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _validated_cookie_token(token),
        max_age=maximum_age,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _set_csrf_cookie(
    response: Response, token: str, *, maximum_age: int, secure: bool
) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        _validated_cookie_token(token),
        max_age=maximum_age,
        secure=secure,
        httponly=False,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        secure=secure,
        httponly=False,
        samesite="lax",
        path="/",
    )


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin not in request.app.state.settings.allowed_request_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "request origin is not allowed")


def _require_preauth_csrf(request: Request) -> None:
    if not request.app.state.preauth_csrf.valid(
        request.cookies.get(PREAUTH_COOKIE),
        request.headers.get(CSRF_HEADER),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


def _require_session_csrf(request: Request, expected_token: str | None) -> None:
    supplied = request.headers.get(CSRF_HEADER)
    if (
        not expected_token
        or not supplied
        or not hmac.compare_digest(supplied, expected_token)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


def _client_key(request: Request, username: str) -> str:
    address = request.client.host if request.client else "unknown"
    value = f"{address}\0{username.lower()}".encode()
    return hashlib.sha256(value).hexdigest()


def _raise_authentication_error(exc: AuthenticationError) -> None:
    if isinstance(exc, InvalidSession):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL
        ) from exc
    if isinstance(exc, (InvalidCredentials, InvalidActivation)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "authentication failed"
        ) from exc
    if isinstance(exc, AuthenticationUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication is not available",
        ) from exc
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication failed") from exc


@router.get("/me", response_model=AuthMeResponse)
async def current_user(request: Request, response: Response) -> AuthMeResponse:
    session_token = request.cookies.get(SESSION_COOKIE)
    try:
        view = await request.app.state.container.authentication.current(
            session_token,
            request.cookies.get(CSRF_COOKIE),
        )
    except InvalidSession:
        _clear_session_cookie(response, secure=request.app.state.settings.cookie_secure)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL)
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
        raise
    if view is not None:
        response.headers[CSRF_HEADER] = view.csrf_token
        return AuthMeResponse(user=view.user, csrf_token=view.csrf_token)

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
    return AuthMeResponse(user=None, csrf_token=csrf_token)


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    request: Request, response: Response, payload: LoginRequest
) -> AuthSessionResponse:
    _require_origin(request)
    _require_preauth_csrf(request)
    try:
        issued = await request.app.state.container.authentication.login(
            payload.username,
            payload.password,
            _client_key(request, payload.username),
        )
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
        raise
    _set_session_cookie(
        response,
        issued.session_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    _set_csrf_cookie(
        response,
        issued.csrf_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    response.delete_cookie(
        PREAUTH_COOKIE,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
        path="/api",
    )
    response.headers[CSRF_HEADER] = issued.csrf_token
    return AuthSessionResponse(user=issued.user, csrf_token=issued.csrf_token)


@router.post("/activate", response_model=AuthSessionResponse)
async def activate(
    request: Request, response: Response, payload: ActivationRequest
) -> AuthSessionResponse:
    _require_origin(request)
    _require_preauth_csrf(request)
    try:
        issued = await request.app.state.container.authentication.activate(
            payload.code, payload.password
        )
    except (AuthenticationError, ValueError) as exc:
        if isinstance(exc, AuthenticationError):
            _raise_authentication_error(exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _set_session_cookie(
        response,
        issued.session_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    _set_csrf_cookie(
        response,
        issued.csrf_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    response.headers[CSRF_HEADER] = issued.csrf_token
    return AuthSessionResponse(user=issued.user, csrf_token=issued.csrf_token)


@router.post("/password", response_model=AuthSessionResponse)
async def change_password(
    request: Request, response: Response, payload: PasswordChangeRequest
) -> AuthSessionResponse:
    _require_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    try:
        view = await request.app.state.container.authentication.current(
            token, request.cookies.get(CSRF_COOKIE)
        )
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
        raise
    if view is None or token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, AUTHENTICATION_REQUIRED_DETAIL
        )
    _require_session_csrf(request, view.csrf_token)
    try:
        issued = await request.app.state.container.authentication.change_password(
            token, payload.current_password, payload.new_password
        )
    except (AuthenticationError, ValueError) as exc:
        if isinstance(exc, AuthenticationError):
            _raise_authentication_error(exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _set_session_cookie(
        response,
        issued.session_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    _set_csrf_cookie(
        response,
        issued.csrf_token,
        maximum_age=request.app.state.settings.session_idle_seconds,
        secure=request.app.state.settings.cookie_secure,
    )
    response.headers[CSRF_HEADER] = issued.csrf_token
    return AuthSessionResponse(user=issued.user, csrf_token=issued.csrf_token)


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh_session(request: Request, response: Response) -> AuthSessionResponse:
    _require_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    csrf_token = request.cookies.get(CSRF_COOKIE)
    if token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, AUTHENTICATION_REQUIRED_DETAIL
        )
    _require_session_csrf(request, csrf_token)
    try:
        refreshed = await request.app.state.container.authentication.refresh(
            token, csrf_token
        )
    except InvalidSession:
        _clear_session_cookie(response, secure=request.app.state.settings.cookie_secure)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED_DETAIL)
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
        raise
    view = refreshed.view
    if refreshed.refreshed:
        _set_session_cookie(
            response,
            token,
            maximum_age=request.app.state.settings.session_idle_seconds,
            secure=request.app.state.settings.cookie_secure,
        )
        _set_csrf_cookie(
            response,
            view.csrf_token,
            maximum_age=request.app.state.settings.session_idle_seconds,
            secure=request.app.state.settings.cookie_secure,
        )
    response.headers[CSRF_HEADER] = view.csrf_token
    return AuthSessionResponse(user=view.user, csrf_token=view.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    _require_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    try:
        view = await request.app.state.container.authentication.current(
            token, request.cookies.get(CSRF_COOKIE)
        )
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
        raise
    if view is None or token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, AUTHENTICATION_REQUIRED_DETAIL
        )
    _require_session_csrf(request, view.csrf_token)
    try:
        await request.app.state.container.authentication.logout(token)
    except AuthenticationError as exc:
        _raise_authentication_error(exc)
    _clear_session_cookie(response, secure=request.app.state.settings.cookie_secure)
