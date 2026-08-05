from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.actor import ActorContext, ActorRole
from app.services.system import SystemCapabilityDenied


class MemberDeniedSystem:
    @staticmethod
    def denied(actor: ActorContext) -> None:
        assert actor.role is ActorRole.MEMBER
        raise SystemCapabilityDenied

    async def overview(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def capabilities(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def configuration(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def version_inventory(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def select_ingestion_profile(
        self, actor: ActorContext, _token: str, _selection: object
    ):
        self.denied(actor)

    async def preview_reprocessing(
        self, actor: ActorContext, _token: str, _selection: object
    ):
        self.denied(actor)

    async def reauthenticate_reprocessing(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def start_reprocessing(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def reprocessing_operations(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def control_reprocessing(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def rollback_embedding_generation(
        self, actor: ActorContext, _token: str, _generation_id: object
    ):
        self.denied(actor)

    async def cleanup_generation(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def configuration_changes(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def start_personal_backup(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def personal_backup_status(self, actor: ActorContext, _token: str):
        self.denied(actor)

    async def personal_backup_history(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def preview_configuration(
        self, actor: ActorContext, _token: str, _selection: object
    ):
        self.denied(actor)

    async def reauthenticate_configuration(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def apply_configuration(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    async def operations(self, actor: ActorContext, _token: str, **_values: object):
        self.denied(actor)

    async def run_profile_operation(
        self, actor: ActorContext, _token: str, **_values: object
    ):
        self.denied(actor)

    def diagnostics_preview(self, actor: ActorContext):
        self.denied(actor)

    async def diagnostics_archive(self, actor: ActorContext, _token: str):
        self.denied(actor)


def test_every_system_route_rejects_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = ActorContext(uuid4(), ActorRole.MEMBER, 1, 1, uuid4())
    mutation_flags: list[bool] = []

    @asynccontextmanager
    async def request_auth(_request: object, *, mutation: bool = False):
        mutation_flags.append(mutation)
        yield SimpleNamespace(actor=actor, session_token="opaque")

    monkeypatch.setattr("app.routes.system.authenticated_request", request_auth)
    application = create_app(
        Settings(environment="test"),
        container=SimpleNamespace(system=MemberDeniedSystem()),
    )
    client = TestClient(application, base_url="https://rag.home.arpa")
    operation_id = uuid4()
    profile_id = "ocr.paddleocr-vl-1.6.cpu.windows-x64"

    gets = [
        "/api/admin/system/overview",
        "/api/admin/system/capabilities",
        "/api/admin/system/configuration",
        "/api/admin/system/versions",
        "/api/admin/system/reprocessing",
        "/api/admin/system/configuration/changes",
        "/api/admin/system/backups/latest",
        "/api/admin/system/backups",
        "/api/admin/system/operations",
        f"/api/admin/system/operations/{operation_id}",
        "/api/admin/system/diagnostics/preview",
    ]
    posts = [
        f"/api/admin/system/profiles/{profile_id}/validate",
        f"/api/admin/system/profiles/{profile_id}/benchmark",
        "/api/admin/system/diagnostics/export",
        "/api/admin/system/backups",
    ]
    for path in gets:
        assert client.get(path).status_code == 403
    for path in posts:
        assert client.post(path).status_code == 403
    selection = {
        "base_revision": "v8d-baseline-0001",
        "generation_profile_id": "generation.qwen3-8b.ollama.windows-x64",
        "reranker_profile_id": "reranking.bge-v2-m3.cpu.windows-x64",
        "ocr_mode": "explicit",
        "ocr_profile_id": profile_id,
        "ocr_cpu_threads": 8,
        "ocr_process_count": 2,
    }
    preview_id = str(uuid4())
    digest = "a" * 64
    assert (
        client.post(
            "/api/admin/system/configuration/preview", json=selection
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/system/configuration/reauthenticate",
            json={
                "preview_id": preview_id,
                "impact_digest": digest,
                "password": "password",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/system/configuration/apply",
            json={
                "preview_id": preview_id,
                "impact_digest": digest,
                "reauthentication_grant": "g" * 43,
            },
        ).status_code
        == 403
    )
    reprocessing_preview_id = str(uuid4())
    generation_id = uuid4()
    assert (
        client.put(
            "/api/admin/system/versions/ingestion",
            json={
                "base_revision": "v8e-ingestion-initial",
                "parser_profile_id": "parser.paddleocr-vl-1.6.adaptive-v2",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/system/reprocessing/preview",
            json={
                "operation_type": "reindex",
                "target_profile_id": "embedding.qwen3-0.6b-1024.ollama.windows-x64",
                "source_parser_version": None,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/system/reprocessing/reauthenticate",
            json={
                "preview_id": reprocessing_preview_id,
                "impact_digest": digest,
                "password": "password",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/system/reprocessing",
            json={
                "preview_id": reprocessing_preview_id,
                "impact_digest": digest,
                "reauthentication_grant": "g" * 43,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/admin/system/reprocessing/{operation_id}/pause"
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/admin/system/generations/embedding/{generation_id}/rollback"
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/admin/system/generations/document/{generation_id}"
        ).status_code
        == 403
    )
    assert mutation_flags == [False] * len(gets) + [True] * (len(posts) + 10)
