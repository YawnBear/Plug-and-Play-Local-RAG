from uuid import UUID

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    stage: str
    completed_units: int
    total_units: int | None
    error: str | None
