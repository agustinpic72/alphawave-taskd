import httpx
import sqlite3
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.confirmations import PendingConfirmation
from app.schemas.briefing import BriefingGenerateRequest, BriefingPayload, BriefingRuns, BriefingStatus
from app.schemas.auth import CsrfResponse, LoginRequest, LoginResponse, SessionResponse
from app.schemas.confirmations import ConfirmationBulkRequest, ConfirmationBulkResponse, ConfirmationBulkResult, ConfirmationList, ConfirmationRead
from app.schemas.jobs import BackgroundJobList, BackgroundJobQueued, BackgroundJobRead
from app.schemas.llm import (
    LLMStatus,
    LLMValidateResponse,
    OpenAICredentialsRequest,
    OpenAIModelCatalog,
    OpenAISettingsRequest,
)
from app.schemas.planning import InboxSuggestions, NowPlan, SortApplyRequest, SortProposal, TodayPlan
from app.schemas.reminders import ReminderCreate, ReminderList, ReminderRead, ReminderUpdate
from app.schemas.settings import (
    SettingsResetRequest,
    SettingsResponse,
    TrelloBoardCreate,
    TrelloBoardPatch,
    TrelloDiscoveryResponse,
    TrelloValidationResponse,
)
from app.schemas.tasks import (
    BulkTaskCreate,
    BulkTaskIdsRequest,
    BulkTaskSuggestionApplyRequest,
    BulkTaskSuggestionApplyResponse,
    BulkTaskSuggestionRequest,
    BulkTaskSuggestionResponse,
    HealthResponse,
    IncompleteTaskList,
    ManualTaskDetailsResponse,
    ManualTaskDetailsUpdate,
    ReorderRequest,
    TaskDetailGapsResponse,
    TaskCreate,
    TaskList,
    TaskRead,
    TaskSnoozeRequest,
    TaskSuggestionApplyRequest,
    TaskSuggestionApplyResponse,
    TaskSuggestionConfirmationResponse,
    TaskSuggestionRequest,
    TaskSuggestionResponse,
    TrelloCardLinkRequest,
    TaskUpdate,
    TrashDeleteResult,
    TrashRestoreResult,
)
from app.schemas.telegram import SimulateTelegramMessageRequest, TelegramProcessResponse
from app.schemas.system import BackupCreateResponse, BackupList, BackupRestorePlan, BackupRestoreRequest, BackupRestoreResponse, BackupStatus, BackupValidateResponse, InstallationReadiness, PreflightReport, SystemHealth, SystemInfo, SystemStatus
from app.schemas.trello import TrelloStatus, TrelloSyncSummary
from app.schemas.trello import TrelloCreateCardNow, TrelloCreateCardProposal, TrelloDueProposal, TrelloMoveProposal, TrelloRenameProposal
from app.services import tasks as task_service
from app.api.deps import RequestContext, auth_gate, get_request_context, require_admin
from app.services import background_jobs
from app.services import auth as auth_service
from app.services.backups import backup_metadata, backup_status_payload, create_backup_result, get_backup, list_backups, record_validation, restore_plan, validate_backup
from app.services import briefing as briefing_service
from app.services import confirmations as confirmation_service
from app.services import diagnostics as diagnostics_service
from app.services import integrations
from app.services import llm as llm_service
from app.services import openai_models
from app.services import reminders as reminder_service
from app.services import planning as planning_service
from app.services import settings_service
from app.services import system as system_service
from app.services import task_suggestions
from app.services import trello_actions
from app.services import trello_sync
from app.services import secret_store
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_client import TelegramApiClient
from app.services.telegram_processor import process_pending_updates, store_raw_update
from app.services.trello_client import TrelloApiClient
from app.services.trello_config import board_configs, trello_credentials_ready


router = APIRouter(dependencies=[Depends(auth_gate)])


def _set_session_cookie(response: Response, session_token: str) -> None:
    max_age = max(settings.auth_session_ttl_hours, 1) * 60 * 60
    response.set_cookie(
        settings.auth_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.auth_cookie_name, path="/", samesite="lax")


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", app="alphawave-taskd")


@router.post("/api/auth/login", response_model=LoginResponse)
def auth_login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Auth no está habilitado.")
    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas.")
    tokens = auth_service.create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_session_cookie(response, tokens.session_token)
    return LoginResponse(user=auth_service.auth_user_payload(user), csrf_token=tokens.csrf_token)


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    resolved = auth_service.resolve_session(db, request.cookies.get(settings.auth_cookie_name))
    if resolved:
        _user, session = resolved
        auth_service.revoke_session(db, session)
    _clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/api/auth/session", response_model=SessionResponse)
