from __future__ import annotations

from pydantic import BaseModel

from app.schemas.tasks import TaskRead


class PriorityFactor(BaseModel):
    criterion: str
    label: str
    contribution: float
    direction: str
    reason: str


class PriorityExplanation(BaseModel):
    task_id: str
    score: float
    priority_band: str
    factors: list[PriorityFactor]
    summary: str
    warnings: list[str] = []


class PlanItem(BaseModel):
    task: TaskRead
    reason: str
    priority: PriorityExplanation | None = None


class PlanGroup(BaseModel):
    key: str
    title: str
    items: list[PlanItem]
    summary: str = ""


class PlanRecommendation(BaseModel):
    kind: str
    task: TaskRead | None = None
    title: str
    reason: str
    priority: PriorityExplanation | None = None


class TodayPlan(BaseModel):
    groups: list[PlanGroup]
    summary: str = ""
    recommendations: list[PlanRecommendation] = []
    warnings: list[str] = []


class NowPlan(BaseModel):
    recommended: list[PlanItem]
    afterwards: list[PlanItem]
    avoid: list[PlanItem]
    summary: str = ""
    alternatives: list[PlanRecommendation] = []
    warnings: list[str] = []


class InboxSuggestion(BaseModel):
    task: TaskRead
    suggested_scope: str
    confidence: float
    reason: str


class InboxSuggestions(BaseModel):
    suggestions: list[InboxSuggestion]


class SortProposalItem(BaseModel):
    task_id: str
    old_index: int
    new_index: int
    title: str
    scope: str
    score: float
    reason: str
    priority: PriorityExplanation | None = None


class SortProposal(BaseModel):
    confirmation_id: str
    task_ids: list[str]
    tasks: list[TaskRead]
    expires_at: str
    items: list[SortProposalItem] = []
    summary: str = ""
    requires_confirmation: bool = True


class SortApplyRequest(BaseModel):
    confirmation_id: str
