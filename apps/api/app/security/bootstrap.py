import getpass
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security.identity import (
    normalize_display_name,
    normalize_username,
    validate_permanent_password,
)
from app.security.passwords import calibrate_password_hasher
from app.security.tokens import issue_opaque_token


@dataclass(frozen=True, slots=True)
class IssuedSetupCode:
    code: str
    expires_at: datetime


async def bootstrap_first_admin(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    password_hasher: PasswordHasher | None = None,
    password_prompt: Callable[[str], str] = getpass.getpass,
    identity_prompt: Callable[[str], str] = input,
) -> uuid.UUID:
    """Interactively create the single fresh-install administrator."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeError("bootstrap-admin requires an interactive local TTY")
    username = normalize_username(identity_prompt("Owner username: "))
    display_name = normalize_display_name(identity_prompt("Display name: "))
    first = validate_permanent_password(password_prompt("Permanent password: "))
    second = validate_permanent_password(password_prompt("Repeat permanent password: "))
    if first != second:
        raise ValueError("passwords do not match")

    hasher = password_hasher or calibrate_password_hasher()
    password_hash = hasher.hash(first)
    first = ""
    second = ""

    async with session_factory() as session, session.begin():
        admin_id = await session.scalar(
            text("SELECT v4_bootstrap_admin(:username, :display_name, :password_hash)"),
            {
                "username": username,
                "display_name": display_name,
                "password_hash": password_hash,
            },
        )
        if not isinstance(admin_id, uuid.UUID):
            raise RuntimeError("bootstrap function did not return an administrator ID")
        return admin_id


async def issue_owner_setup_code(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lifetime_seconds: int = 15 * 60,
) -> IssuedSetupCode:
    """Rotate the unused Personal setup code and persist only its digest."""
    if lifetime_seconds != 15 * 60:
        raise ValueError("owner setup code lifetime must be 15 minutes")
    issued = issue_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=lifetime_seconds)
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT v8_issue_setup_code(:code_hash, :expires_at)"),
                {"code_hash": issued.digest, "expires_at": expires_at},
            )
    except SQLAlchemyError as exc:
        raise RuntimeError("owner setup code could not be issued") from exc
    return IssuedSetupCode(code=issued.plaintext, expires_at=expires_at)
