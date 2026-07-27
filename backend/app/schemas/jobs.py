from pydantic import BaseModel


class BackgroundJobQueued(BaseModel):
    job_id: str
    status: str
    total: int


class BackgroundJobResultItem(BaseModel):
    id: str
    status: str
    message: str | None = None
    error: str | None = None


class BackgroundJobRead(BaseModel):
    id: str
    kind: str
    status: str
    total: int
    processed: int
    succeeded: int
    failed: int
    results: list[BackgroundJobResultItem] = []
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class BackgroundJobList(BaseModel):
    jobs: list[BackgroundJobRead]
