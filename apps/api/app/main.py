from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, get_settings
from app.dependencies import ApplicationContainer
from app.lifespan import build_lifespan
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.chats import router as chats_router
from app.routes.controller import router as controller_router
from app.routes.documents import router as documents_router
from app.routes.jobs import router as jobs_router
from app.routes.library import account_router
from app.routes.library import router as library_router
from app.routes.query import router as query_router
from app.routes.search import router as search_router
from app.routes.setup import router as setup_router
from app.routes.system import router as system_router
from app.runtime.maintenance_gate import MaintenanceGate, MaintenanceGateMiddleware
from app.schemas.health import ReadinessResponse
from app.services.authentication import PreAuthCsrf


def create_app(
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_container = container or ApplicationContainer.from_settings(
        resolved_settings
    )
    application = FastAPI(
        title="RAG API",
        lifespan=build_lifespan(resolved_settings, resolved_container),
        docs_url=None if resolved_settings.environment == "production" else "/docs",
        redoc_url=None if resolved_settings.environment == "production" else "/redoc",
        openapi_url=(
            None if resolved_settings.environment == "production" else "/openapi.json"
        ),
    )
    application.state.container = resolved_container
    application.state.settings = resolved_settings
    application.state.preauth_csrf = PreAuthCsrf(
        resolved_settings.csrf_signing_secret.get_secret_value()
    )
    maintenance_gate = MaintenanceGate()
    application.state.maintenance_gate = maintenance_gate
    application.add_middleware(MaintenanceGateMiddleware, gate=maintenance_gate)
    if resolved_settings.environment == "production":
        allowed_hosts = (
            ["127.0.0.1", "localhost", "::1"]
            if resolved_settings.product_profile == "personal"
            else [resolved_settings.canonical_host]
        )
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    elif resolved_settings.cors_origin_strings:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origin_strings,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.include_router(auth_router)
    application.include_router(setup_router)
    application.include_router(admin_router)
    application.include_router(system_router)
    application.include_router(controller_router)
    application.include_router(documents_router)
    application.include_router(chats_router)
    application.include_router(jobs_router)
    application.include_router(library_router)
    application.include_router(account_router)
    application.include_router(query_router)
    application.include_router(search_router)

    @application.exception_handler(HTTPException)
    async def http_exception_response(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        response = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "session_expired":
            response.delete_cookie(
                "rag_session",
                secure=resolved_settings.cookie_secure,
                httponly=True,
                samesite="lax",
                path="/",
            )
            response.delete_cookie(
                "csrf_token",
                secure=resolved_settings.cookie_secure,
                httponly=False,
                samesite="lax",
                path="/",
            )
        return response

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", response_model=ReadinessResponse)
    async def ready(request: Request) -> ReadinessResponse | JSONResponse:
        if resolved_settings.environment == "production" and (
            request.client is None or request.client.host not in {"127.0.0.1", "::1"}
        ):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "not found"},
            )
        readiness = await request.app.state.container.readiness.check()
        response = ReadinessResponse.from_state(readiness)
        response.deployment_id = resolved_settings.deployment_id
        if not response.ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=response.model_dump(),
            )
        return response

    return application


app = create_app()
