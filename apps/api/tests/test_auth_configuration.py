import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_production_disables_docs_and_credentialed_cors() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        cors_origins=[],
        csrf_signing_secret="production-csrf-signing-secret-value",
        coordinator_service_token="c" * 32,
        controller_service_token="d" * 32,
    )
    app = create_app(settings)

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    assert settings.cors_origin_strings == []
    assert settings.allowed_request_origins == {"https://rag.home.arpa"}


def test_production_rejects_development_cors_and_csrf_secret() -> None:
    with pytest.raises(ValidationError, match="CORS"):
        Settings(
            _env_file=None,
            environment="production",
            csrf_signing_secret="production-csrf-signing-secret-value",
        )
    with pytest.raises(ValidationError, match="non-development"):
        Settings(
            _env_file=None,
            environment="production",
            cors_origins=[],
        )


def test_session_and_activation_expiry_contract_is_frozen() -> None:
    settings = Settings(_env_file=None)

    assert settings.session_idle_seconds == 30 * 60
    with pytest.raises(ValueError, match="less than or equal to 1800"):
        Settings(session_idle_seconds=1801)
    assert settings.activation_ttl_seconds == 30 * 60
    assert settings.allowed_request_origins == {
        "https://rag.home.arpa",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
