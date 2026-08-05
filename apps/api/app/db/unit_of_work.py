import re
from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security.actor import ActorContext, ActorRole


class AuthenticatedUnitOfWork:
    """One protected transaction whose actor is derived from a database session."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        token_hash: str,
    ) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", token_hash) is None:
            raise ValueError("token_hash must be a SHA-256 hexadecimal digest")
        self._session_factory = session_factory
        self._token_hash = token_hash
        self.session: AsyncSession | None = None
        self.actor: ActorContext | None = None
        self._transaction: object | None = None

    async def __aenter__(self) -> "AuthenticatedUnitOfWork":
        self.session = self._session_factory()
        self._transaction = await self.session.begin()
        try:
            row = (
                await self.session.execute(
                    text(
                        "SELECT user_id, actor_role, authentication_version, "
                        "authorization_version, session_id "
                        "FROM v4_activate_actor(:token_hash)"
                    ),
                    {"token_hash": self._token_hash},
                )
            ).one()
            self.actor = ActorContext(
                user_id=row.user_id,
                role=ActorRole(row.actor_role),
                authentication_version=row.authentication_version,
                authorization_version=row.authorization_version,
                session_id=row.session_id,
            )
        except BaseException:
            await self.session.rollback()
            await self.session.close()
            self.session = None
            self._transaction = None
            raise
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exception_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None
            self.actor = None
            self._transaction = None
