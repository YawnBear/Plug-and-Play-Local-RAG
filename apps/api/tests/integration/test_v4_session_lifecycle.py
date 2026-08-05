import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import Settings

pytestmark = pytest.mark.integration

_CONFIRMATION = "V4-SECURITY-DEDICATED-ONLY"
_LOCK_KEY = 7_418_114_725_462_311_904


def _run(coroutine: object) -> object:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)


def _environment() -> tuple[str, str]:
    if os.environ.get("RUN_V4_SECURITY_E2E") != "1":
        pytest.skip("RUN_V4_SECURITY_E2E is not enabled")
    url = os.environ.get("V4_SECURITY_TEST_DATABASE_ADMIN_URL", "")
    name = os.environ.get("V4_SECURITY_TEST_DATABASE_NAME", "")
    confirmation = os.environ.get("V4_SECURITY_DEDICATED_DATABASE_CONFIRM", "")
    if not url or not name:
        pytest.skip("the V4 security test database is not configured")
    if confirmation != _CONFIRMATION:
        pytest.fail(f"expected dedicated-database confirmation {_CONFIRMATION!r}")
    if make_url(url).database != name or not name.startswith("rag_v4_security_"):
        pytest.fail("V4 security URL must target the named disposable database")
    if make_url(Settings().database_url).database == name:
        pytest.fail("refusing to run V4 security integration against DATABASE_URL")
    return url, name


async def _session_call(
    connection: AsyncConnection,
    function_name: str,
    token: str,
    csrf: str,
) -> object | None:
    await connection.execute(text("SET LOCAL ROLE rag_api"))
    if function_name == "v4_session_view":
        statement = text("SELECT * FROM v4_session_view(:token)")
        parameters = {"token": token}
    else:
        statement = text(
            "SELECT * FROM v4_refresh_session("
            ":token, :csrf, statement_timestamp() + interval '30 minutes')"
        )
        parameters = {"token": token, "csrf": csrf}
    return (await connection.execute(statement, parameters)).first()


