export type Task = {
  id: string;
  title: string;
  status: "active" | "completed" | "deleted" | "archived";
  task_kind: "normal" | "perpetual" | "attention";
  source_type: "local" | "trello";
  source_id: string | null;
  source_url: string | null;
  scope: string;
  origin_label: string | null;
  manual_order: number;
  trello_board_name: string | null;
  trello_list_name: string | null;
  trello_board_id: string | null;
  trello_list_id: string | null;
  trello_state: string | null;
  checklist_done: number | null;
  checklist_total: number | null;
  priority_label: "high" | "medium_high" | "medium" | "low" | null;
  impact_score: number | null;
  urgency_score: number | null;
  blocking_score: number | null;
  effort_bucket: "quick" | "medium" | "deep" | null;
  estimated_minutes: number | null;
  context_bucket: "deep_work" | "quick_task" | "call" | "errand" | "admin" | "review" | null;
  due_at: string | null;
  snoozed_until: string | null;
  last_trello_activity_at: string | null;
  next_checkin_at: string | null;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  deleted_at: string | null;
};

export type TaskPatch = Partial<
  Pick<
    Task,
    | "title"
    | "scope"
    | "priority_label"
    | "due_at"
    | "snoozed_until"
    | "impact_score"
    | "urgency_score"
    | "blocking_score"
    | "effort_bucket"
    | "estimated_minutes"
    | "context_bucket"
  >
> & {
  notes?: string | null;
};

export type TaskCreatePayload = Partial<
  Pick<
    Task,
    | "scope"
    | "priority_label"
    | "due_at"
    | "impact_score"
    | "urgency_score"
    | "blocking_score"
    | "effort_bucket"
    | "estimated_minutes"
    | "context_bucket"
  >
> & {
  title: string;
  auto_classify?: boolean;
};

export type Reminder = {
  id: string;
  task_id: string | null;
  message: string;
  remind_at: string;
  channel: string;
  status: "pending" | "sent" | "cancelled";
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  source: string | null;
  source_update_id: number | null;
};

export type Confirmation = {
  id: string;
  source: string;
  chat_id: string | null;
  user_message_id: number | null;
  action_type: string;
  payload_json: string;
  status: "pending" | "confirmed" | "cancelled" | "expired" | "failed";
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  summary: string | null;
  error: string | null;
};

type TaskListResponse = {
  tasks: Task[];
};

export type AuthUser = {
  id: string;
  email: string;
  display_name: string | null;
  role: string;
};

export type AuthSession = {
  auth_enabled: boolean;
  authenticated: boolean;
  owner_exists: boolean;
  user: AuthUser | null;
  csrf_token: string | null;
};

export type LoginResponse = {
  user: AuthUser;
  csrf_token: string;
};

export type SuggestionItem = {
  value: unknown;
  confidence: number;
  reason: string;
  applies_to_empty_field: boolean;
};

export type TaskSuggestionResponse = {
  task_id: string;
  suggestions: Record<string, SuggestionItem>;
  source: "openai" | "heuristic" | string;
  llm_status: string;
  missing_fields: string[];
  created_at: string;
  warnings: string[];
};

export type BulkTaskSuggestionResult = {
  task_id: string;
  ok: boolean;
  suggestion: TaskSuggestionResponse | null;
  error: string | null;
};

export type BulkTaskSuggestionResponse = {
  mode: "sync" | string;
  source_summary: Record<string, number>;
  results: BulkTaskSuggestionResult[];
  warnings: string[];
};

export type BulkTaskSuggestionApplyItem = {
  task_id: string;
  suggestions: Record<string, unknown>;
  fields: string[];
  allow_existing_field_updates?: boolean;
  source?: string;
};

export type TaskSuggestionApplyResponse = {
  task: Task;
  applied_fields: string[];
  skipped_fields: Array<{ field: string; reason: string }>;
  remaining_missing_fields: string[];
  is_candidate: boolean;
  priority_explanation: PriorityExplanation;
};

