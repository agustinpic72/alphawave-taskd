from pydantic import BaseModel, ConfigDict


class BriefingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    briefing_date: str
    scheduled_for: str
    sent_at: str | None
    status: str
    reason: str | None
    channel: str
    payload_json: str | None
    created_at: str
    updated_at: str


class BriefingStatus(BaseModel):
    enabled: bool
    timezone: str
    time: str
    late_cutoff: str
    today_sent: bool
    last_run: BriefingRunRead | None = None


class BriefingGenerateRequest(BaseModel):
    date: str | None = None
    manual: bool = False


class BriefingPayload(BaseModel):
    briefing_date: str
    text: str
    groups: dict[str, list[dict]]


class BriefingRuns(BaseModel):
    runs: list[BriefingRunRead]
