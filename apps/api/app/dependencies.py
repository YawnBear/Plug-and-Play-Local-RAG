from dataclasses import dataclass

from app.config import Settings
from app.db.session import DatabaseManager
from app.runtime.controller_client import ControllerClient
from app.runtime.coordinator_client import (
    CoordinatorClient,
    CoordinatorEmbeddingClient,
    CoordinatorGenerationClient,
    CoordinatorReranker,
)
from app.runtime.ocr_client import OcrServiceClient
from app.services.authentication import (
    AuthenticationService,
    DatabaseAuthenticationGateway,
)
from app.services.authorization import AuthorizationService, DatabaseAdminGateway
from app.services.chats import ChatService
from app.services.document_content import DocumentContentService
from app.services.document_reingest import DocumentReingestService
from app.services.documents import DocumentService
from app.services.library import LibraryService
from app.services.object_storage import S3ObjectStore
from app.services.ollama_embeddings import OllamaEmbeddingClient
from app.services.ollama_generation import OllamaGenerationClient
from app.services.rag import RagService
from app.services.readiness import ReadinessService
from app.services.reranker import BgeReranker
from app.services.retrieval import RetrievalService
from app.services.search import SearchService
from app.services.setup import DatabaseSetupGateway, SetupService
from app.services.system import DatabaseSystemGateway, SystemService


@dataclass(slots=True)
class ApplicationContainer:
    database: DatabaseManager
    documents: DocumentService
    document_reingest: DocumentReingestService
    document_content: DocumentContentService
    chats: ChatService
    library: LibraryService
    object_store: S3ObjectStore
    embedder: OllamaEmbeddingClient
    retrieval: RetrievalService
    reranker: BgeReranker
    generator: OllamaGenerationClient
    rag: RagService
    search: SearchService
    readiness: ReadinessService
    authentication: AuthenticationService
    authorization: AuthorizationService
    setup: SetupService
    system: SystemService
    coordinator: CoordinatorClient | None = None
    system_ocr: OcrServiceClient | None = None
    controller: ControllerClient | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ApplicationContainer":
        database = DatabaseManager.from_settings(settings)
        object_store = S3ObjectStore.from_settings(settings)
        coordinator: CoordinatorClient | None = None
        if settings.environment == "production":
            coordinator = CoordinatorClient(
                str(settings.coordinator_base_url),
                settings.coordinator_service_token.get_secret_value(),
                timeout_seconds=settings.generation_timeout_seconds,
            )
            embedder = CoordinatorEmbeddingClient(coordinator)
            reranker = CoordinatorReranker(coordinator)
            generator = CoordinatorGenerationClient(
                coordinator,
                generation_model=settings.generation_model,
                embedding_model=settings.embedding_model,
            )
        else:
            embedder = OllamaEmbeddingClient(
                str(settings.ollama_base_url), settings.embedding_model
            )
            reranker = BgeReranker(settings.reranker_model)
            generator = OllamaGenerationClient(
                str(settings.ollama_base_url),
                settings.generation_model,
                context_size=settings.maximum_generation_context,
                output_tokens=settings.maximum_generation_output,
                timeout_seconds=settings.generation_timeout_seconds,
            )
        retrieval = RetrievalService(database.session_factory, embedder)
        library = LibraryService(database.session_factory)
        ocr_token = settings.ocr_service_token.get_secret_value()
        system_ocr = (
            OcrServiceClient(
                str(settings.ocr_service_base_url),
                ocr_token,
                timeout_seconds=settings.ocr_timeout_seconds,
            )
            if ocr_token
            else None
        )
        readiness = ReadinessService(
            database, generator, reranker, object_store, settings
        )
        authentication = AuthenticationService(
            DatabaseAuthenticationGateway(
                database.session_factory,
                session_idle_seconds=settings.session_idle_seconds,
            ),
            maximum_hash_concurrency=settings.password_hash_concurrency,
        )
        controller_token = settings.controller_service_token.get_secret_value()
        controller = (
            ControllerClient(
                str(settings.controller_base_url),
                controller_token,
            )
            if controller_token
            else None
        )
        system = SystemService(
            DatabaseSystemGateway(database.session_factory),
            settings,
            readiness,
            generator,
            embedder,
            reranker,
            ocr=system_ocr,
            authentication=authentication,
            controller=controller,
        )
        return cls(
            database=database,
            documents=DocumentService(settings, object_store),
            document_reingest=DocumentReingestService(settings, object_store),
            document_content=DocumentContentService(object_store),
            chats=ChatService(database.session_factory, retrieval, reranker, generator),
            object_store=object_store,
            embedder=embedder,
            retrieval=retrieval,
            reranker=reranker,
            generator=generator,
            rag=RagService(retrieval, reranker, generator, library),
            search=SearchService(),
            library=library,
            readiness=readiness,
            authentication=authentication,
            authorization=AuthorizationService(
                DatabaseAdminGateway(database.session_factory)
            ),
            setup=SetupService(
                DatabaseSetupGateway(database.session_factory),
                challenge_ttl_seconds=settings.setup_challenge_ttl_seconds,
                maximum_hash_concurrency=settings.password_hash_concurrency,
            ),
            system=system,
            coordinator=coordinator,
            system_ocr=system_ocr,
            controller=controller,
        )