export type BulkTaskSuggestionApplyResponse = {
  applied: number;
  errors: Array<{ task_id: string; error: string }>;
  tasks: Task[];
  results: TaskSuggestionApplyResponse[];
};

export type TaskDetailGaps = {
  task_id: string;
  missing_fields: string[];
  is_candidate: boolean;
};

export type TaskSuggestionConfirmationResponse = {
  created: number;
  skipped: number;
  confirmation_ids: string[];
};

export type IncompleteTaskCandidate = {
  id: string;
  title: string;
  status: Task["status"];
  scope: string;
  source_type: Task["source_type"];
  source_id: string | null;
  updated_at: string;
  version_token: string;
  missing_fields: string[];
  missing_count: number;
  candidate: boolean;
  current: Record<string, unknown>;
};

export type IncompleteTaskDetails = {
  tasks: IncompleteTaskCandidate[];
  total_candidates: number;
  limit: number;
};

export type ManualTaskDetailsResponse = {
  task: Task;
  remaining_missing_fields: string[];
  is_candidate: boolean;
  priority_explanation: PriorityExplanation;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type TrashDeleteResponse = {
  deleted_count: number;
};

type TrashRestoreResponse = {
  restored_count: number;
};

type ReminderListResponse = {
  reminders: Reminder[];
};

type ConfirmationListResponse = {
  confirmations: Confirmation[];
};

export type ConfirmationBulkResult = {
  id: string;
  status: "confirmed" | "cancelled" | "failed" | string;
  message: string | null;
  error: string | null;
};

export type ConfirmationBulkResponse = {
  status: "ok" | "partial" | "failed" | string;
  total: number;
  confirmed: number;
  cancelled: number;
  failed: number;
  results: ConfirmationBulkResult[];
};

export type BackgroundJob = {
  id: string;
  kind: string;
  status: "queued" | "running" | "completed" | "partial" | "failed" | string;
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  results: ConfirmationBulkResult[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
};

export type BackgroundJobQueued = {
  job_id: string;
  status: string;
  total: number;
};

export type BriefingRun = {
  id: string;
  briefing_date: string;
  scheduled_for: string;
  sent_at: string | null;
  status: string;
  reason: string | null;
  channel: string;
  payload_json: string | null;
  created_at: string;
  updated_at: string;
};

export type PlanItem = {
  task: Task;
  reason: string;
  priority?: PriorityExplanation | null;
};

export type PlanRecommendation = {
  kind: string;
  task?: Task | null;
  title: string;
  reason: string;
  priority?: PriorityExplanation | null;
};

export type TodayPlan = {
  groups: Array<{ key: string; title: string; items: PlanItem[]; summary?: string }>;
  summary?: string;
  recommendations?: PlanRecommendation[];
  warnings?: string[];
};

export type NowPlan = {
  recommended: PlanItem[];
  afterwards: PlanItem[];
  avoid: PlanItem[];
  summary?: string;
  alternatives?: PlanRecommendation[];
  warnings?: string[];
};

export type SortProposalItem = {
  task_id: string;
  old_index: number;
  new_index: number;
  title: string;
  scope: string;
  score: number;
  reason: string;
  priority?: PriorityExplanation | null;
};

export type PriorityFactor = {
  criterion: string;
  label: string;
  contribution: number;
  direction: "up" | "down";
  reason: string;
};

export type PriorityExplanation = {
  task_id: string;
  score: number;
  priority_band: "high" | "medium_high" | "medium" | "low";
  factors: PriorityFactor[];
  summary: string;
  warnings: string[];
};

export type SortProposal = {
  confirmation_id: string;
  task_ids: string[];
  tasks: Task[];
  expires_at: string;
  items?: SortProposalItem[];
  summary?: string;
  requires_confirmation?: boolean;
};

export type BriefingStatus = {
  enabled: boolean;
  timezone: string;
  time: string;
  late_cutoff: string;
  today_sent: boolean;
  last_run: BriefingRun | null;
};

export type BriefingPayload = {
  briefing_date: string;
  text: string;
  groups: Record<string, Array<Record<string, unknown>>>;
};

// The settings and diagnostics endpoints intentionally return schema-driven
// objects whose exact fields vary by backend capability.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DynamicApiObject = Record<string, any>;

export type AppSettingsResponse = {
  settings: DynamicApiObject;
  sources: Record<string, string>;
  requires_restart: string[];
  available_scopes: string[];
  selected_weekend_scopes: string[];
};

export type SettingsSchema = DynamicApiObject;

export type TrelloDiscovery = {
  boards: Array<{
    alias: string;
    name: string;
    board_id_configured: boolean;
    lists: TrelloList[];
  }>;
};

export type TrelloList = {
  id: string;
  name: string;
  closed?: boolean;
};

export type TrelloAvailableBoard = {
  id: string;
  name: string;
  url?: string | null;
  shortLink?: string | null;
  closed?: boolean;
};

export type TrelloBoardDiscovery = {
  boards: TrelloAvailableBoard[];
};

export type TrelloConfiguredBoards = {
  boards: DynamicApiObject[];
};

export type TrelloValidation = {
  status: string;
  boards: DynamicApiObject[];
  settings: DynamicApiObject;
};

export type TrelloStatus = {
  enabled: boolean;
  configured: boolean;
  last_sync_started_at: string | null;
  last_sync_completed_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
};

export type TelegramIntegrationStatus = {
  provider: "telegram";
  configured: boolean;
  linked: boolean;
  status: string;
  chat_redacted: string;
  legacy_fallback: boolean;
  secret_store_available: boolean;
};

export type TelegramLinkCode = {
  code: string;
  expires_at: string;
  instructions: string;
};

export type TrelloIntegrationStatus = {
  provider: "trello";
  configured: boolean;
  credentials_present: boolean;
  member_id_configured: boolean;
  credentials_source: string;
  status: string;
  api_key_hint: string;
  token_hint: string;
  secret_store_available: boolean;
};

export type ReadinessItem = {
  status: "ready" | "requires_configuration" | "unavailable" | "disabled";
  ready: boolean;
  detail: string;
  fallback?: string | null;
};

export type InstallationReadiness = {
  generated_at: string;
  instance: Record<string, ReadinessItem>;
  user: Record<string, ReadinessItem>;
};

export type BackupStatus = {
  latest_path: string | null;
  latest_created_at: string | null;
  automatic_enabled?: boolean | null;
  retention_days?: number | null;
  backup_dir?: string | null;
  latest_backup?: BackupMetadata | null;
  count?: number;
  total_size_bytes?: number;
  last_error?: string | null;
};

export type BackupMetadata = {
  id: string;
  created_at: string;
  path_redacted: string;
  size_bytes: number;
  db_size_bytes?: number | null;
  sha256?: string | null;
  source: "manual" | "automatic" | "pre_restore" | "unknown";
  valid: boolean;
  validation: {
    checked_at: string | null;
    sqlite_integrity: "ok" | "failed" | "not_checked";
    readable: boolean;
    reason: string | null;
  };
};

export type BackupRestorePlan = {
  backup: BackupMetadata;
  current_db: {
    path_redacted: string;
    size_bytes: number;
    last_modified_at: string | null;
  } | null;
  actions: string[];
  requires_confirmation: boolean;
  confirmation_phrase: string;
  automatic_restore_available: boolean;
  manual_commands: string[];
  reason: string | null;
};

export type SystemStatusItem = {
  status: string;
  detail?: string | null;
  interval_seconds?: number | null;
  interval_minutes?: number | null;
  last_sync?: string | null;
  last_backup?: string | null;
  last_run?: string | null;
};

export type SystemStatus = {
  generated_at?: string | null;
  environment?: DynamicApiObject | null;
  overall?: {
    status: string;
    summary: string;
  } | null;
  services?: DynamicApiObject | null;
  weekend_mode?: DynamicApiObject | null;
  telegram: SystemStatusItem;
  trello: SystemStatusItem;
  trello_write: SystemStatusItem;
  llm: SystemStatusItem;
  reminders: SystemStatusItem;
  trello_sync: SystemStatusItem;
  briefing: SystemStatusItem;
  backup: SystemStatusItem;
};

export type SystemDiagnostics = DynamicApiObject;

export type LLMStatus = {
  enabled: boolean;
  user_enabled: boolean;
  provider: "openai";
  status: "disabled" | "requires_encryption_key" | "requires_api_key" | "not_validated" | "ready" | "error";
  api_key_configured: boolean;
  api_key_hint?: string | null;
  model: string;
  model_configured: boolean;
  model_supported: boolean;
  model_available?: boolean | null;
  validated_model?: string | null;
  validation_status: "disabled" | "requires_encryption_key" | "requires_api_key" | "not_validated" | "ready" | "error";
  secret_store_available: boolean;
  last_validated_at?: string | null;
  last_success_at?: string | null;
  last_error_at?: string | null;
  last_error?: string | null;
  safe_to_use: boolean;
  fallback_available: boolean;
  reason: string;
};

export type OpenAIModelOption = {
  id: string;
  display_name: string;
  category: string;
  recommendation?: string | null;
  created_at?: string | null;
  owned_by?: string | null;
  is_current: boolean;
  is_validated: boolean;
  compatibility: "supported" | "requires_validation" | "unavailable";
};

export type OpenAIModelCatalog = {
  models: OpenAIModelOption[];
  current_model?: string | null;
  recommended_model?: string | null;
  fetched_at?: string | null;
  cached: boolean;
  stale: boolean;
  policy_version: string;
  status: "ready" | "requires_api_key" | "requires_encryption_key" | "fetch_error";
  error?: string | null;
};

export type LLMValidateResponse = {
  status: LLMStatus;
  checked: boolean;
};

type BriefingRunsResponse = {
  runs: BriefingRun[];
};

const jsonHeaders = { "Content-Type": "application/json" };
let csrfToken: string | null = null;

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  const method = (options?.method || "GET").toUpperCase();
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method) && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(url, { ...options, headers, credentials: "include" });
  if (!response.ok) {
    const raw = await response.text();
    const message = humanizeApiError(raw, response.status);
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("alphawave-auth-expired"));
    }
    throw new ApiError(message || `Request failed: ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

function humanizeApiError(raw: string, status: number) {
  if (!raw) return `Request failed: ${status}`;
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown };
    const detail = parsed.detail ?? parsed.message;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
          return "";
        })
        .filter(Boolean)
        .join(" · ");
    }
  } catch {
    return raw;
  }
  return raw;
}

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function fetchAuthSession() {
  return request<AuthSession>("/api/auth/session").then((session) => {
    if (session.csrf_token) setCsrfToken(session.csrf_token);
    return session;
  });
}

export function login(email: string, password: string) {
  return request<LoginResponse>("/api/auth/login", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ email, password }),
  }).then((response) => {
    setCsrfToken(response.csrf_token);
    return response;
  });
}

export function logout() {
  return request<{ status: string }>("/api/auth/logout", { method: "POST" }).finally(() => setCsrfToken(null));
}

export function fetchTasks(status = "active", q = "", scope = "") {
  const params = new URLSearchParams({ status });
  if (q.trim()) params.set("q", q.trim());
  if (scope.trim()) params.set("scope", scope.trim());
  return request<TaskListResponse>(`/api/tasks?${params.toString()}`);
}

export function createTask(payload: string | TaskCreatePayload) {
  const body = typeof payload === "string" ? { title: payload, auto_classify: true } : payload;
  return request<Task>("/api/tasks", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(body),
  });
}

export function bulkCreateTasks(text: string) {
  return request<TaskListResponse>("/api/tasks/bulk", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ text, auto_classify: true }),
  });
}

export function patchTask(id: string, patch: TaskPatch) {
  return request<Task>(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: jsonHeaders,
    body: JSON.stringify(patch),
  });
}

export function createTaskTrelloCard(id: string, targetState = "pending") {
  return request<Confirmation>(`/api/tasks/${id}/create-trello-card`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ target_state: targetState }),
  });
}

export function suggestTaskDetails(id: string, allowExistingFieldUpdates = false) {
  return request<TaskSuggestionResponse>(`/api/tasks/${id}/suggest-details`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ include_notes: true, include_checklist: false, allow_existing_field_updates: allowExistingFieldUpdates }),
  });
}

export function applyTaskSuggestions(
  id: string,
  suggestions: Record<string, unknown>,
  fields: string[],
  allowExistingFieldUpdates = false,
  source = "unknown",
) {
  return request<TaskSuggestionApplyResponse>(`/api/tasks/${id}/apply-suggestions`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ suggestions, fields, allow_existing_field_updates: allowExistingFieldUpdates, source }),
  });
}

export function fetchTaskDetailGaps(id: string) {
  return request<TaskDetailGaps>(`/api/tasks/${id}/detail-gaps`);
}

export function suggestTaskDetailsBulk(taskIds: string[], limit = 10) {
  return request<BulkTaskSuggestionResponse>("/api/tasks/suggest-details-bulk", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({
      task_ids: taskIds,
      limit,
      include_notes: true,
      include_checklist: false,
      allow_existing_field_updates: false,
    }),
  });
}

export function applyTaskSuggestionsBulk(items: BulkTaskSuggestionApplyItem[]) {
  return request<BulkTaskSuggestionApplyResponse>("/api/tasks/apply-suggestions-bulk", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ items }),
  });
}

export function createTaskSuggestionConfirmations(limit = 25) {
  return request<TaskSuggestionConfirmationResponse>("/api/tasks/suggest-details-confirmations", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ limit, include_notes: true }),
  });
}

export function fetchIncompleteTaskDetails(limit = 25, q = "", taskId = "") {
  const params = new URLSearchParams({ limit: String(limit) });
  if (q.trim()) params.set("q", q.trim());
  if (taskId) params.set("task_id", taskId);
  return request<IncompleteTaskDetails>(`/api/tasks/incomplete-details?${params.toString()}`);
}

export function completeTaskDetails(
  id: string,
  expectedUpdatedAt: string,
  expectedVersionToken: string,
  patch: TaskPatch,
) {
  return request<ManualTaskDetailsResponse>(`/api/tasks/${id}/complete-details`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({
      expected_updated_at: expectedUpdatedAt,
      expected_version_token: expectedVersionToken,
      ...patch,
    }),
  });
}

export function completeTask(id: string) {
  return request<Task>(`/api/tasks/${id}/complete`, { method: "POST" });
}

export function deleteTask(id: string) {
  return request<Task>(`/api/tasks/${id}`, { method: "DELETE" });
}

export function restoreTask(id: string) {
  return request<Task>(`/api/tasks/${id}/restore`, { method: "POST" });
}

export function permanentlyDeleteTask(id: string) {
  return request<TrashDeleteResponse>(`/api/tasks/${id}/permanent`, { method: "DELETE" });
}

export function permanentlyDeleteTrashTasks(taskIds: string[]) {
  return request<TrashDeleteResponse>("/api/tasks/trash/delete-bulk", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ task_ids: taskIds }),
  });
}

export function emptyTrash() {
  return request<TrashDeleteResponse>("/api/tasks/trash/empty", { method: "POST" });
}

export function restoreTrashTasks(taskIds: string[]) {
  return request<TrashRestoreResponse>("/api/tasks/trash/restore-bulk", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ task_ids: taskIds }),
  });
}

export function reorderTasks(taskIds: string[]) {
  return request<TaskListResponse>("/api/tasks/reorder", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ task_ids: taskIds }),
  });
}

export function proposePrioritySort() {
  return request<SortProposal>("/api/tasks/sort/propose", { method: "POST" });
}

export function applyPrioritySort(confirmationId: string) {
  return request<TaskListResponse>("/api/tasks/sort/apply", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
}

export function fetchReminders(status?: string) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const query = params.toString();
  return request<ReminderListResponse>(`/api/reminders${query ? `?${query}` : ""}`);
}

export function createReminder(message: string, remindAt: string) {
  return request<Reminder>("/api/reminders", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ message, remind_at: remindAt, source: "ui" }),
  });
}

export function cancelReminder(id: string) {
  return request<Reminder>(`/api/reminders/${id}/cancel`, { method: "POST" });
}

export function fetchTodayPlan() {
  return request<TodayPlan>("/api/planning/today");
}

export function fetchNowPlan() {
  return request<NowPlan>("/api/planning/now");
}

export function fetchBriefingStatus() {
  return request<BriefingStatus>("/api/briefing/status");
}

export function generateBriefing() {
  return request<BriefingPayload>("/api/briefing/generate", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ manual: true }),
  });
}

export function sendTestBriefing() {
  return request<{ status: string; id: string }>("/api/briefing/send-test", { method: "POST" });
}

export function fetchBriefingRuns() {
  return request<BriefingRunsResponse>("/api/briefing/runs");
}

export function fetchConfirmations() {
  return request<ConfirmationListResponse>("/api/confirmations");
}

export function confirmConfirmation(id: string) {
  return request<{ message: string }>(`/api/confirmations/${id}/confirm`, { method: "POST" });
}

export function cancelConfirmation(id: string) {
  return request<{ message: string }>(`/api/confirmations/${id}/cancel`, { method: "POST" });
}

export function bulkConfirmConfirmations(confirmationIds: string[]) {
  return request<BackgroundJobQueued>("/api/confirmations/bulk-confirm", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ confirmation_ids: confirmationIds }),
  });
}

export function bulkCancelConfirmations(confirmationIds: string[]) {
  return request<ConfirmationBulkResponse>("/api/confirmations/bulk-cancel", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ confirmation_ids: confirmationIds }),
  });
}

export function fetchJob(jobId: string) {
  return request<BackgroundJob>(`/api/jobs/${jobId}`);
}

export function proposeTrelloCreateCard(boardAlias: string, title: string, listName = "pending") {
  return request<Confirmation>("/api/trello/cards/propose", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ board_alias: boardAlias, title, list_name: listName }),
  });
}

export function createTrelloCardNow(payload: {
  board_alias: string;
  title: string;
  description?: string | null;
  due_at?: string | null;
  priority?: Task["priority_label"];
}) {
  return request<Confirmation>("/api/trello/cards/create-now", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function proposeTrelloMove(taskId: string, targetState: string) {
  return request<Confirmation>(`/api/trello/cards/${taskId}/move/propose`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ target_state: targetState }),
  });
}

export function proposeTrelloRename(taskId: string, title: string) {
  return request<Confirmation>(`/api/trello/cards/${taskId}/rename/propose`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ title }),
  });
}

export function proposeTrelloDue(taskId: string, dueAt: string | null) {
  return request<Confirmation>(`/api/trello/cards/${taskId}/due/propose`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ due_at: dueAt }),
  });
}

export function fetchSettings() {
  return request<AppSettingsResponse>("/api/settings");
}

export function patchSettings(payload: DynamicApiObject) {
  return request<AppSettingsResponse>("/api/settings", {
    method: "PATCH",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function fetchSettingsSchema() {
  return request<SettingsSchema>("/api/settings/schema");
}

export function discoverTrelloLists() {
  return request<TrelloDiscovery>("/api/settings/trello/discover", { method: "POST" });
}

export function fetchConfiguredTrelloBoards() {
  return request<TrelloConfiguredBoards>("/api/settings/trello/boards");
}

export function discoverTrelloBoards() {
  return request<TrelloBoardDiscovery>("/api/settings/trello/discover-boards");
}

export function createConfiguredTrelloBoard(payload: { alias: string; name: string; board_id: string; auto_confirm_writes?: boolean }) {
  return request<AppSettingsResponse>("/api/settings/trello/boards", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function patchConfiguredTrelloBoard(alias: string, payload: DynamicApiObject) {
  return request<AppSettingsResponse>(`/api/settings/trello/boards/${encodeURIComponent(alias)}`, {
    method: "PATCH",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function disableConfiguredTrelloBoard(alias: string) {
  return request<AppSettingsResponse>(`/api/settings/trello/boards/${encodeURIComponent(alias)}/disable`, { method: "POST" });
}

export function fetchTrelloBoardLists(boardId: string) {
  return request<{ board_id: string; lists: TrelloList[] }>(`/api/settings/trello/boards/${encodeURIComponent(boardId)}/lists`);
}

export function validateTrelloMappings() {
  return request<TrelloValidation>("/api/settings/trello/validate", { method: "POST" });
}

export function fetchTrelloStatus() {
  return request<TrelloStatus>("/api/trello/status");
}

export function fetchTelegramIntegrationStatus() {
  return request<TelegramIntegrationStatus>("/api/integrations/telegram/status");
}

export function createTelegramLinkCode() {
  return request<TelegramLinkCode>("/api/integrations/telegram/link-code", { method: "POST" });
}

export function unlinkTelegramIntegration() {
  return request<TelegramIntegrationStatus>("/api/integrations/telegram/unlink", { method: "POST" });
}

export function fetchTrelloIntegrationStatus() {
  return request<TrelloIntegrationStatus>("/api/integrations/trello/status");
}

export function saveTrelloManualCredentials(payload: { api_key: string; token: string }) {
  return request<TrelloIntegrationStatus>("/api/integrations/trello/manual-credentials", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function revokeTrelloManualCredentials() {
  return request<TrelloIntegrationStatus>("/api/integrations/trello/revoke-credentials", { method: "POST" });
}

export function createSystemBackup() {
  return request<{ created: boolean; path: string | null; backup?: BackupMetadata | null; reason?: string | null }>("/api/backups", { method: "POST" });
}

export function fetchBackupStatus() {
  return request<BackupStatus>("/api/backups/status");
}

export function fetchBackups() {
  return request<{ backups: BackupMetadata[] }>("/api/backups");
}

export function validateBackup(backupId: string) {
  return request<{ backup: BackupMetadata }>(`/api/backups/${encodeURIComponent(backupId)}/validate`, { method: "POST" });
}

export function fetchBackupRestorePlan(backupId: string) {
  return request<BackupRestorePlan>(`/api/backups/${encodeURIComponent(backupId)}/restore-plan`, { method: "POST" });
}

export function fetchSystemStatus() {
  return request<SystemStatus>("/api/system/status");
}

export function fetchInstallationReadiness() {
  return request<InstallationReadiness>("/api/system/installation-readiness");
}

export function fetchSystemDiagnostics() {
  return request<SystemDiagnostics>("/api/system/diagnostics");
}

export function fetchLLMStatus() {
  return request<LLMStatus>("/api/integrations/openai/status");
}

export function fetchOpenAIModels() {
  return request<OpenAIModelCatalog>("/api/integrations/openai/models");
}

export function refreshOpenAIModels() {
  return request<OpenAIModelCatalog>("/api/integrations/openai/models/refresh", { method: "POST" });
}

export function validateLLM() {
  return request<LLMValidateResponse>("/api/integrations/openai/validate", { method: "POST" });
}

export function saveOpenAICredentials(apiKey: string) {
  return request<LLMStatus>("/api/integrations/openai/credentials", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function saveOpenAISettings(payload: { enabled?: boolean; model?: string }) {
  return request<LLMStatus>("/api/integrations/openai/settings", {
    method: "PATCH",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
}

export function revokeOpenAICredentials() {
  return request<LLMStatus>("/api/integrations/openai/credentials", { method: "DELETE" });
}
