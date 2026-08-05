from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from app.schemas.jobs import JobStatusResponse
from app.security.request_auth import authenticated_request

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(request: Request, job_id: UUID) -> JobStatusResponse:
    async with authenticated_request(request) as auth:
        job = (
            await auth.session.execute(
                text("SELECT * FROM v4_get_job(:job_id)"),
                {"job_id": job_id},
            )
        ).one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobStatusResponse(
        job_id=job.job_id,
        document_id=job.document_id,
        status=job.status,
        stage=job.stage,
        completed_units=job.completed_units,
        total_units=job.total_units,
        error=job.error,
    )