def test_v45_database_session_lifecycle_contract() -> None:
    async def exercise() -> None:
        url, database_name = _environment()
        engine = create_async_engine(url)
        coordinator = await engine.connect()
        user_ids = [uuid.uuid4() for _ in range(6)]
        (
            admin_id,
            member_id,
            disabled_id,
            deleted_id,
            auth_changed_id,
            ordinary_id,
        ) = user_ids
        tokens = {
            "view": "1" * 64,
            "refresh": "2" * 64,
            "expired": "3" * 64,
            "revoked": "4" * 64,
            "disabled": "5" * 64,
            "deleted": "6" * 64,
            "auth_changed": "7" * 64,
            "auth_epoch": "8" * 64,
            "session_epoch": "9" * 64,
            "admin": "a" * 64,
            "member": "b" * 64,
            "concurrent": "c" * 64,
        }
        csrf = "d" * 64
        try:
            assert await coordinator.scalar(text("SELECT current_database()")) == (
                database_name
            )
            assert (
                await coordinator.scalar(text("SELECT current_setting('is_superuser')"))
                == "on"
            )
            assert (
                await coordinator.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                == "0006_versioned_claim"
            )
            await coordinator.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY}
            )
            await coordinator.commit()
            if await coordinator.scalar(text("SELECT count(*) FROM users")):
                pytest.fail("refusing reused V4 security database with existing users")
            await coordinator.commit()

            async with coordinator.begin():
                epoch = (
                    await coordinator.execute(
                        text(
                            "SELECT authentication_version, session_epoch "
                            "FROM security_epochs WHERE singleton"
                        )
                    )
                ).one()
                await coordinator.execute(text("SET LOCAL ROLE rag_owner"))
                await coordinator.execute(
                    text(
                        "INSERT INTO users ("
                        "id, username, display_name, role, status, password_hash, "
                        "authentication_version, deleted_at"
                        ") VALUES "
                        "(:admin, 'session.admin', 'Session Admin', 'admin', "
                        "'active', '$argon2id$test', 1, NULL), "
                        "(:member, 'session.member', 'Session Member', 'member', "
                        "'active', '$argon2id$test', 1, NULL), "
                        "(:disabled, 'session.disabled', 'Disabled', 'member', "
                        "'disabled', '$argon2id$test', 1, NULL), "
                        "(:deleted, 'session.deleted', 'Deleted', 'member', "
                        "'deleted', NULL, 1, statement_timestamp()), "
                        "(:auth_changed, 'session.changed', 'Changed', 'member', "
                        "'active', '$argon2id$test', 2, NULL), "
                        "(:ordinary, 'session.ordinary', 'Ordinary', 'member', "
                        "'active', '$argon2id$test', 1, NULL)"
                    ),
                    {
                        "admin": admin_id,
                        "member": member_id,
                        "disabled": disabled_id,
                        "deleted": deleted_id,
                        "auth_changed": auth_changed_id,
                        "ordinary": ordinary_id,
                    },
                )

                async def insert_session(
                    *,
                    token: str,
                    user_id: uuid.UUID,
                    issued_minutes: int,
                    last_seen_minutes: int,
                    expiry_minutes: int,
                    revoked: bool = False,
                    auth_version: int = 1,
                    auth_epoch: int | None = None,
                    session_epoch: uuid.UUID | None = None,
                    recent_minutes: int | None = None,
                ) -> None:
                    await coordinator.execute(
                        text(
                            "INSERT INTO sessions ("
                            "id, user_id, token_hash, csrf_token_hash, "
                            "issued_authentication_version, "
                            "issued_authentication_epoch, issued_session_epoch, "
                            "issued_at, last_seen_at, idle_expires_at, "
                            "absolute_expires_at, recent_reauthenticated_at, "
                            "revoked_at"
                            ") VALUES ("
                            "gen_random_uuid(), :user_id, :token, :csrf, "
                            ":auth_version, :auth_epoch, :session_epoch, "
                            "statement_timestamp() + "
                            "(:issued * interval '1 minute'), "
                            "statement_timestamp() + "
                            "(:last_seen * interval '1 minute'), "
                            "statement_timestamp() + "
                            "(:expiry * interval '1 minute'), "
                            "statement_timestamp() + "
                            "(:expiry * interval '1 minute'), "
                            "CASE WHEN CAST(:recent AS integer) IS NULL THEN NULL ELSE "
                            "statement_timestamp() + "
                            "(CAST(:recent AS integer) * interval '1 minute') END, "
                            "CASE WHEN :revoked THEN statement_timestamp() "
                            "ELSE NULL END)"
                        ),
                        {
                            "user_id": user_id,
                            "token": token,
                            "csrf": csrf,
                            "auth_version": auth_version,
                            "auth_epoch": (
                                epoch.authentication_version
                                if auth_epoch is None
                                else auth_epoch
                            ),
                            "session_epoch": session_epoch or epoch.session_epoch,
                            "issued": issued_minutes,
                            "last_seen": last_seen_minutes,
                            "expiry": expiry_minutes,
                            "recent": recent_minutes,
                            "revoked": revoked,
                        },
                    )

                await insert_session(
                    token=tokens["view"],
                    user_id=ordinary_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                )
                await insert_session(
                    token=tokens["refresh"],
                    user_id=ordinary_id,
                    issued_minutes=-6,
                    last_seen_minutes=-6,
                    expiry_minutes=24,
                )
                await insert_session(
                    token=tokens["expired"],
                    user_id=ordinary_id,
                    issued_minutes=-40,
                    last_seen_minutes=-31,
                    expiry_minutes=-1,
                )
                await insert_session(
                    token=tokens["revoked"],
                    user_id=ordinary_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                    revoked=True,
                )
                await insert_session(
                    token=tokens["disabled"],
                    user_id=disabled_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                )
                await insert_session(
                    token=tokens["deleted"],
                    user_id=deleted_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                )
                await insert_session(
                    token=tokens["auth_changed"],
                    user_id=auth_changed_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                    auth_version=1,
                )
                await insert_session(
                    token=tokens["auth_epoch"],
                    user_id=ordinary_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                    auth_epoch=epoch.authentication_version + 1,
                )
                await insert_session(
                    token=tokens["session_epoch"],
                    user_id=ordinary_id,
                    issued_minutes=-10,
                    last_seen_minutes=-10,
                    expiry_minutes=20,
                    session_epoch=uuid.uuid4(),
                )
                await insert_session(
                    token=tokens["admin"],
                    user_id=admin_id,
                    issued_minutes=-20,
                    last_seen_minutes=-20,
                    expiry_minutes=10,
                    recent_minutes=-20,
                )
                await insert_session(
                    token=tokens["member"],
                    user_id=member_id,
                    issued_minutes=-20,
                    last_seen_minutes=-20,
                    expiry_minutes=10,
                    recent_minutes=-20,
                )
                await insert_session(
                    token=tokens["concurrent"],
                    user_id=ordinary_id,
                    issued_minutes=-6,
                    last_seen_minutes=-6,
                    expiry_minutes=24,
                )

            before = await coordinator.scalar(
                text("SELECT last_seen_at FROM sessions WHERE token_hash = :token"),
                {"token": tokens["view"]},
            )
            async with engine.begin() as connection:
                assert (
                    await _session_call(
                        connection, "v4_session_view", tokens["view"], csrf
                    )
                    is not None
                )
            after = await coordinator.scalar(
                text("SELECT last_seen_at FROM sessions WHERE token_hash = :token"),
                {"token": tokens["view"]},
            )
            assert after == before

            async with engine.begin() as connection:
                refreshed = await _session_call(
                    connection, "v4_refresh_session", tokens["refresh"], csrf
                )
                assert refreshed is not None and refreshed.refreshed is True
            deadline = (
                await coordinator.execute(
                    text(
                        "SELECT idle_expires_at, absolute_expires_at "
                        "FROM sessions WHERE token_hash = :token"
                    ),
                    {"token": tokens["refresh"]},
                )
            ).one()
            assert deadline.idle_expires_at == deadline.absolute_expires_at
            async with engine.begin() as connection:
                duplicate = await _session_call(
                    connection, "v4_refresh_session", tokens["refresh"], csrf
                )
                assert duplicate is not None and duplicate.refreshed is False

            invalid_names = (
                "expired",
                "revoked",
                "disabled",
                "deleted",
                "auth_changed",
                "auth_epoch",
                "session_epoch",
            )
            for name in invalid_names:
                async with engine.begin() as connection:
                    assert (
                        await _session_call(
                            connection, "v4_session_view", tokens[name], csrf
                        )
                        is None
                    )
                async with engine.begin() as connection:
                    assert (
                        await _session_call(
                            connection, "v4_refresh_session", tokens[name], csrf
                        )
                        is None
                    )

            async def concurrent_refresh() -> bool:
                async with engine.begin() as connection:
                    row = await _session_call(
                        connection,
                        "v4_refresh_session",
                        tokens["concurrent"],
                        csrf,
                    )
                    assert row is not None
                    return bool(row.refreshed)

            results = await asyncio.gather(concurrent_refresh(), concurrent_refresh())
            assert sorted(results) == [False, True]

            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL ROLE rag_api"))
                await connection.execute(
                    text("SELECT v4_activate_actor(:token)"),
                    {"token": tokens["admin"]},
                )
                with pytest.raises(DBAPIError) as missing_node:
                    async with connection.begin_nested():
                        await connection.execute(
                            text("SELECT v4_admin_access_context(:node_id)"),
                            {"node_id": uuid.uuid4()},
                        )
                assert getattr(missing_node.value.orig, "sqlstate", None) == "P0002"
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL ROLE rag_api"))
                await connection.execute(
                    text("SELECT v4_activate_actor(:token)"),
                    {"token": tokens["member"]},
                )
                with pytest.raises(DBAPIError) as denied:
                    async with connection.begin_nested():
                        await connection.execute(
                            text("SELECT v4_admin_access_context(:node_id)"),
                            {"node_id": uuid.uuid4()},
                        )
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        finally:
            try:
                if coordinator.in_transaction():
                    await coordinator.rollback()
                async with coordinator.begin():
                    await coordinator.execute(text("SET LOCAL ROLE rag_owner"))
                    await coordinator.execute(
                        text("DELETE FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"),
                        {"ids": user_ids},
                    )
            finally:
                await coordinator.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY}
                )
                await coordinator.close()
                await engine.dispose()

    _run(exercise())
