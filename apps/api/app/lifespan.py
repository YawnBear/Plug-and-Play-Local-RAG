from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.dependencies import ApplicationContainer


def build_lifespan(
    settings: Settings,
    container: ApplicationContainer,
) -> Callable[[FastAPI], AsyncIterator[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            settings.prepare_api_paths()
            startup_readiness = getattr(
                container.database,
                "startup_readiness",
                container.database.readiness,
            )
            readiness = await startup_readiness()
            personal_setup_ready = (
                settings.setup_enabled
                and readiness.database
                and readiness.vector_extension
                and readiness.migration_current
                and readiness.catalog_integrity
                and readiness.identity_valid
                and readiness.bootstrap_required
            )
            if not readiness.ready and not personal_setup_ready:
                raise RuntimeError(readiness.message)
            yield
        finally:
            await container.embedder.close()
            await container.generator.close()
            coordinator = getattr(container, "coordinator", None)
            if coordinator is not None:
                await coordinator.close()
            system_ocr = getattr(container, "system_ocr", None)
            if system_ocr is not None:
                await system_ocr.close()
            controller = getattr(container, "controller", None)
            if controller is not None:
                await controller.close()
            await container.database.dispose()

    return lifespan