def auth_session(request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    if not settings.auth_enabled:
        return SessionResponse(auth_enabled=False, authenticated=True, owner_exists=auth_service.owner_exists(db))
    resolved = auth_service.resolve_session(db, request.cookies.get(settings.auth_cookie_name))
    if not resolved:
        return SessionResponse(auth_enabled=True, authenticated=False, owner_exists=auth_service.owner_exists(db))
    user, session = resolved
    csrf_token = auth_service.rotate_csrf_token(db, session)
    return SessionResponse(
        auth_enabled=True,
        authenticated=True,
        owner_exists=True,
        user=auth_service.auth_user_payload(user),
        csrf_token=csrf_token,
    )


@router.get("/api/auth/csrf", response_model=CsrfResponse)
def auth_csrf(request: Request, db: Session = Depends(get_db)) -> CsrfResponse:
    if not settings.auth_enabled:
        return CsrfResponse(csrf_token=None)
    resolved = auth_service.resolve_session(db, request.cookies.get(settings.auth_cookie_name))
    if not resolved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")
    _user, session = resolved
    return CsrfResponse(csrf_token=auth_service.rotate_csrf_token(db, session))


@router.get("/api/system/health", response_model=SystemHealth)
def system_health(db: Session = Depends(get_db)) -> SystemHealth:
    return system_service.health(db)


@router.get("/api/system/info", response_model=SystemInfo)
def system_info() -> SystemInfo:
    return system_service.info()


@router.get("/api/system/preflight", response_model=PreflightReport)
def system_preflight(strict: bool = Query(default=False)) -> PreflightReport:
    return system_service.preflight(strict=strict)


@router.get("/api/system/status", response_model=SystemStatus)
def system_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> SystemStatus:
    return system_service.status(db, ctx.user_id)


@router.get("/api/system/installation-readiness", response_model=InstallationReadiness)
def system_installation_readiness(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> InstallationReadiness:
    return system_service.installation_readiness(db, ctx.user_id)


@router.get("/api/system/diagnostics")
def system_diagnostics(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> dict:
    status = system_service.status(db, ctx.user_id).model_dump()
    return diagnostics_service.diagnostics_payload(db, status, user_id=ctx.user_id)


@router.get("/api/llm/status", response_model=LLMStatus)
def llm_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> LLMStatus:
    return llm_service.llm_status(db, ctx.user_id)


@router.post("/api/llm/validate", response_model=LLMValidateResponse)
def validate_llm(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> LLMValidateResponse:
    return llm_service.validate_llm(db, ctx.user_id)


@router.get("/api/integrations/openai/status", response_model=LLMStatus)
def openai_integration_status(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> LLMStatus:
    return llm_service.openai_status(db, ctx.user_id)


@router.get("/api/integrations/openai/models", response_model=OpenAIModelCatalog)
def openai_model_catalog(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> OpenAIModelCatalog:
    current = llm_service.openai_status(db, ctx.user_id)
    return openai_models.list_available_openai_models(
        db,
        ctx.user_id,
        current_model=current.model,
        validated_model=current.validated_model,
        validation_ready=current.validation_status == "ready",
    )


@router.post("/api/integrations/openai/models/refresh", response_model=OpenAIModelCatalog)
def refresh_openai_model_catalog(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> OpenAIModelCatalog:
    current = llm_service.openai_status(db, ctx.user_id)
    return openai_models.list_available_openai_models(
        db,
        ctx.user_id,
        current_model=current.model,
        validated_model=current.validated_model,
        validation_ready=current.validation_status == "ready",
        force_refresh=True,
    )


@router.post("/api/integrations/openai/credentials", response_model=LLMStatus)
def save_openai_credentials(
    payload: OpenAICredentialsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> LLMStatus:
    try:
        return llm_service.save_openai_credentials(db, user_id=ctx.user_id, api_key=payload.api_key)
    except (ValueError, secret_store.SecretStoreUnavailable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/integrations/openai/settings", response_model=LLMStatus)
@router.post("/api/integrations/openai/settings", response_model=LLMStatus)
def save_openai_settings(
    payload: OpenAISettingsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> LLMStatus:
    try:
        return llm_service.update_openai_settings(
            db,
            user_id=ctx.user_id,
            enabled=payload.enabled,
            model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/integrations/openai/validate", response_model=LLMValidateResponse)
def validate_openai_credentials(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> LLMValidateResponse:
    return llm_service.validate_llm(db, ctx.user_id)


@router.delete("/api/integrations/openai/credentials", response_model=LLMStatus)
def revoke_openai_credentials(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> LLMStatus:
    return llm_service.revoke_openai_credentials(db, user_id=ctx.user_id)


@router.get("/api/integrations/telegram/status")
def telegram_integration_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    return integrations.telegram_status_for_user(db, ctx.user_id)


@router.post("/api/integrations/telegram/link-code")
def create_telegram_link_code(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    code, row = integrations.create_telegram_link_code(db, user_id=ctx.user_id)
    return {
        "code": code,
        "expires_at": row.expires_at,
        "instructions": f"Enviá /link {code} al bot de Telegram.",
    }


@router.post("/api/integrations/telegram/unlink")
def unlink_telegram_integration(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    integrations.unlink_telegram(db, user_id=ctx.user_id)
    return integrations.telegram_status_for_user(db, ctx.user_id)


@router.get("/api/integrations/trello/status")
def trello_integration_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    return integrations.trello_status_for_user(db, ctx.user_id)


@router.post("/api/integrations/trello/manual-credentials")
def save_trello_manual_credentials(payload: dict = Body(...), db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    api_key = str(payload.get("api_key") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not api_key or not token:
        raise HTTPException(status_code=400, detail="api_key y token son requeridos.")
    if not secret_store.is_secret_store_available():
        raise HTTPException(status_code=400, detail="Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY para guardar credenciales por usuario.")
    integrations.store_trello_credentials(db, user_id=ctx.user_id, api_key=api_key, token=token)
    return integrations.trello_status_for_user(db, ctx.user_id)


@router.post("/api/integrations/trello/revoke-credentials")
def revoke_trello_manual_credentials(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    integrations.revoke_trello_credentials(db, user_id=ctx.user_id)
    return integrations.trello_status_for_user(db, ctx.user_id)


@router.post("/api/integrations/trello/validate")
async def validate_trello_manual_credentials(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    credentials = integrations.get_trello_credentials_for_user(db, ctx.user_id)
    if not credentials or not credentials.ready:
        return {"provider": "trello", "status": "not_configured", "ok": False, "credentials_source": "none"}
    client = TrelloApiClient(credentials.api_key, credentials.token, timeout=8.0)
    try:
        boards = await client.get_member_boards(credentials.member_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="No pude validar Trello con una lectura segura. Revisá credenciales o conexión.") from exc
    return {
        "provider": "trello",
        "status": "ok",
        "ok": True,
        "credentials_source": credentials.source,
        "boards_seen": len([board for board in boards if board.get("id") and not board.get("closed", False)]),
    }


@router.get("/api/tasks", response_model=TaskList)
def list_tasks(
    status: str = Query(default="active"),
    q: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> TaskList:
    return TaskList(tasks=task_service.list_tasks(db, status=status, q=q, scope=scope, user_id=ctx.user_id))


@router.post("/api/tasks", response_model=TaskRead, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    return task_service.create_task(db, payload, user_id=ctx.user_id)


@router.post("/api/tasks/bulk", response_model=TaskList, status_code=201)
def bulk_create(payload: BulkTaskCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    return TaskList(tasks=task_service.bulk_create_tasks(db, payload, user_id=ctx.user_id))


@router.get("/api/tasks/incomplete-details", response_model=IncompleteTaskList)
def incomplete_task_details(
    limit: int = Query(default=25, ge=1, le=25),
    scope: str | None = Query(default=None),
    q: str | None = Query(default=None),
    include_completed: bool = Query(default=False),
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> IncompleteTaskList:
    if task_id:
        task = _get_or_404(db, task_id, user_id=ctx.user_id)
        if task.status != "active" and not include_completed:
            return IncompleteTaskList(tasks=[], total_candidates=0, limit=limit)
        item = task_suggestions.incomplete_task_payload(task)
        return IncompleteTaskList(tasks=[item], total_candidates=int(item["candidate"]), limit=limit)
    tasks, total = task_suggestions.incomplete_tasks(
        task_service.list_tasks(db, q=q, user_id=ctx.user_id),
        limit=limit,
        scope=scope,
        include_completed=include_completed,
    )
    return IncompleteTaskList(tasks=tasks, total_candidates=total, limit=limit)


@router.post("/api/tasks/suggest-details-bulk", response_model=BulkTaskSuggestionResponse)
def suggest_task_details_bulk(payload: BulkTaskSuggestionRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BulkTaskSuggestionResponse:
    tasks = task_service.list_tasks(db, user_id=ctx.user_id)
    by_id = {task.id: task for task in tasks}
    selected = [by_id[task_id] for task_id in payload.task_ids or [] if task_id in by_id] if payload.task_ids else [
        task for task in tasks if task_suggestions.is_candidate(task)
    ][: payload.limit]
    if payload.task_ids and len(payload.task_ids) > 25:
        raise HTTPException(status_code=400, detail="El lote máximo es 25 tareas.")
    return task_suggestions.suggest_details_bulk(
        db,
        selected[: payload.limit],
        include_notes=payload.include_notes,
        include_checklist=payload.include_checklist,
        allow_existing_field_updates=payload.allow_existing_field_updates,
    )


@router.post("/api/tasks/apply-suggestions-bulk", response_model=BulkTaskSuggestionApplyResponse)
def apply_task_suggestions_bulk(payload: BulkTaskSuggestionApplyRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BulkTaskSuggestionApplyResponse:
    tasks = task_service.list_tasks(db, user_id=ctx.user_id)
    by_id = {task.id: task for task in tasks}
    return task_suggestions.apply_suggestions_bulk(db, by_id, payload.items)


@router.post("/api/tasks/suggest-details-confirmations", response_model=TaskSuggestionConfirmationResponse)
def suggest_task_details_confirmations(payload: BulkTaskSuggestionRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskSuggestionConfirmationResponse:
    tasks = task_service.list_tasks(db, user_id=ctx.user_id)
    by_id = {task.id: task for task in tasks}
    selected = [by_id[task_id] for task_id in payload.task_ids or [] if task_id in by_id] if payload.task_ids else [
        task for task in tasks if len(task_suggestions.missing_detail_fields(task)) >= 3
    ]
    confirmations, skipped = task_suggestions.create_suggestion_confirmations(
        db,
        selected[: payload.limit],
        include_notes=payload.include_notes,
        user_id=ctx.user_id,
    )
    return TaskSuggestionConfirmationResponse(
        created=len(confirmations),
        skipped=skipped,
        confirmation_ids=[confirmation.id for confirmation in confirmations],
    )


@router.get("/api/tasks/{task_id}/detail-gaps", response_model=TaskDetailGapsResponse)
def task_detail_gaps(task_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskDetailGapsResponse:
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    missing = task_suggestions.missing_detail_fields(task)
    return TaskDetailGapsResponse(task_id=task.id, missing_fields=missing, is_candidate=task_suggestions.is_candidate(task))


@router.post("/api/tasks/{task_id}/complete-details", response_model=ManualTaskDetailsResponse)
def complete_task_details(
    task_id: str,
    payload: ManualTaskDetailsUpdate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> ManualTaskDetailsResponse:
    db.execute(text("BEGIN IMMEDIATE"))
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    if (
        task.updated_at != payload.expected_updated_at
        or task_service.task_version_token(task) != payload.expected_version_token
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La tarea cambió mientras la editabas. Revisá los datos actualizados antes de guardar.",
        )
    changes = payload.model_dump(
        exclude={"expected_updated_at", "expected_version_token"},
        exclude_unset=True,
    )
    task = task_service.update_task(
        db,
        task,
        TaskUpdate(**changes),
        preserve_trello_manual_fields=True,
    )
    remaining = task_suggestions.missing_detail_fields(task)
    priority = planning_service.score_task(
        task,
        settings_service.priority_settings(db, user_id=task.user_id),
    )
    return ManualTaskDetailsResponse(
        task=task,
        remaining_missing_fields=remaining,
        is_candidate=task_suggestions.is_candidate(task),
        priority_explanation=priority.model_dump(),
    )


@router.patch("/api/tasks/{task_id}", response_model=TaskRead)
def update_task(task_id: str, payload: TaskUpdate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_service.update_task(db, task, payload)


@router.post("/api/tasks/{task_id}/suggest-details", response_model=TaskSuggestionResponse)
def suggest_task_details(task_id: str, payload: TaskSuggestionRequest | None = Body(default=None), db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_suggestions.suggest_details(
        db,
        task,
        include_notes=(payload.include_notes if payload else True),
        include_checklist=(payload.include_checklist if payload else False),
        allow_existing_field_updates=(payload.allow_existing_field_updates if payload else False),
    )


@router.post("/api/tasks/{task_id}/apply-suggestions", response_model=TaskSuggestionApplyResponse)
def apply_task_suggestions(task_id: str, payload: TaskSuggestionApplyRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        return task_suggestions.apply_suggestions(
            db,
            task,
            payload.suggestions,
            payload.fields,
            allow_existing_field_updates=payload.allow_existing_field_updates,
            source=payload.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tasks/{task_id}/complete", response_model=TaskRead)
def complete_task(task_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_service.complete_task(db, task)


@router.post("/api/tasks/{task_id}/restore", response_model=TaskRead)
def restore_task(task_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_service.restore_task(db, task)


@router.post("/api/tasks/{task_id}/snooze", response_model=TaskRead)
def snooze_task(task_id: str, payload: TaskSnoozeRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_service.snooze_task(db, task, payload.snoozed_until, reason=payload.reason)


@router.post("/api/tasks/{task_id}/create-trello-card", response_model=ConfirmationRead)
async def create_task_trello_card(task_id: str, payload: TrelloCardLinkRequest | None = Body(default=None), db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        return trello_actions.propose_create_card_for_task(db, task, target_state=(payload.target_state if payload else "pending"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/tasks/{task_id}", response_model=TaskRead)
def delete_task(task_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    return task_service.soft_delete_task(db, task)


@router.delete("/api/tasks/{task_id}/permanent", response_model=TrashDeleteResult)
def permanent_delete_task(task_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrashDeleteResult:
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        deleted_count = task_service.permanent_delete_task(db, task)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TrashDeleteResult(deleted_count=deleted_count)


@router.post("/api/tasks/trash/delete-bulk", response_model=TrashDeleteResult)
def permanent_delete_tasks(payload: BulkTaskIdsRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrashDeleteResult:
    try:
        deleted_count = task_service.permanent_delete_tasks(db, payload.task_ids, user_id=ctx.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TrashDeleteResult(deleted_count=deleted_count)


@router.post("/api/tasks/trash/empty", response_model=TrashDeleteResult)
def empty_trash(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrashDeleteResult:
    return TrashDeleteResult(deleted_count=task_service.empty_trash(db, user_id=ctx.user_id))


@router.post("/api/tasks/trash/restore-bulk", response_model=TrashRestoreResult)
def restore_tasks(payload: BulkTaskIdsRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrashRestoreResult:
    try:
        restored_count = task_service.restore_tasks(db, payload.task_ids, user_id=ctx.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TrashRestoreResult(restored_count=restored_count)


@router.post("/api/tasks/reorder", response_model=TaskList)
def reorder_tasks(payload: ReorderRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    return TaskList(tasks=task_service.reorder_tasks(db, payload.task_ids, user_id=ctx.user_id))


@router.post("/api/tasks/sort/propose", response_model=SortProposal)
def propose_task_sort(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> SortProposal:
    return planning_service.propose_sort(db, user_id=ctx.user_id)


@router.post("/api/tasks/sort/apply", response_model=TaskList)
def apply_task_sort(payload: SortApplyRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    try:
        return TaskList(tasks=planning_service.apply_sort(db, payload.confirmation_id, user_id=ctx.user_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/tasks/completed", response_model=TaskList)
def completed_tasks(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    return TaskList(tasks=task_service.list_tasks(db, status="completed", user_id=ctx.user_id))


@router.get("/api/tasks/trash", response_model=TaskList)
def trash_tasks(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    return TaskList(tasks=task_service.list_tasks(db, status="deleted", user_id=ctx.user_id))


@router.get("/api/tasks/search", response_model=TaskList)
def search_tasks(q: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TaskList:
    return TaskList(tasks=task_service.list_tasks(db, q=q, user_id=ctx.user_id))


@router.get("/api/planning/today", response_model=TodayPlan)
def get_today_plan(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TodayPlan:
    return planning_service.today_plan(db, user_id=ctx.user_id)


@router.post("/api/planning/today/regenerate", response_model=TodayPlan)
def regenerate_today_plan(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TodayPlan:
    return planning_service.today_plan(db, user_id=ctx.user_id)


@router.get("/api/planning/now", response_model=NowPlan)
def get_now_plan(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> NowPlan:
    return planning_service.now_plan(db, user_id=ctx.user_id)


@router.post("/api/planning/now/regenerate", response_model=NowPlan)
def regenerate_now_plan(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> NowPlan:
    return planning_service.now_plan(db, user_id=ctx.user_id)


@router.get("/api/planning/inbox-suggestions", response_model=InboxSuggestions)
def get_inbox_suggestions(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> InboxSuggestions:
    return planning_service.inbox_suggestions(db, user_id=ctx.user_id)


@router.get("/api/briefing/status", response_model=BriefingStatus)
def get_briefing_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BriefingStatus:
    return briefing_service.status(db, user_id=ctx.user_id)


@router.post("/api/briefing/generate", response_model=BriefingPayload)
def generate_briefing(payload: BriefingGenerateRequest | None = None, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BriefingPayload:
    return briefing_service.generate_payload(db, briefing_date=payload.date if payload else None, user_id=ctx.user_id)


@router.post("/api/briefing/send")
async def send_briefing(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    messenger = TelegramApiClient(settings.telegram_bot_token)
    run = await briefing_service.send_briefing(db, messenger, reason="auto", user_id=ctx.user_id)
    return {"status": run.status, "id": run.id}


@router.post("/api/briefing/send-test")
async def send_test_briefing(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    messenger = TelegramApiClient(settings.telegram_bot_token)
    run = await briefing_service.send_briefing(db, messenger, reason="test", force=True, user_id=ctx.user_id)
    return {"status": run.status, "id": run.id}


@router.get("/api/briefing/runs", response_model=BriefingRuns)
def get_briefing_runs(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BriefingRuns:
    return BriefingRuns(runs=briefing_service.list_runs(db, user_id=ctx.user_id))


@router.get("/api/reminders", response_model=ReminderList)
def list_reminders(
    status: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> ReminderList:
    return ReminderList(reminders=reminder_service.list_reminders(db, status=status, limit=limit, user_id=ctx.user_id))


@router.post("/api/reminders", response_model=ReminderRead, status_code=201)
def create_reminder(payload: ReminderCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    return reminder_service.create_reminder(db, payload, user_id=ctx.user_id)


@router.patch("/api/reminders/{reminder_id}", response_model=ReminderRead)
def update_reminder(reminder_id: str, payload: ReminderUpdate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    reminder = _get_reminder_or_404(db, reminder_id, user_id=ctx.user_id)
    return reminder_service.update_reminder(db, reminder, payload)


@router.post("/api/reminders/{reminder_id}/cancel", response_model=ReminderRead)
def cancel_reminder(reminder_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    reminder = _get_reminder_or_404(db, reminder_id, user_id=ctx.user_id)
    return reminder_service.cancel_reminder(db, reminder)


@router.get("/api/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> SettingsResponse:
    return SettingsResponse(
        settings=settings_service.get_settings(db, user_id=ctx.user_id),
        sources=settings_service.get_sources(db, user_id=ctx.user_id),
        requires_restart=settings_service.schema()["requires_restart"],
        available_scopes=settings_service.available_scopes(db, user_id=ctx.user_id),
        selected_weekend_scopes=settings_service.selected_weekend_scopes(db, user_id=ctx.user_id),
    )


@router.patch("/api/settings", response_model=SettingsResponse)
def patch_settings(payload: dict = Body(...), db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> SettingsResponse:
    settings_service.patch_settings(db, payload, user_id=ctx.user_id, is_admin=ctx.is_admin)
    return get_settings(db, ctx)


@router.get("/api/settings/schema")
def get_settings_schema():
    return settings_service.schema()


@router.post("/api/settings/reset-section", response_model=SettingsResponse)
def reset_settings_section(payload: SettingsResetRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> SettingsResponse:
    settings_service.reset_section(db, payload.section, user_id=ctx.user_id, is_admin=ctx.is_admin)
    return get_settings(db, ctx)


@router.get("/api/settings/trello/boards")
def list_configured_trello_boards(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> dict:
    return settings_service.trello_boards_response(db, user_id=ctx.user_id)


@router.post("/api/settings/trello/boards", response_model=SettingsResponse, status_code=201)
def create_configured_trello_board(payload: TrelloBoardCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> SettingsResponse:
    settings_service.create_trello_board(db, payload.model_dump(), user_id=ctx.user_id)
    return get_settings(db, ctx)


@router.get("/api/settings/trello/discover-boards", response_model=TrelloDiscoveryResponse)
async def discover_available_trello_boards(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> TrelloDiscoveryResponse:
    if not settings.trello_enabled:
        raise HTTPException(status_code=400, detail="Trello no está habilitado. Revisá TRELLO_ENABLED.")
    credentials = integrations.get_trello_credentials_for_user(db, ctx.user_id)
    if not credentials or not credentials.ready:
        raise HTTPException(status_code=400, detail="No hay credenciales Trello configuradas para este usuario.")
    client = TrelloApiClient(credentials.api_key, credentials.token)
    try:
        boards = await client.get_member_boards(credentials.member_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="No pude consultar tus boards de Trello. Revisá credenciales o conexión.") from exc
    return TrelloDiscoveryResponse(
        boards=[
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "url": item.get("url"),
                "shortLink": item.get("shortLink"),
                "closed": bool(item.get("closed", False)),
            }
            for item in boards
            if item.get("id") and not item.get("closed", False)
        ]
    )


@router.get("/api/settings/trello/boards/{board_id}/lists")
async def discover_trello_board_lists(board_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> dict:
    credentials = integrations.get_trello_credentials_for_user(db, ctx.user_id)
    if not settings.trello_enabled:
        raise HTTPException(status_code=400, detail="Trello no está habilitado. Revisá TRELLO_ENABLED.")
    if not credentials or not credentials.ready:
        raise HTTPException(status_code=400, detail="No hay credenciales Trello configuradas para este usuario.")
    client = TrelloApiClient(credentials.api_key, credentials.token)
    try:
        lists = await client.get_lists(board_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="No pude consultar las listas de ese board Trello. Revisá credenciales o conexión.") from exc
    return {
        "board_id": board_id,
        "lists": [
            {"id": item.get("id"), "name": item.get("name"), "closed": bool(item.get("closed", False))}
            for item in lists
            if item.get("id") and item.get("name") and not item.get("closed", False)
        ],
    }


@router.post("/api/settings/trello/discover", response_model=TrelloDiscoveryResponse)
async def discover_trello_lists(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> TrelloDiscoveryResponse:
    return await _trello_discovery_response(db, user_id=ctx.user_id)


@router.get("/api/settings/trello/discover", response_model=TrelloDiscoveryResponse)
async def get_trello_lists_discovery(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> TrelloDiscoveryResponse:
    return await _trello_discovery_response(db, user_id=ctx.user_id)


@router.post("/api/settings/trello/validate", response_model=TrelloValidationResponse)
async def validate_trello_mappings(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> TrelloValidationResponse:
    lists_by_board = await _trello_lists_by_board(db, user_id=ctx.user_id)
    return TrelloValidationResponse(**settings_service.validate_trello_lists(db, lists_by_board, user_id=ctx.user_id))


@router.patch("/api/settings/trello/boards/{board_alias}", response_model=SettingsResponse)
def patch_configured_trello_board(board_alias: str, payload: TrelloBoardPatch, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> SettingsResponse:
    settings_service.patch_trello_board(db, board_alias, payload.model_dump(exclude_unset=True), user_id=ctx.user_id)
    return get_settings(db, ctx)


@router.post("/api/settings/trello/boards/{board_alias}/disable", response_model=SettingsResponse)
def disable_configured_trello_board(board_alias: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> SettingsResponse:
    settings_service.disable_trello_board(db, board_alias, user_id=ctx.user_id)
    return get_settings(db, ctx)


@router.post("/api/settings/trello/boards/{board_alias}/validate", response_model=TrelloValidationResponse)
async def validate_configured_trello_board(board_alias: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> TrelloValidationResponse:
    lists_by_board = await _trello_lists_by_board(db, user_id=ctx.user_id)
    result = settings_service.validate_trello_lists(db, lists_by_board, user_id=ctx.user_id)
    result["boards"] = [board for board in result["boards"] if str(board.get("alias", "")).casefold() == board_alias.casefold()]
    result["status"] = "error" if any(board.get("status") == "error" for board in result["boards"]) else "ok"
    if not result["boards"]:
        raise HTTPException(status_code=404, detail="Board Trello no encontrado.")
    return TrelloValidationResponse(**result)


@router.patch("/api/settings/trello/boards/{board_alias}/mapping", response_model=SettingsResponse)
def patch_trello_board_mapping(board_alias: str, payload: dict = Body(...), db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> SettingsResponse:
    settings_service.update_trello_mapping(
        db,
        board_alias,
        str(payload.get("state") or ""),
        list_id=payload.get("list_id"),
        list_name=payload.get("list_name"),
        user_id=ctx.user_id,
    )
    return get_settings(db, ctx)


@router.post("/api/trello/sync", response_model=TrelloSyncSummary)
async def sync_trello(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrelloSyncSummary:
    return await trello_sync.run_trello_sync(db, user_id=ctx.user_id)


@router.get("/api/trello/status", response_model=TrelloStatus)
def get_trello_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> TrelloStatus:
    return trello_sync.trello_status(db, user_id=ctx.user_id)


@router.get("/api/confirmations", response_model=ConfirmationList)
def list_confirmations(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> ConfirmationList:
    return ConfirmationList(confirmations=confirmation_service.list_pending(db, user_id=ctx.user_id))


@router.get("/api/jobs", response_model=BackgroundJobList)
def list_background_jobs(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_admin),
) -> BackgroundJobList:
    return BackgroundJobList(jobs=[background_jobs.serialize_job(job) for job in background_jobs.list_jobs(db, kind=kind, status=status)])


@router.get("/api/jobs/{job_id}", response_model=BackgroundJobRead)
def get_background_job(job_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> BackgroundJobRead:
    job = background_jobs.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return background_jobs.serialize_job(job)


@router.post("/api/confirmations/bulk-confirm", response_model=BackgroundJobQueued)
async def bulk_confirm_confirmations(payload: ConfirmationBulkRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> BackgroundJobQueued:
    requested = set(payload.confirmation_ids)
    scoped = db.scalars(
        select(PendingConfirmation).where(
            PendingConfirmation.id.in_(requested),
            PendingConfirmation.user_id == ctx.user_id,
        )
    ).all()
    scoped_ids = [confirmation.id for confirmation in scoped]
    if len(scoped_ids) != len(requested):
        raise HTTPException(status_code=404, detail="Una o más confirmaciones no existen.")
    job = background_jobs.create_bulk_confirmation_job(db, scoped_ids, user_id=ctx.user_id)
    background_jobs.schedule_bulk_confirmation_job(job.id)
    return BackgroundJobQueued(job_id=job.id, status="queued", total=job.total)


@router.post("/api/confirmations/bulk-cancel", response_model=ConfirmationBulkResponse)
def bulk_cancel_confirmations(payload: ConfirmationBulkRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)) -> ConfirmationBulkResponse:
    results: list[ConfirmationBulkResult] = []
    for confirmation_id in payload.confirmation_ids:
        confirmation = confirmation_service.get_pending(db, confirmation_id, user_id=ctx.user_id)
        if not confirmation:
            results.append(ConfirmationBulkResult(id=confirmation_id, status="failed", error="La confirmación no está pendiente o ya venció."))
            continue
        try:
            _cancel_confirmation_action(db, confirmation)
            results.append(ConfirmationBulkResult(id=confirmation_id, status="cancelled", message="Confirmación cancelada."))
        except Exception as exc:  # noqa: BLE001 - bulk must continue with remaining confirmations.
            db.rollback()
            results.append(ConfirmationBulkResult(id=confirmation_id, status="failed", error=str(exc)))
    cancelled = sum(1 for result in results if result.status == "cancelled")
    failed = len(results) - cancelled
    status = "ok" if cancelled == len(results) else "failed" if cancelled == 0 else "partial"
    return ConfirmationBulkResponse(status=status, total=len(results), cancelled=cancelled, failed=failed, results=results)


@router.post("/api/confirmations/{confirmation_id}/confirm")
async def confirm_confirmation(confirmation_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    confirmation = confirmation_service.get_pending(db, confirmation_id, user_id=ctx.user_id)
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmation not found or expired")
    try:
        return {"message": await _execute_confirmation_action(db, confirmation)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/confirmations/{confirmation_id}/cancel")
def cancel_confirmation(confirmation_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    confirmation = confirmation_service.get_pending(db, confirmation_id, user_id=ctx.user_id)
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmation not found or expired")
    _cancel_confirmation_action(db, confirmation)
    return {"message": "Confirmación cancelada."}


async def _execute_confirmation_action(db: Session, confirmation) -> str:
    if confirmation.action_type == "apply_sort_order":
        planning_service.apply_sort(db, confirmation.id, user_id=confirmation.user_id)
        return "Orden aplicado."
    if confirmation.action_type == task_suggestions.APPLY_SUGGESTIONS_ACTION:
        return task_suggestions.apply_suggestion_confirmation(db, confirmation)
    if confirmation.action_type in trello_actions.TRELLO_ACTION_TYPES:
        return await trello_actions.execute_confirmation(db, confirmation)
    raise ValueError("Confirmation action is not executable from API")


def _cancel_confirmation_action(db: Session, confirmation) -> None:
    if confirmation.action_type in trello_actions.TRELLO_ACTION_TYPES:
        trello_actions.cancel_confirmation(db, confirmation)
    else:
        confirmation_service.cancel(db, confirmation)


@router.post("/api/trello/cards/propose", response_model=ConfirmationRead)
async def propose_trello_card(payload: TrelloCreateCardProposal, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    try:
        confirmation = trello_actions.propose_create_card(
            db,
            board_alias=payload.board_alias,
            list_name=payload.list_name,
            title=payload.title,
            description=payload.description,
            due_at=payload.due_at,
            source="ui",
            user_id=ctx.user_id,
        )
        return confirmation
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/trello/cards/create-now", response_model=ConfirmationRead)
async def create_trello_card_now(payload: TrelloCreateCardNow, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    try:
        confirmation = trello_actions.propose_create_card(
            db,
            board_alias=payload.board_alias,
            list_name=None,
            title=payload.title,
            description=payload.description,
            due_at=payload.due_at,
            source="ui",
            user_id=ctx.user_id,
        )
        return confirmation
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/trello/cards/{task_id}/move/propose", response_model=ConfirmationRead)
async def propose_trello_move(task_id: str, payload: TrelloMoveProposal, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        confirmation = trello_actions.propose_move_task(db, task, target_state=payload.target_state, source="ui")
        return confirmation
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/trello/cards/{task_id}/rename/propose", response_model=ConfirmationRead)
async def propose_trello_rename(task_id: str, payload: TrelloRenameProposal, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        confirmation = trello_actions.propose_rename_task(db, task, title=payload.title, source="ui")
        return confirmation
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/trello/cards/{task_id}/due/propose", response_model=ConfirmationRead)
async def propose_trello_due(task_id: str, payload: TrelloDueProposal, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context)):
    task = _get_or_404(db, task_id, user_id=ctx.user_id)
    try:
        confirmation = trello_actions.propose_update_due(db, task, due_at=payload.due_at, source="ui")
        return confirmation
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backups/status", response_model=BackupStatus)
def backups_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> BackupStatus:
    return BackupStatus(**backup_status_payload(db=db))


@router.get("/api/backups", response_model=BackupList)
def backups_list(ctx: RequestContext = Depends(require_admin)) -> BackupList:
    return BackupList(backups=list_backups())


@router.post("/api/backups", response_model=BackupCreateResponse)
def backups_create(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> BackupCreateResponse:
    result = create_backup_result(db=db, manual=True)
    return BackupCreateResponse(
        created=result.created,
        backup=result.backup,
        path=result.backup["path_redacted"] if result.backup else None,
        reason=result.reason,
    )


@router.post("/api/backups/{backup_id}/validate", response_model=BackupValidateResponse)
def backups_validate(backup_id: str, ctx: RequestContext = Depends(require_admin)) -> BackupValidateResponse:
    path = get_backup(backup_id)
    if not path:
        raise HTTPException(status_code=404, detail="El backup seleccionado ya no existe.")
    validation = validate_backup(path)
    return BackupValidateResponse(backup=record_validation(path, validation))


@router.post("/api/backups/{backup_id}/restore-plan", response_model=BackupRestorePlan)
def backups_restore_plan(backup_id: str, ctx: RequestContext = Depends(require_admin)) -> BackupRestorePlan:
    path = get_backup(backup_id)
    if not path:
        raise HTTPException(status_code=404, detail="El backup seleccionado ya no existe.")
    return BackupRestorePlan(**restore_plan(path))


@router.post("/api/backups/{backup_id}/restore", response_model=BackupRestoreResponse)
def backups_restore(
    backup_id: str,
    payload: BackupRestoreRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_admin),
) -> BackupRestoreResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=403, detail="Restore por API requiere autenticación, admin y protección CSRF.")
    path = get_backup(backup_id)
    if not path:
        raise HTTPException(status_code=404, detail="El backup seleccionado ya no existe.")
    plan = restore_plan(path)
    if not plan["automatic_restore_available"]:
        raise HTTPException(status_code=409, detail=plan["reason"] or "El backup no superó la validación.")
    def create_pre_restore_backup():
        try:
            return create_backup_result(db=db, manual=True, source="pre_restore").path
        finally:
            db.close()

    try:
        safety_path = system_service.restore_database_from_backup(
            path,
            create_safety_backup=create_pre_restore_backup,
        )
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=f"Restore cancelado: {exc}") from exc
    if not safety_path:  # pragma: no cover - service enforces this invariant.
        raise HTTPException(status_code=500, detail="Restore sin backup de seguridad.")
    return BackupRestoreResponse(
        backup_id=backup_id,
        safety_backup=backup_metadata(safety_path),
    )


@router.post("/api/system/backup")
def backup_now(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)):
    result = create_backup_result(db=db, manual=True)
    return {"created": result.created, "path": result.backup["path_redacted"] if result.backup else None, "backup": result.backup, "reason": result.reason}


@router.get("/api/system/backup-status", response_model=BackupStatus)
def backup_status(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)) -> BackupStatus:
    return BackupStatus(**backup_status_payload(db=db))


@router.post("/api/dev/simulate-message", response_model=TelegramProcessResponse)
async def simulate_telegram_message(payload: SimulateTelegramMessageRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)):
    _require_dev_endpoints_enabled()
    update_id = payload.update_id if payload.update_id is not None else _next_dev_update_id(db)
    store_raw_update(
        db,
        {
            "update_id": update_id,
            "message": {
                "message_id": payload.message_id or update_id,
                "from": {"id": payload.user_id},
                "chat": {"id": payload.chat_id},
                "text": payload.text,
            },
        },
        advance_offset=False,
    )
    messenger = CollectingMessenger()
    result = await process_pending_updates(db, messenger, allowed_user_id=payload.user_id)
    return TelegramProcessResponse(
        processed=result.processed,
        ignored=result.ignored,
        replies=[message for _, message in messenger.messages],
        summary_sent=result.summary_sent,
    )


@router.post("/api/telegram/process-pending", response_model=TelegramProcessResponse)
async def process_telegram_pending(db: Session = Depends(get_db), ctx: RequestContext = Depends(require_admin)):
    _require_dev_endpoints_enabled()
    messenger = CollectingMessenger()
    result = await process_pending_updates(db, messenger)
    return TelegramProcessResponse(
        processed=result.processed,
        ignored=result.ignored,
        replies=[message for _, message in messenger.messages],
        summary_sent=result.summary_sent,
    )


def _require_dev_endpoints_enabled() -> None:
    if not settings.app_dev_endpoints_enabled:
        raise HTTPException(status_code=404, detail="Dev endpoint disabled.")


def _get_or_404(db: Session, task_id: str, *, user_id: str | None = None):
    task = task_service.get_task(db, task_id, user_id=user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _get_reminder_or_404(db: Session, reminder_id: str, *, user_id: str | None = None):
    reminder = reminder_service.get_reminder(db, reminder_id, user_id=user_id)
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return reminder


def _next_dev_update_id(db: Session) -> int:
    from sqlalchemy import func, select
    from app.models.telegram import TelegramUpdate

    current = db.scalar(select(func.min(TelegramUpdate.update_id)).where(TelegramUpdate.update_id < 0))
    return int(current) - 1 if current is not None else -1


async def _trello_discovery_response(db: Session, user_id: str | None = None) -> TrelloDiscoveryResponse:
    return TrelloDiscoveryResponse(**settings_service.trello_discovery_from_lists(await _trello_lists_by_board(db, user_id=user_id), db, user_id=user_id))


async def _trello_lists_by_board(db: Session, user_id: str | None = None) -> dict[str, list[dict]]:
    if not settings.trello_enabled:
        raise HTTPException(status_code=400, detail="Trello no está habilitado. Revisá TRELLO_ENABLED.")
    credentials = integrations.get_trello_credentials_for_user(db, user_id)
    if not credentials or not credentials.ready:
        raise HTTPException(status_code=400, detail="Faltan credenciales Trello para descubrir listas.")
    client = TrelloApiClient(credentials.api_key, credentials.token)
    result: dict[str, list[dict]] = {}
    for board in board_configs(db, include_disabled=True, user_id=user_id):
        if not board.board_id:
            result[board.alias] = []
            continue
        result[board.alias] = await client.get_lists(board.board_id)
    return result
