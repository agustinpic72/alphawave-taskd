import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ArchiveRestore,
  ArrowLeft,
  BadgeCheck,
  Bell,
  Check,
  CheckCircle2,
  Circle,
  CircleHelp,
  ExternalLink,
  GripVertical,
  ListFilter,
  LoaderCircle,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  SunMedium,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, type KeyboardEvent, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  bulkCreateTasks,
  bulkCancelConfirmations,
  bulkConfirmConfirmations,
  cancelConfirmation,
  cancelReminder,
  completeTaskDetails,
  completeTask,
  applyTaskSuggestionsBulk,
  applyTaskSuggestions,
  createTrelloCardNow,
  createTaskTrelloCard,
  confirmConfirmation,
  createReminder,
  createTask,
  emptyTrash,
  applyPrioritySort,
  fetchConfirmations,
  fetchJob,
  fetchBriefingRuns,
  fetchBriefingStatus,
  deleteTask,
  fetchNowPlan,
  fetchReminders,
  fetchTasks,
  fetchTodayPlan,
  fetchSettings,
  fetchSettingsSchema,
  fetchTelegramIntegrationStatus,
  fetchTrelloIntegrationStatus,
  fetchTrelloBoardLists,
  discoverTrelloLists,
  discoverTrelloBoards,
  createSystemBackup,
  fetchBackupStatus,
  fetchBackups,
  fetchBackupRestorePlan,
  fetchAuthSession,
  fetchLLMStatus,
  fetchOpenAIModels,
  fetchIncompleteTaskDetails,
  fetchInstallationReadiness,
  fetchSystemDiagnostics,
  fetchSystemStatus,
  fetchTrelloStatus,
  createTelegramLinkCode,
  generateBriefing,
  login,
  logout,
  patchTask,
  patchSettings,
  permanentlyDeleteTask,
  permanentlyDeleteTrashTasks,
  proposePrioritySort,
  proposeTrelloCreateCard,
  proposeTrelloDue,
  proposeTrelloMove,
  proposeTrelloRename,
  reorderTasks,
  restoreTask,
  restoreTrashTasks,
  revokeTrelloManualCredentials,
  revokeOpenAICredentials,
  refreshOpenAIModels,
  sendTestBriefing,
  saveTrelloManualCredentials,
  saveOpenAICredentials,
  saveOpenAISettings,
  suggestTaskDetailsBulk,
  suggestTaskDetails,
  validateBackup,
  validateLLM,
  validateTrelloMappings,
  unlinkTelegramIntegration,
  type AppSettingsResponse,
  ApiError,
  type AuthSession,
  type BackgroundJob,
  type BackgroundJobQueued,
  type InstallationReadiness,
  type IncompleteTaskCandidate,
  type IncompleteTaskDetails,
  type Reminder,
  type Confirmation,
  type ConfirmationBulkResponse,
  type TrelloList,
  type BriefingPayload,
  type BriefingRun,
  type BriefingStatus,
  type BackupStatus,
  type BackupMetadata,
  type BackupRestorePlan,
  type LLMStatus,
  type OpenAIModelCatalog,
  type Task,
  type BulkTaskSuggestionApplyItem,
  type BulkTaskSuggestionResponse,
  type TaskSuggestionResponse,
  type NowPlan,
  type PlanItem,
  type PriorityExplanation,
  type SortProposal,
  type SystemStatus,
  type SortProposalItem,
  type TodayPlan,
  type TrelloDiscovery,
  type TrelloAvailableBoard,
  type TrelloStatus,
  type TelegramIntegrationStatus,
  type TelegramLinkCode,
  type TrelloIntegrationStatus,
} from "./api";
import { TimezoneCombobox, formatTimezoneLabel } from "./components/TimezoneCombobox";
import { OpenAIModelCombobox } from "./components/OpenAIModelCombobox";
import { TimeField } from "./components/TimeField";

type View = "todo" | "today" | "now" | "completed" | "trash" | "reminders" | "confirmations" | "briefing" | "settings";
type ToastKind = "success" | "info" | "warning" | "error" | "loading";
type ToastMessage = {
  id: string;
  kind: ToastKind;
  message: string;
  persistent?: boolean;
};
type AISuggestionProgressState = {
  phase: "preparing" | "generating" | "completed" | "partial_success" | "error";
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  currentTaskTitle: string | null;
  startedAt: number | null;
  error: string | null;
  isDeterminate: boolean;
};
type TaskCreateDraft = {
  title: string;
  scope: string;
  priority_label: Task["priority_label"];
  due_at: string;
  effort_bucket: Task["effort_bucket"];
  context_bucket: Task["context_bucket"];
  estimated_minutes: string;
  create_in_trello: boolean;
};
type WeekendModeContext = {
  enabled: boolean;
  active: boolean;
  activeToday: boolean;
  activeDays: string[];
  activeScopes: string[];
  timezone: string;
  nextActiveDay: string | null;
};
type TrelloBoardSummary = {
  alias: string;
  name: string;
  enabled: boolean;
  configured: boolean;
  workflow_states?: Array<Record<string, any>>;
  states?: Record<string, any>;
};
type TrelloTaskLinkInfo =
  | { status: "linkable"; board: TrelloBoardSummary; listName: string }
  | { status: "missing_mapping"; board: TrelloBoardSummary }
  | { status: "not_applicable"; board?: TrelloBoardSummary };
type Loadable<T> =
  | { state: "idle" }
  | { state: "loading"; data?: T; updatedAt?: string }
  | { state: "ready"; data: T; updatedAt: string }
  | { state: "stale"; data: T; updatedAt: string; error: string }
  | { state: "error"; error: string };

const fallbackScopes = ["Inbox", "Personal"];
const backupPageSize = 5;
const weekendBannerViews: View[] = ["todo", "today", "now", "briefing"];
const weekdayOrder = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const priorities = [
  { label: "Sin prioridad", value: "" },
  { label: "Alta", value: "high" },
  { label: "Media alta", value: "medium_high" },
  { label: "Media", value: "medium" },
  { label: "Baja", value: "low" },
];
const contexts = [
  { label: "Sin contexto", value: "" },
  { label: "Trabajo profundo", value: "deep_work" },
  { label: "Tarea rápida", value: "quick_task" },
  { label: "Llamada/contacto", value: "call" },
  { label: "Errand/personal", value: "errand" },
  { label: "Admin", value: "admin" },
  { label: "Revisión", value: "review" },
];
const priorityPresetOrders: Record<string, string[]> = {
  balanced: [
    "due_date",
    "manual_priority",
    "urgency",
    "impact",
    "blocking",
    "stale_in_progress",
    "source_priority",
    "effort",
    "age",
  ],
  deadlines_first: [
    "due_date",
    "urgency",
    "blocking",
    "manual_priority",
    "stale_in_progress",
    "impact",
    "source_priority",
    "effort",
    "age",
  ],
  quick_wins: [
    "effort",
    "due_date",
    "manual_priority",
    "urgency",
    "impact",
    "blocking",
    "source_priority",
    "stale_in_progress",
    "age",
  ],
  deep_work: [
    "effort",
    "impact",
    "manual_priority",
    "blocking",
    "due_date",
    "urgency",
    "stale_in_progress",
    "source_priority",
    "age",
  ],
};
const priorityCriterionDescriptions: Record<string, string> = {
  due_date: "Vencidas, de hoy o próximas suben según cercanía.",
  manual_priority: "Prioridad marcada localmente por vos.",
  source_priority: "Prioridad que llega desde Trello cuando aplica.",
  urgency: "Requieren movimiento pronto.",
  impact: "Aportan más valor si se resuelven.",
  blocking: "Desbloquean a otra tarea o persona.",
  effort: "Quick wins suben; trabajo profundo se pondera con impacto.",
  stale_in_progress: "Quedaron en EN PROCESO demasiado tiempo.",
  age: "Llevan tiempo acumulado sin resolverse.",
};
const settingsSections = [
  { id: "settings-status", label: "Estado" },
  { id: "settings-general", label: "General" },
  { id: "settings-weekend", label: "Modo fin de semana" },
  { id: "settings-briefing", label: "Briefing" },
  { id: "settings-reminders", label: "Recordatorios" },
  { id: "settings-trello", label: "Trello" },
  { id: "settings-priority", label: "Prioridad" },
  { id: "settings-backups", label: "Backups" },
  { id: "settings-advanced", label: "Avanzado" },
];
const emptyBackupStatus: BackupStatus = { latest_path: null, latest_created_at: null };
const emptyBackupList: BackupMetadata[] = [];

type TrashConfirmAction =
  | { type: "single-delete"; tasks: Task[] }
  | { type: "bulk-delete"; tasks: Task[] }
  | { type: "empty"; tasks: Task[] };

type SettingsSaveState =
  | { status: "idle" }
  | { status: "saved"; savedAt: string }
  | { status: "error"; message: string };

function LoginScreen({
  ownerExists,
  error,
  onLogin,
}: {
  ownerExists: boolean;
  error: string | null;
  onLogin: (email: string, password: string) => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) return;
    setSubmitting(true);
    try {
      await onLogin(email.trim(), password);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="app-shell auth-shell">
      <section className="auth-panel" aria-label="Iniciar sesión">
        <div className="auth-brand">
          <img className="brand-mark" src="/brand-mark.svg" width="48" height="48" alt="" aria-hidden="true" />
          <div>
            <p className="eyebrow">AlphaWave TaskD</p>
            <h1>TODO</h1>
          </div>
        </div>
        {ownerExists ? (
          <form className="auth-form" onSubmit={handleSubmit}>
            <label>
              Email
              <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
            </label>
            {error ? <div className="notice auth-notice" role="alert">{error}</div> : null}
            <button className="priority-button" type="submit" disabled={submitting || !email.trim() || !password}>
              {submitting ? "Entrando…" : "Entrar"}
            </button>
          </form>
        ) : (
          <div className="notice auth-notice" role="alert">
            No hay usuario owner configurado. Ejecutá <code>scripts/admin/create-owner.sh</code> en el servidor.
          </div>
        )}
      </section>
    </main>
  );
}

export function App() {
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [view, setView] = useState<View>("todo");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [completed, setCompleted] = useState<Task[]>([]);
  const [trash, setTrash] = useState<Task[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [confirmations, setConfirmations] = useState<Confirmation[]>([]);
  const [briefingStatus, setBriefingStatus] = useState<BriefingStatus | null>(null);
  const [briefingRuns, setBriefingRuns] = useState<BriefingRun[]>([]);
  const [briefingPreview, setBriefingPreview] = useState<BriefingPayload | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettingsResponse | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, any> | null>(null);
  const [settingsSchema, setSettingsSchema] = useState<Record<string, any> | null>(null);
  const [trelloDiscovery, setTrelloDiscovery] = useState<TrelloDiscovery | null>(null);
  const [trelloBoardDiscovery, setTrelloBoardDiscovery] = useState<TrelloAvailableBoard[]>([]);
  const [trelloStatus, setTrelloStatus] = useState<TrelloStatus | null>(null);
  const [backupStatus, setBackupStatus] = useState<BackupStatus | null>(null);
  const [backupList, setBackupList] = useState<BackupMetadata[]>(emptyBackupList);
  const [backupRestorePlan, setBackupRestorePlan] = useState<BackupRestorePlan | null>(null);
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);
  const [openAIModels, setOpenAIModels] = useState<OpenAIModelCatalog | null>(null);
  const openAIModelsLoadGeneration = useRef(0);
  const [systemStatusState, setSystemStatusState] = useState<Loadable<SystemStatus>>({ state: "idle" });
  const systemStatus = loadableData(systemStatusState);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaveState, setSettingsSaveState] = useState<SettingsSaveState>({ status: "idle" });
  const [todayPlan, setTodayPlan] = useState<TodayPlan>({ groups: [] });
  const [nowPlan, setNowPlan] = useState<NowPlan>({ recommended: [], afterwards: [], avoid: [] });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [bulkText, setBulkText] = useState("");
  const [taskCreateOpen, setTaskCreateOpen] = useState(false);
  const [reminderMessage, setReminderMessage] = useState("");
  const [reminderAt, setReminderAt] = useState("");
  const [trelloBoard, setTrelloBoard] = useState("");
  const [trelloCardTitle, setTrelloCardTitle] = useState("");
  const [trelloChoiceTask, setTrelloChoiceTask] = useState<Task | null>(null);
  const [trelloRestoreTask, setTrelloRestoreTask] = useState<Task | null>(null);
  const [trelloLinkingTaskId, setTrelloLinkingTaskId] = useState<string | null>(null);
  const [detailSuggestions, setDetailSuggestions] = useState<{ task: Task; response: TaskSuggestionResponse } | null>(null);
  const [bulkSuggestionsOpen, setBulkSuggestionsOpen] = useState(false);
  const [manualDetailQueue, setManualDetailQueue] = useState<{ details: IncompleteTaskDetails; openAIAvailable: boolean } | null>(null);
  const [incompleteDetailsFilter, setIncompleteDetailsFilter] = useState<IncompleteTaskDetails | null>(null);
  const [inboxTriageOpen, setInboxTriageOpen] = useState(false);
  const [suggestingDetailsTaskId, setSuggestingDetailsTaskId] = useState<string | null>(null);
  const [individualSuggestionProgress, setIndividualSuggestionProgress] = useState<AISuggestionProgressState | null>(null);
  const [taskExitIntent, setTaskExitIntent] = useState<Record<string, "restore" | "delete">>({});
  const [sortProposal, setSortProposal] = useState<SortProposal | null>(null);
  const [prioritizing, setPrioritizing] = useState(false);
  const [trashSelectionMode, setTrashSelectionMode] = useState(false);
  const [selectedTrashIds, setSelectedTrashIds] = useState<string[]>([]);
  const [trashConfirmAction, setTrashConfirmAction] = useState<TrashConfirmAction | null>(null);
  const [query, setQuery] = useState("");
  const [settingsScrollTarget, setSettingsScrollTarget] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);
  const isSuggestingDetails = suggestingDetailsTaskId !== null;
  const weekendMode = useMemo(() => buildWeekendMode(appSettings?.settings), [appSettings]);
  const configuredTrelloBoards = useMemo(() => trelloBoardsFromSettings(appSettings?.settings), [appSettings]);
  const trelloScopeSet = useMemo(() => new Set(configuredTrelloBoards.filter((board) => board.enabled).map((board) => board.alias)), [configuredTrelloBoards]);
  const availableTaskScopes = useMemo(
    () => availableScopesFromSettings(appSettings?.available_scopes),
    [appSettings?.available_scopes],
  );
  const appUnlocked = !authSession?.auth_enabled || authSession.authenticated;

  useEffect(() => {
    fetchAuthSession()
      .then((session) => setAuthSession(session))
      .catch((nextError: unknown) => setError(readError(nextError)))
      .finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    const refreshAuth = () => {
      fetchAuthSession()
        .then((session) => setAuthSession(session))
        .catch(() => setAuthSession({ auth_enabled: true, authenticated: false, owner_exists: true, user: null, csrf_token: null }));
    };
    window.addEventListener("alphawave-auth-expired", refreshAuth);
    return () => window.removeEventListener("alphawave-auth-expired", refreshAuth);
  }, []);

  const loadAll = useCallback(async () => {
    if (!appUnlocked) return;
    setError(null);
    const [
      activeResponse,
      completedResponse,
      trashResponse,
      reminderResponse,
      confirmationResponse,
      briefingStatusResponse,
      briefingRunsResponse,
      todayResponse,
      nowResponse,
    ] = await Promise.all([
      fetchTasks("active", deferredQuery),
      fetchTasks("completed", deferredQuery),
      fetchTasks("deleted", deferredQuery),
      fetchReminders(),
      fetchConfirmations(),
      fetchBriefingStatus(),
      fetchBriefingRuns(),
      fetchTodayPlan(),
      fetchNowPlan(),
    ]);
    setTasks(activeResponse.tasks);
    setCompleted(completedResponse.tasks);
    setTrash(trashResponse.tasks);
    setReminders(reminderResponse.reminders);
    setConfirmations(confirmationResponse.confirmations);
    setBriefingStatus(briefingStatusResponse);
    setBriefingRuns(briefingRunsResponse.runs);
    setTodayPlan(todayResponse);
    setNowPlan(nowResponse);
  }, [appUnlocked, deferredQuery]);

  useEffect(() => {
    if (!appUnlocked || authLoading) return;
    loadAll().catch((nextError: unknown) => setError(readError(nextError)));
  }, [appUnlocked, authLoading, loadAll]);

  useEffect(() => {
    if (!appUnlocked || authLoading || appSettings) return;
    fetchSettings()
      .then((settingsResponse) => setAppSettings(settingsResponse))
      .catch(() => undefined);
  }, [appSettings, appUnlocked, authLoading]);

  const refreshSystemStatus = useCallback(async () => {
    setSystemStatusState((current) => {
      const data = loadableData(current);
      const updatedAt = loadableUpdatedAt(current);
      return data ? { state: "loading", data, updatedAt } : { state: "loading" };
    });
    try {
      const nextStatus = await fetchSystemStatus();
      setSystemStatusState({ state: "ready", data: nextStatus, updatedAt: statusGeneratedAt(nextStatus) });
      return nextStatus;
    } catch (nextError) {
      const message = readError(nextError);
      setSystemStatusState((current) => {
        const data = loadableData(current);
        const updatedAt = loadableUpdatedAt(current);
        return data ? { state: "stale", data, updatedAt, error: message } : { state: "error", error: message };
      });
      throw nextError;
    }
  }, []);

  const loadSettings = useCallback(async () => {
    const catalogGeneration = ++openAIModelsLoadGeneration.current;
    setOpenAIModels(null);
    const openAIModelsRequest = fetchOpenAIModels().catch(() => null);
    const [settingsResponse, schemaResponse, trelloStatusResponse, backupStatusResponse, backupListResponse, llmStatusResponse, systemStatusResponse] = await Promise.all([
      fetchSettings(),
      fetchSettingsSchema(),
      fetchTrelloStatus().catch(() => null),
      fetchBackupStatus().catch(() => emptyBackupStatus),
      fetchBackups().catch(() => ({ backups: emptyBackupList })),
      fetchLLMStatus().catch(() => null),
      fetchSystemStatus()
        .then((data) => ({ state: "ready" as const, data, updatedAt: statusGeneratedAt(data) }))
        .catch((nextError: unknown) => ({ state: "error" as const, error: readError(nextError) })),
    ]);
    setAppSettings(settingsResponse);
    setSettingsDraft(structuredClone(settingsResponse.settings));
    setSettingsSchema(schemaResponse);
    setTrelloStatus(trelloStatusResponse);
    setBackupStatus(backupStatusResponse);
    setBackupList(backupListResponse.backups);
    setLlmStatus(llmStatusResponse);
    setSystemStatusState(systemStatusResponse);
    setSettingsDirty(false);
    void openAIModelsRequest.then(async (catalog) => {
      if (catalogGeneration !== openAIModelsLoadGeneration.current) return;
      setOpenAIModels(catalog);
      if (!catalog) return;
      setLlmStatus((current) => reconcileLLMStatusWithCatalog(current, catalog));
      const catalogAwareStatus = await fetchLLMStatus().catch(() => null);
      if (catalogGeneration === openAIModelsLoadGeneration.current && catalogAwareStatus) {
        setLlmStatus(catalogAwareStatus);
      }
    });
  }, []);

  async function mutateOpenAI(
    action: () => Promise<LLMStatus>,
    successMessage: string,
  ): Promise<LLMStatus> {
    setBusy(true);
    try {
      const nextStatus = await action();
      setLlmStatus(nextStatus);
      showTransientFeedback(successMessage);
      await loadSettings();
      await refreshSystemStatus().catch(() => undefined);
      return nextStatus;
    } catch (nextError) {
      showTransientFeedback(readError(nextError), "error");
      throw nextError;
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (appUnlocked && view === "settings" && !settingsDraft) {
      loadSettings().catch((nextError: unknown) => setError(readError(nextError)));
    }
  }, [appUnlocked, loadSettings, settingsDraft, view]);

  useEffect(() => {
    if (view !== "settings") return undefined;
    let cancelled = false;
    const refreshStatus = async () => {
      try {
        await refreshSystemStatus();
      } catch {
        if (cancelled) return;
      }
    };
    refreshStatus();
    const interval = window.setInterval(refreshStatus, 60000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [refreshSystemStatus, view]);

  useEffect(() => {
    if (view !== "settings" || !settingsScrollTarget || !settingsDraft) return undefined;
    const timer = window.setTimeout(() => {
      scrollToSettingsSection(settingsScrollTarget);
      setSettingsScrollTarget(null);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [settingsDraft, settingsScrollTarget, view]);

  useEffect(() => {
    setError(null);
  }, [view]);

  useEffect(() => {
    if (trelloScopeSet.has(trelloBoard)) return;
    const firstBoard = configuredTrelloBoards.find((board) => board.enabled)?.alias;
    if (firstBoard) setTrelloBoard(firstBoard);
  }, [configuredTrelloBoards, trelloBoard, trelloScopeSet]);

  const selectedTask = useMemo(() => {
    return [...tasks, ...completed, ...trash].find((task) => task.id === selectedId) ?? null;
  }, [completed, selectedId, tasks, trash]);
  const priorityExplanationByTaskId = useMemo(() => {
    const entries: Array<[string, PriorityExplanation]> = [];
    for (const group of todayPlan.groups) {
      for (const item of group.items) {
        if (item.priority) entries.push([item.task.id, item.priority]);
      }
    }
    for (const item of [...nowPlan.recommended, ...nowPlan.afterwards]) {
      if (item.priority) entries.push([item.task.id, item.priority]);
    }
    for (const item of sortProposalItems(sortProposal)) {
      if (item.priority) entries.push([item.task_id, item.priority]);
    }
    return new Map(entries);
  }, [nowPlan, sortProposal, todayPlan]);
  const selectedTrelloLinkInfo = useMemo(
    () => (selectedTask ? trelloLinkInfo(selectedTask, configuredTrelloBoards) : { status: "not_applicable" as const }),
    [configuredTrelloBoards, selectedTask],
  );
  const selectedTrashTasks = useMemo(() => {
    const selected = new Set(selectedTrashIds);
    return trash.filter((task) => selected.has(task.id));
  }, [selectedTrashIds, trash]);
  const incompleteFilterIds = useMemo(
    () => new Set(incompleteDetailsFilter?.tasks.map((task) => task.id) ?? []),
    [incompleteDetailsFilter],
  );
  const visibleTasks = incompleteDetailsFilter ? tasks.filter((task) => incompleteFilterIds.has(task.id)) : tasks;
  const attentionTasks = visibleTasks.filter((task) => task.task_kind === "attention");
  const inboxTasks = visibleTasks.filter((task) => task.scope === "Inbox" && task.task_kind !== "attention");
  const normalTasks = visibleTasks.filter((task) => task.scope !== "Inbox" && task.task_kind !== "attention");
  const incompleteFieldsByTaskId = useMemo(
    () => new Map(incompleteDetailsFilter?.tasks.map((task) => [task.id, task.missing_fields.length]) ?? []),
    [incompleteDetailsFilter],
  );
  const todayCount = todayPlan.groups.reduce((total, group) => total + group.items.length, 0);
  const nowCount = nowPlan.recommended.length + nowPlan.afterwards.length;
  const pendingReminderCount = reminders.filter((reminder) => reminder.status === "pending").length;
  const briefingCount = briefingStatus?.today_sent ? 1 : 0;

  useEffect(() => {
    setSelectedTrashIds((current) => current.filter((id) => trash.some((task) => task.id === id)));
  }, [trash]);

  useEffect(() => {
    if (view !== "trash") {
      setTrashSelectionMode(false);
      setSelectedTrashIds([]);
    }
  }, [view]);

  const pushToast = useCallback((kind: ToastKind, message: string, options: { persistent?: boolean; id?: string } = {}) => {
    const id = options.id ?? `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setToasts((current) => [
      ...current.filter((toast) => toast.id !== id),
      { id, kind, message, persistent: options.persistent },
    ]);
    return id;
  }, []);

  const updateToast = useCallback((id: string, patch: Partial<Omit<ToastMessage, "id">>) => {
    setToasts((current) => current.map((toast) => (toast.id === id ? { ...toast, ...patch } : toast)));
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (nextError) {
      pushToast("error", readError(nextError), { persistent: true });
    } finally {
      setBusy(false);
    }
  }

  function showTransientFeedback(message: string, kind: ToastKind = "success") {
    pushToast(kind, message);
  }

  async function pollBackgroundJob(jobId: string, toastId: string) {
    let lastJob: BackgroundJob | null = null;
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await wait(1400);
      const job = await fetchJob(jobId);
      lastJob = job;
      updateToast(toastId, {
        message: `Procesando ${job.processed}/${job.total} confirmación(es)...`,
        kind: "loading",
        persistent: true,
      });
      if (isTerminalJob(job)) {
        updateToast(toastId, {
          kind: job.failed > 0 ? (job.succeeded > 0 ? "warning" : "error") : "success",
          message: formatBackgroundJobSummary(job),
          persistent: job.failed > 0,
        });
        await loadAll();
        return;
      }
    }
    updateToast(toastId, {
      kind: "warning",
      message: lastJob ? `El job sigue en ${lastJob.status}. Revisá Confirmaciones en unos segundos.` : "No pude consultar el job en segundo plano.",
      persistent: true,
    });
  }

  function trackBulkConfirmationJob(job: BackgroundJobQueued) {
    const toastId = pushToast("loading", `Procesando ${job.total} confirmación(es) en segundo plano...`, { persistent: true });
    void pollBackgroundJob(job.job_id, toastId).catch((nextError: unknown) => {
      updateToast(toastId, { kind: "error", message: readError(nextError), persistent: true });
    });
  }

  function openWeekendSettings() {
    openSettingsSection("settings-weekend");
  }

  function openSettingsSection(sectionId: string) {
    setSettingsScrollTarget(sectionId);
    setView("settings");
  }

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    const cleanTitle = title.trim();
    if (!cleanTitle) return;
    run(async () => {
      const created = await createTask(cleanTitle);
      setTitle("");
      setSelectedId(created.id);
      showTransientFeedback(`Creada: ${created.title} · ${created.scope}`);
      await loadAll();
    });
  }

  async function createBulkTasksFromText(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    try {
      const created = await bulkCreateTasks(text);
      setBulkText("");
      setSelectedId(created.tasks[0]?.id ?? null);
      showTransientFeedback(
        "Creadas:\n" + created.tasks.map((task) => `- ${task.title} · ${task.scope}`).join("\n"),
      );
      await loadAll();
    } catch (nextError) {
      showTransientFeedback(readError(nextError), "error");
      throw nextError;
    } finally {
      setBusy(false);
    }
  }

  async function createTaskFromModal(draft: TaskCreateDraft) {
    const cleanTitle = draft.title.trim();
    if (!cleanTitle) return;
    setBusy(true);
    try {
      if (draft.create_in_trello && trelloScopeSet.has(draft.scope)) {
        const confirmation = await createTrelloCardNow({
          board_alias: draft.scope,
          title: cleanTitle,
          due_at: draft.due_at ? new Date(draft.due_at).toISOString() : null,
          priority: draft.priority_label,
        });
        showTransientFeedback(`Confirmación creada: ${confirmation.summary ?? cleanTitle}`);
      } else {
        const created = await createTask({
          title: cleanTitle,
          scope: draft.scope,
          priority_label: draft.priority_label,
          due_at: draft.due_at ? new Date(draft.due_at).toISOString() : null,
          effort_bucket: draft.effort_bucket,
          context_bucket: draft.context_bucket,
          estimated_minutes: draft.estimated_minutes ? Number(draft.estimated_minutes) : null,
          auto_classify: false,
        });
        setSelectedId(created.id);
        showTransientFeedback(`Creada: ${created.title} · ${created.scope}`);
      }
      await loadAll();
    } catch (nextError) {
      showTransientFeedback(readError(nextError), "error");
      throw nextError;
    } finally {
      setBusy(false);
    }
  }

  function handleCreateReminder(event: FormEvent) {
    event.preventDefault();
    if (!reminderMessage.trim() || !reminderAt) return;
    run(async () => {
      await createReminder(reminderMessage.trim(), new Date(reminderAt).toISOString());
      setReminderMessage("");
      setReminderAt("");
      await loadAll();
    });
  }

  function handleCreateTrelloCard(event: FormEvent) {
    event.preventDefault();
    if (!trelloCardTitle.trim()) return;
    run(async () => {
      const confirmation = await proposeTrelloCreateCard(trelloBoard, trelloCardTitle.trim());
      setTrelloCardTitle("");
      showTransientFeedback(formatTrelloActionFeedback(confirmation));
      await loadAll();
    });
  }

  function completeFromList(task: Task) {
    if (isTrelloLinkedTask(task)) {
      setTrelloChoiceTask(task);
      return;
    }
    run(async () => void (await completeTask(task.id), await loadAll()));
  }

  function proposeMove(task: Task, targetState: string) {
    run(async () => {
      const confirmation = await proposeTrelloMove(task.id, targetState);
      showTransientFeedback(formatTrelloActionFeedback(confirmation));
      setTrelloChoiceTask(null);
      await loadAll();
    });
  }

  function linkTaskToTrello(task: Task) {
    setTrelloLinkingTaskId(task.id);
    void run(async () => {
      const confirmation = await createTaskTrelloCard(task.id);
      showTransientFeedback(formatTrelloActionFeedback(confirmation));
      setSelectedId(task.id);
      await loadAll();
    }).finally(() => setTrelloLinkingTaskId(null));
  }

  function requestTaskSuggestions(task: Task) {
    setSuggestingDetailsTaskId(task.id);
    setIndividualSuggestionProgress({
      phase: "generating",
      total: 1,
      completed: 0,
      succeeded: 0,
      failed: 0,
      currentTaskTitle: task.title,
      startedAt: Date.now(),
      error: null,
      isDeterminate: false,
    });
    void suggestTaskDetails(task.id)
      .then((response) => {
        if (!Object.keys(response.suggestions).length) {
          setIndividualSuggestionProgress(null);
          showTransientFeedback("Esta tarea ya tiene suficientes detalles", "info");
          return;
        }
        setIndividualSuggestionProgress(null);
        setDetailSuggestions({ task, response });
        showTransientFeedback("Sugerencias listas");
      })
      .catch((nextError) => {
        setIndividualSuggestionProgress((current) =>
          current
            ? {
                ...current,
                phase: "error",
                completed: 1,
                failed: 1,
                error: readError(nextError),
                isDeterminate: true,
              }
            : null,
        );
      })
      .finally(() => setSuggestingDetailsTaskId(null));
  }

  function suggestFirstIncompleteTask() {
    setBulkSuggestionsOpen(true);
  }

  function applySelectedTaskSuggestions(task: Task, response: TaskSuggestionResponse, fields: string[]) {
    run(async () => {
      const suggestions = Object.fromEntries(Object.entries(response.suggestions).map(([field, item]) => [field, item.value]));
      const result = await applyTaskSuggestions(task.id, suggestions, fields, false, response.source);
      replaceTaskInState(result.task);
      setDetailSuggestions(null);
      showTransientFeedback(suggestionApplySummary(result.applied_fields.length, result.remaining_missing_fields.length));
      await loadAll();
    });
  }

  function applyBulkSuggestionUpdates(updatedTasks: Task[]) {
    updatedTasks.forEach(replaceTaskInState);
    showTransientFeedback(`${updatedTasks.length} tarea(s) actualizada(s)`);
    void loadAll();
  }

  function openManualDetailQueue(details: IncompleteTaskDetails, openAIAvailable: boolean) {
    setBulkSuggestionsOpen(false);
    setManualDetailQueue({ details, openAIAvailable });
  }

  function showIncompleteTasks(details: IncompleteTaskDetails) {
    setBulkSuggestionsOpen(false);
    setIncompleteDetailsFilter(details);
    setView("todo");
    setSelectedId(null);
  }

  function handleManualDetailUpdate(
    updatedTask: Task,
    remainingMissingFields: string[],
    isCandidate: boolean,
  ) {
    replaceTaskInState(updatedTask);
    setIncompleteDetailsFilter((current) => {
      if (!current) return current;
      const nextTasks = isCandidate
        ? current.tasks.map((candidate) =>
            candidate.id === updatedTask.id
              ? { ...candidate, missing_fields: remainingMissingFields, missing_count: remainingMissingFields.length }
              : candidate,
          )
        : current.tasks.filter((candidate) => candidate.id !== updatedTask.id);
      return { ...current, tasks: nextTasks, total_candidates: Math.max(0, current.total_candidates - (isCandidate ? 0 : 1)) };
    });
  }

  function handleInboxTriageUpdate(updatedTask: Task, message?: string) {
    replaceTaskInState(updatedTask);
    if (message) showTransientFeedback(message);
    void loadAll();
  }

  function handleInboxTriageComplete(task: Task) {
    run(async () => {
      await completeTask(task.id);
      showTransientFeedback("Tarea completada");
      await loadAll();
    });
  }

  function handleInboxTriageDiscard(task: Task) {
    run(async () => {
      await deleteTask(task.id);
      showTransientFeedback("Tarea descartada");
      await loadAll();
    });
  }

  function replaceTaskInState(updatedTask: Task) {
    const replace = (items: Task[]) => items.map((item) => (item.id === updatedTask.id ? updatedTask : item));
    setTasks(replace);
    setCompleted(replace);
    setTrash(replace);
  }

  function localComplete(task: Task) {
    run(async () => {
      await completeTask(task.id);
      setTrelloChoiceTask(null);
      await loadAll();
    });
  }

  function restoreCompletedTask(task: Task, forceLocal = false) {
    if (isTrelloLinkedTask(task) && !forceLocal) {
      setTrelloRestoreTask(task);
      return;
    }
    runTaskExit(task, "restore", async () => {
      await restoreTask(task.id);
      setTrelloRestoreTask(null);
      showTransientFeedback("Tarea restaurada");
    });
  }

  function moveTaskToTrash(task: Task) {
    runTaskExit(task, "delete", async () => {
      await deleteTask(task.id);
      showTransientFeedback("Movida a Papelera");
    });
  }

  function runTaskExit(task: Task, intent: "restore" | "delete", action: () => Promise<void>) {
    run(async () => {
      setTaskExitIntent((current) => ({ ...current, [task.id]: intent }));
      try {
        await wait(170);
        await action();
        if (selectedId === task.id) setSelectedId(null);
        await loadAll();
      } finally {
        setTaskExitIntent((current) => {
          const next = { ...current };
          delete next[task.id];
          return next;
        });
      }
    });
  }

  function runTrashExit(tasksToAnimate: Task[], intent: "restore" | "delete", action: () => Promise<void>) {
    run(async () => {
      const ids = tasksToAnimate.map((task) => task.id);
      setTaskExitIntent((current) => ({
        ...current,
        ...Object.fromEntries(ids.map((id) => [id, intent])),
      }));
      try {
        await wait(170);
        await action();
        if (selectedId && ids.includes(selectedId)) setSelectedId(null);
        await loadAll();
      } finally {
        setTaskExitIntent((current) => {
          const next = { ...current };
          for (const id of ids) delete next[id];
          return next;
        });
      }
    });
  }

  function toggleTrashSelection(taskId: string) {
    setSelectedTrashIds((current) =>
      current.includes(taskId) ? current.filter((id) => id !== taskId) : [...current, taskId],
    );
  }

  function cancelTrashSelection() {
    setTrashSelectionMode(false);
    setSelectedTrashIds([]);
  }

  function restoreSelectedTrashTasks() {
    if (!selectedTrashTasks.length) return;
    const count = selectedTrashTasks.length;
    runTrashExit(selectedTrashTasks, "restore", async () => {
      await restoreTrashTasks(selectedTrashTasks.map((task) => task.id));
      cancelTrashSelection();
      showTransientFeedback(`${count} tarea${count === 1 ? "" : "s"} restaurada${count === 1 ? "" : "s"}`);
    });
  }

  function confirmTrashAction() {
    if (!trashConfirmAction) return;
    const action = trashConfirmAction;
    const ids = action.tasks.map((task) => task.id);
    const count = action.tasks.length;
    setTrashConfirmAction(null);

    if (action.type === "empty") {
      runTrashExit(action.tasks, "delete", async () => {
        await emptyTrash();
        cancelTrashSelection();
        showTransientFeedback("Papelera vaciada");
      });
      return;
    }

    runTrashExit(action.tasks, "delete", async () => {
      if (action.type === "single-delete") {
        await permanentlyDeleteTask(ids[0]);
        showTransientFeedback("Tarea eliminada definitivamente");
      } else {
        await permanentlyDeleteTrashTasks(ids);
        cancelTrashSelection();
        showTransientFeedback(`${count} tarea${count === 1 ? "" : "s"} eliminada${count === 1 ? "" : "s"} definitivamente`);
      }
    });
  }

  function openPrioritySortProposal() {
    setPrioritizing(true);
    void run(async () => {
      const proposal = await proposePrioritySort();
      setSortProposal(proposal);
      if (sortProposalItems(proposal).length < 2) {
        showTransientFeedback("No hay suficientes tareas activas para reordenar.");
      }
    }).finally(() => setPrioritizing(false));
  }

  function applySortProposal() {
    if (!sortProposal) return;
    run(async () => {
      const response = await applyPrioritySort(sortProposal.confirmation_id);
      setTasks(response.tasks);
      setSortProposal(null);
      showTransientFeedback("Orden aplicado");
      await loadAll();
    });
  }

  function updateSettingsDraft(path: string[], value: unknown) {
    setSettingsDraft((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      let pointer: Record<string, any> = next;
      for (const key of path.slice(0, -1)) {
        pointer[key] = pointer[key] ?? {};
        pointer = pointer[key];
      }
      pointer[path[path.length - 1]] = value;
      return next;
    });
    setSettingsDirty(true);
    setSettingsSaveState({ status: "idle" });
  }

  function saveSettings() {
    if (!settingsDraft) return;
    run(async () => {
      try {
        const response = await patchSettings(settingsPatchFromDraft(settingsDraft, appSettings?.settings));
        setAppSettings(response);
        setSettingsDraft(structuredClone(response.settings));
        setSettingsDirty(false);
        setSettingsSaveState({ status: "saved", savedAt: new Date().toISOString() });
        showTransientFeedback("Configuración guardada");
        const [nextBackupStatus, nextSystemStatus] = await Promise.all([
          fetchBackupStatus().catch(() => emptyBackupStatus),
          fetchSystemStatus().catch((nextError: unknown) => ({ error: readError(nextError) })),
        ]);
        setBackupStatus(nextBackupStatus);
        updateSystemStatusFromFetchResult(nextSystemStatus);
        await loadAll();
      } catch (nextError) {
        setSettingsSaveState({ status: "error", message: readError(nextError) });
        throw nextError;
      }
    });
  }

  function discardSettings() {
    if (!appSettings) return;
    setSettingsDraft(structuredClone(appSettings.settings));
    setSettingsDirty(false);
    setSettingsSaveState({ status: "idle" });
  }

  function updateSystemStatusFromFetchResult(result: SystemStatus | { error: string }) {
    if ("error" in result) {
      setSystemStatusState((current) => {
        const data = loadableData(current);
        const updatedAt = loadableUpdatedAt(current);
        return data ? { state: "stale", data, updatedAt, error: result.error } : { state: "error", error: result.error };
      });
      return;
    }
    setSystemStatusState({ state: "ready", data: result, updatedAt: statusGeneratedAt(result) });
  }

  function discoverTrelloSettings() {
    run(async () => {
      const response = await discoverTrelloLists();
      setTrelloDiscovery(response);
      showTransientFeedback("Listas Trello descubiertas", "info");
    });
  }

  function discoverAvailableTrelloBoards() {
    run(async () => {
      const response = await discoverTrelloBoards();
      setTrelloBoardDiscovery(response.boards);
      showTransientFeedback(response.boards.length ? "Boards Trello descubiertos" : "No encontré boards Trello abiertos", response.boards.length ? "info" : "warning");
    });
  }

  function validateTrelloSettings() {
    run(async () => {
      const response = await validateTrelloMappings();
      const [nextSettings, nextTrelloStatus, nextSystemStatus] = await Promise.all([
        fetchSettings(),
        fetchTrelloStatus().catch(() => null),
        fetchSystemStatus().catch((nextError: unknown) => ({ error: readError(nextError) })),
      ]);
      setTrelloStatus(nextTrelloStatus);
      updateSystemStatusFromFetchResult(nextSystemStatus);
      setAppSettings(nextSettings);
      setSettingsDraft(structuredClone(nextSettings.settings));
      setSettingsDirty(false);
      showTransientFeedback(response.status === "ok" ? "Mapeos Trello validados" : "Revisá los mapeos Trello faltantes", response.status === "ok" ? "success" : "warning");
    });
  }

  function createBackupNow() {
    run(async () => {
      const response = await createSystemBackup();
      const [nextBackupStatus, nextBackupList, nextSystemStatus] = await Promise.all([
        fetchBackupStatus().catch(() => emptyBackupStatus),
        fetchBackups().catch(() => ({ backups: backupList })),
        fetchSystemStatus().catch((nextError: unknown) => ({ error: readError(nextError) })),
      ]);
      setBackupStatus(nextBackupStatus);
      setBackupList(nextBackupList.backups);
      updateSystemStatusFromFetchResult(nextSystemStatus);
      showTransientFeedback(response.created ? "Backup creado" : response.reason || "No se creó backup.", response.created ? "success" : "warning");
    });
  }

  function validateBackupNow(backupId: string) {
    run(async () => {
      const response = await validateBackup(backupId);
      setBackupList((current) => current.map((item) => (item.id === backupId ? response.backup : item)));
      showTransientFeedback(response.backup.valid ? "Backup válido" : response.backup.validation.reason || "Backup inválido", response.backup.valid ? "success" : "warning");
    });
  }

  function openBackupRestorePlan(backupId: string) {
    run(async () => {
      const plan = await fetchBackupRestorePlan(backupId);
      setBackupRestorePlan(plan);
    });
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = tasks.findIndex((task) => task.id === active.id);
    const newIndex = tasks.findIndex((task) => task.id === over.id);
    const nextTasks = arrayMove(tasks, oldIndex, newIndex);
    setTasks(nextTasks);
    run(async () => {
      await reorderTasks(nextTasks.map((task) => task.id));
      await loadAll();
    });
  }

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const shellClassName = ["app-shell", view === "settings" ? "settings-shell" : "", weekendMode.active ? "app-weekend-mode" : ""]
    .filter(Boolean)
    .join(" ");
  const showWeekendBanner = weekendMode.active && weekendBannerViews.includes(view);

  async function handleLogin(email: string, password: string) {
    setError(null);
    try {
      await login(email, password);
      const session = await fetchAuthSession();
      setAuthSession(session);
      await loadAll();
    } catch (nextError) {
      setError(readError(nextError));
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    const session = await fetchAuthSession();
    setAuthSession(session);
    setTasks([]);
    setCompleted([]);
    setTrash([]);
    setReminders([]);
    setConfirmations([]);
    setSelectedId(null);
  }

  if (authLoading) {
    return (
      <main className="app-shell auth-shell">
        <section className="auth-panel">Cargando sesión…</section>
      </main>
    );
  }

  if (authSession?.auth_enabled && !authSession.authenticated) {
    return (
      <LoginScreen
        ownerExists={authSession.owner_exists}
        error={error}
        onLogin={handleLogin}
      />
    );
  }

  return (
    <main className={shellClassName}>
      <section className="workspace">
        <header className={view === "settings" ? "topbar settings-topbar" : "topbar"}>
          <button
            className="brand-lockup"
            type="button"
            onClick={() => setView("todo")}
            aria-label="Ir a TODO"
            title="Ir a TODO"
          >
            <img className="brand-mark" src="/brand-mark.svg" width="48" height="48" alt="" aria-hidden="true" />
            <div>
              <p className="eyebrow">AlphaWave TaskD</p>
              <h1>TODO</h1>
            </div>
          </button>
          {view !== "settings" ? (
            <div className="topbar-actions">
              <div className="searchbox">
                <Search size={17} aria-hidden="true" />
                <input
                  name="task-search"
                  autoComplete="off"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Buscar…"
                  aria-label="Buscar tareas"
                />
              </div>
              {weekendMode.active ? <WeekendModeBadge onConfigure={openWeekendSettings} /> : null}
              <button
                className="secondary-action subtle"
                type="button"
                onClick={suggestFirstIncompleteTask}
                disabled={busy || !tasks.length}
                title="Sugerir detalles para la primera tarea incompleta"
                data-testid="bulk-suggest-details-button"
              >
                <Sparkles size={17} aria-hidden="true" />
                {isSuggestingDetails ? "Analizando…" : "Sugerir detalles"}
              </button>
              <button
                className="priority-button"
                type="button"
                onClick={openPrioritySortProposal}
                disabled={busy || tasks.length < 2}
                title="Generar propuesta de prioridad"
                data-testid="priority-button"
              >
                <Sparkles size={17} aria-hidden="true" />
                {prioritizing ? "Priorizando…" : "Priorizar"}
              </button>
              {authSession?.auth_enabled ? (
                <button className="secondary-action subtle" type="button" onClick={() => void handleLogout()}>
                  Cerrar sesión
                </button>
              ) : null}
            </div>
          ) : weekendMode.active || authSession?.auth_enabled ? (
            <div className="topbar-actions settings-mode-actions">
              {weekendMode.active ? <WeekendModeBadge onConfigure={openWeekendSettings} /> : null}
              {authSession?.auth_enabled ? (
                <button className="secondary-action subtle" type="button" onClick={() => void handleLogout()}>
                  Cerrar sesión
                </button>
              ) : null}
            </div>
          ) : null}
        </header>

        {view !== "settings" ? (
          <PrimaryNav
            view={view}
            onChange={setView}
            todoCount={tasks.length}
            todayCount={todayCount}
            nowCount={nowCount}
          />
        ) : null}

        {error ? (
          <div className="notice" role="alert">
            {error}
          </div>
        ) : null}
        {showWeekendBanner ? <WeekendModeBanner weekendMode={weekendMode} onConfigure={openWeekendSettings} /> : null}

        {view === "todo" ? (
          <>
            {incompleteDetailsFilter ? (
              <div className="active-task-filter" role="status" data-testid="incomplete-details-filter">
                <ListFilter size={17} aria-hidden="true" />
                <span>
                  Detalles incompletos <strong>({incompleteDetailsFilter.tasks.length})</strong>
                </span>
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => setIncompleteDetailsFilter(null)}
                  title="Quitar filtro"
                  aria-label="Quitar filtro de detalles incompletos"
                >
                  <X size={17} aria-hidden="true" />
                </button>
              </div>
            ) : null}
            <section className="create-bar" aria-label="Crear tareas">
              <button className="new-task-button" type="button" onClick={() => setTaskCreateOpen(true)}>
                <Plus size={17} aria-hidden="true" />
                Nueva tarea
              </button>
              <form className="quick-add" onSubmit={handleCreate}>
                <input
                  name="new-task-title"
                  autoComplete="off"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Capturar rápido..."
                  disabled={busy}
                />
                <button type="submit" title="Agregar" aria-label="Agregar tarea" disabled={busy || !title.trim()}>
                  <Plus size={18} />
                </button>
              </form>
            </section>

            {attentionTasks.length ? (
              <TaskSection title="Requiere atención">
                <TaskList
                  tasks={attentionTasks}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  weekendMode={weekendMode}
                  incompleteFieldsByTaskId={incompleteFieldsByTaskId}
                  trelloLinkInfoForTask={(task) => trelloLinkInfo(task, configuredTrelloBoards)}
                />
              </TaskSection>
            ) : null}

            <TaskSection
              title="Inbox"
              meta={
                inboxTasks.length
                  ? `${inboxTasks.length} tarea${inboxTasks.length === 1 ? "" : "s"} para procesar`
                  : "Inbox limpio. Las tareas nuevas sin clasificar aparecerán acá."
              }
              actions={
                <button
                  className="secondary-action"
                  type="button"
                  onClick={() => setInboxTriageOpen(true)}
                  disabled={!inboxTasks.length}
                  data-testid="inbox-process-button"
                >
                  Procesar Inbox
                </button>
              }
            >
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={tasks.map((task) => task.id)} strategy={verticalListSortingStrategy}>
                  <TaskList
                    tasks={inboxTasks}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    onComplete={completeFromList}
                    onDelete={moveTaskToTrash}
                    exitIntentById={taskExitIntent}
                    weekendMode={weekendMode}
                    incompleteFieldsByTaskId={incompleteFieldsByTaskId}
                    trelloLinkInfoForTask={(task) => trelloLinkInfo(task, configuredTrelloBoards)}
                  />
                </SortableContext>
              </DndContext>
            </TaskSection>

            <TaskSection title="Tareas">
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={tasks.map((task) => task.id)} strategy={verticalListSortingStrategy}>
                  <TaskList
                    tasks={normalTasks}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    onComplete={completeFromList}
                    onDelete={moveTaskToTrash}
                    exitIntentById={taskExitIntent}
                    weekendMode={weekendMode}
                    incompleteFieldsByTaskId={incompleteFieldsByTaskId}
                    trelloLinkInfoForTask={(task) => trelloLinkInfo(task, configuredTrelloBoards)}
                  />
                </SortableContext>
              </DndContext>
            </TaskSection>
          </>
        ) : null}

        {view === "today" ? (
          <PlanTodayView
            plan={todayPlan}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onComplete={completeFromList}
            onDelete={moveTaskToTrash}
            exitIntentById={taskExitIntent}
            weekendMode={weekendMode}
            trelloLinkInfoForTask={(task) => trelloLinkInfo(task, configuredTrelloBoards)}
          />
        ) : null}

        {view === "now" ? (
          <PlanNowView
            plan={nowPlan}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onComplete={completeFromList}
            onDelete={moveTaskToTrash}
            exitIntentById={taskExitIntent}
            weekendMode={weekendMode}
            trelloLinkInfoForTask={(task) => trelloLinkInfo(task, configuredTrelloBoards)}
          />
        ) : null}

        {view === "completed" ? (
          <TaskSection title="Completadas">
            <TaskList
              tasks={completed}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onRestore={restoreCompletedTask}
              onDelete={moveTaskToTrash}
              deleteLabel="Mover a papelera"
              exitIntentById={taskExitIntent}
            />
          </TaskSection>
        ) : null}

        {view === "trash" ? (
          <TrashView
            tasks={trash}
            selectedId={selectedId}
            selectedTrashIds={selectedTrashIds}
            selectionMode={trashSelectionMode}
            busy={busy}
            exitIntentById={taskExitIntent}
            onSelect={setSelectedId}
            onToggleSelection={toggleTrashSelection}
            onStartSelection={() => setTrashSelectionMode(true)}
            onCancelSelection={cancelTrashSelection}
            onRestore={(task) =>
              runTaskExit(task, "restore", async () => {
                await restoreTask(task.id);
                showTransientFeedback("Tarea restaurada");
              })
            }
            onRestoreSelected={restoreSelectedTrashTasks}
            onPermanentDelete={(task) => setTrashConfirmAction({ type: "single-delete", tasks: [task] })}
            onPermanentDeleteSelected={() => setTrashConfirmAction({ type: "bulk-delete", tasks: selectedTrashTasks })}
            onEmptyTrash={() => setTrashConfirmAction({ type: "empty", tasks: trash })}
          />
        ) : null}

        {view === "reminders" ? (
          <ReminderView
            reminders={reminders}
            message={reminderMessage}
            remindAt={reminderAt}
            busy={busy}
            onMessageChange={setReminderMessage}
            onRemindAtChange={setReminderAt}
            onCreate={handleCreateReminder}
            onCancel={(reminder) => run(async () => void (await cancelReminder(reminder.id), await loadAll()))}
          />
        ) : null}

        {view === "confirmations" ? (
          <ConfirmationView
            confirmations={confirmations}
            trelloBoards={configuredTrelloBoards.filter((board) => board.enabled)}
            trelloBoard={trelloBoard}
            trelloCardTitle={trelloCardTitle}
            busy={busy}
            onBoardChange={setTrelloBoard}
            onTitleChange={setTrelloCardTitle}
            onCreateCard={handleCreateTrelloCard}
            onConfirm={(confirmation) =>
              run(async () => {
                const result = await confirmConfirmation(confirmation.id);
                showTransientFeedback(result.message);
                await loadAll();
              })
            }
            onCancel={(confirmation) =>
              run(async () => {
                const result = await cancelConfirmation(confirmation.id);
                showTransientFeedback(result.message);
                await loadAll();
              })
            }
            onBulkConfirm={async (selectedConfirmations) => {
              const job = await bulkConfirmConfirmations(selectedConfirmations.map((confirmation) => confirmation.id));
              trackBulkConfirmationJob(job);
              await loadAll();
              return job;
            }}
            onBulkCancel={async (selectedConfirmations) => {
              const result = await bulkCancelConfirmations(selectedConfirmations.map((confirmation) => confirmation.id));
              showTransientFeedback(formatBulkConfirmationSummary(result, "cancel"));
              await loadAll();
              return result;
            }}
          />
        ) : null}

        {view === "briefing" ? (
          <BriefingView
            status={briefingStatus}
            runs={briefingRuns}
            preview={briefingPreview}
            busy={busy}
            onGenerate={() =>
              run(async () => {
                const preview = await generateBriefing();
                setBriefingPreview(preview);
              })
            }
            onSendTest={() =>
              run(async () => {
                const result = await sendTestBriefing();
                showTransientFeedback(`Briefing enviado: ${result.status}`);
                await loadAll();
              })
            }
          />
        ) : null}

        {view === "settings" ? (
          <SettingsView
            settings={settingsDraft}
            schema={settingsSchema}
            availableScopes={appSettings?.available_scopes ?? fallbackScopes}
            selectedWeekendScopes={appSettings?.selected_weekend_scopes ?? []}
            discovery={trelloDiscovery}
            boardDiscovery={trelloBoardDiscovery}
            trelloStatus={trelloStatus}
            backupStatus={backupStatus}
            backupList={backupList}
            llmStatus={llmStatus}
            openAIModels={openAIModels}
            briefingStatus={briefingStatus}
            systemStatus={systemStatus}
            systemStatusState={systemStatusState}
            onRefreshSystemStatus={() => void refreshSystemStatus().catch(() => undefined)}
            dirty={settingsDirty}
            saveState={settingsSaveState}
            busy={busy}
            onChange={updateSettingsDraft}
            onSave={saveSettings}
            onDiscard={discardSettings}
            onDiscoverTrello={discoverTrelloSettings}
            onDiscoverTrelloBoards={discoverAvailableTrelloBoards}
            onValidateTrello={validateTrelloSettings}
            onValidateLLM={() =>
              run(async () => {
                const response = await validateLLM();
                setLlmStatus(response.status);
                showTransientFeedback(llmStatusMessage(response.status), response.status.status === "error" ? "warning" : "success");
                await loadSettings();
                await refreshSystemStatus().catch(() => undefined);
              })
            }
            onRefreshOpenAIModels={async () => {
              try {
                const catalog = await refreshOpenAIModels();
                setOpenAIModels(catalog);
                setLlmStatus((current) => reconcileLLMStatusWithCatalog(current, catalog));
                const nextStatus = await fetchLLMStatus();
                setLlmStatus(nextStatus);
                showTransientFeedback(
                  catalog.error ?? "Catálogo de modelos actualizado.",
                  catalog.error ? "warning" : "success",
                );
                return catalog;
              } catch (nextError) {
                showTransientFeedback(readError(nextError), "error");
                throw nextError;
              }
            }}
            onSaveOpenAIKey={(apiKey) =>
              mutateOpenAI(() => saveOpenAICredentials(apiKey), "API key guardada de forma cifrada.")
            }
            onUpdateOpenAI={(payload) =>
              mutateOpenAI(() => saveOpenAISettings(payload), "Configuración de OpenAI actualizada.")
            }
            onRevokeOpenAI={() =>
              mutateOpenAI(() => revokeOpenAICredentials(), "API key revocada.")
            }
            onCreateBackup={createBackupNow}
            onValidateBackup={validateBackupNow}
            onOpenBackupRestorePlan={openBackupRestorePlan}
            onBackToTodo={() => setView("todo")}
          />
        ) : null}
      </section>

        {view !== "settings" ? (
        <DetailPanel
          task={selectedTask}
          priorityExplanation={selectedTask ? priorityExplanationByTaskId.get(selectedTask.id) ?? null : null}
          onClose={() => setSelectedId(null)}
          onSave={async (task, patch, reason) => {
            const updated = await patchTask(task.id, patch);
            replaceTaskInState(updated);
            if (reason === "manual") showTransientFeedback("Tarea guardada");
            await loadAll();
          }}
          onSaveError={(message) => showTransientFeedback(message, "error")}
          onTrelloMove={proposeMove}
          onTrelloRename={(task, title) =>
            run(async () => {
              const confirmation = await proposeTrelloRename(task.id, title);
              showTransientFeedback(formatTrelloActionFeedback(confirmation));
              await loadAll();
            })
          }
          onTrelloDue={(task, dueAt) =>
            run(async () => {
              const confirmation = await proposeTrelloDue(task.id, dueAt);
              showTransientFeedback(formatTrelloActionFeedback(confirmation));
              await loadAll();
            })
          }
          onComplete={completeFromList}
          onLocalComplete={localComplete}
          onCreateTrelloCard={linkTaskToTrello}
          onSuggestDetails={requestTaskSuggestions}
          suggestingDetails={suggestingDetailsTaskId === selectedTask?.id}
          onOpenTrelloSettings={() => openSettingsSection("settings-trello")}
          onRestore={restoreCompletedTask}
          onDelete={moveTaskToTrash}
          availableScopes={availableTaskScopes}
          trelloLinkInfo={selectedTrelloLinkInfo}
          linkingTrelloTaskId={trelloLinkingTaskId}
        />
      ) : null}
      <UtilityDock
        view={view}
        onChange={setView}
        completedCount={completed.length}
        trashCount={trash.length}
        reminderCount={pendingReminderCount}
        confirmationCount={confirmations.length}
        briefingCount={briefingCount}
      />
      {trashConfirmAction ? (
        <TrashConfirmModal
          action={trashConfirmAction}
          busy={busy}
          onConfirm={confirmTrashAction}
          onCancel={() => setTrashConfirmAction(null)}
        />
      ) : null}
      {trelloChoiceTask ? (
        <TrelloChoiceModal
          task={trelloChoiceTask}
          onMoveReview={() => proposeMove(trelloChoiceTask, "review")}
          onMoveCompleted={() => proposeMove(trelloChoiceTask, "completed")}
          onLocalOnly={() => localComplete(trelloChoiceTask)}
          onCancel={() => setTrelloChoiceTask(null)}
        />
      ) : null}
      {trelloRestoreTask ? (
        <TrelloRestoreModal
          task={trelloRestoreTask}
          onRestoreLocal={() => restoreCompletedTask(trelloRestoreTask, true)}
          onCancel={() => setTrelloRestoreTask(null)}
        />
      ) : null}

      {backupRestorePlan ? <BackupRestorePlanModal plan={backupRestorePlan} onClose={() => setBackupRestorePlan(null)} /> : null}
      {sortProposal ? (
        <SortProposalModal
          proposal={sortProposal}
          busy={busy}
          onApply={applySortProposal}
          onCancel={() => setSortProposal(null)}
        />
      ) : null}
      {detailSuggestions ? (
        <TaskSuggestionModal
          task={detailSuggestions.task}
          response={detailSuggestions.response}
          busy={busy}
          onApply={(fields) => applySelectedTaskSuggestions(detailSuggestions.task, detailSuggestions.response, fields)}
          onCancel={() => setDetailSuggestions(null)}
        />
      ) : null}
      {individualSuggestionProgress ? (
        <AISuggestionProgressModal
          state={individualSuggestionProgress}
          title="Sugerir detalles"
          description="Analizando la tarea para preparar sugerencias."
          onClose={() => setIndividualSuggestionProgress(null)}
          onRetry={
            individualSuggestionProgress.phase === "error"
              ? () => {
                  const task = tasks.find((item) => item.id === suggestingDetailsTaskId) ?? selectedTask;
                  if (task) requestTaskSuggestions(task);
                }
              : undefined
          }
        />
      ) : null}
      {bulkSuggestionsOpen ? (
        <BulkTaskSuggestionModal
          busy={busy}
          preferredTasks={tasks}
          searchQuery={query}
          onClose={() => setBulkSuggestionsOpen(false)}
          onManual={openManualDetailQueue}
          onShowList={showIncompleteTasks}
          onApplied={applyBulkSuggestionUpdates}
          onGenerated={(failed) =>
            showTransientFeedback(
              failed ? `Sugerencias generadas con ${failed} ${failed === 1 ? "error" : "errores"}` : "Sugerencias listas",
              failed ? "warning" : "success",
            )
          }
        />
      ) : null}
      {manualDetailQueue ? (
        <ManualDetailQueueModal
          initialDetails={manualDetailQueue.details}
          tasks={tasks}
          openAIAvailable={manualDetailQueue.openAIAvailable}
          onClose={() => setManualDetailQueue(null)}
          onTaskUpdated={handleManualDetailUpdate}
        />
      ) : null}
      {inboxTriageOpen ? (
        <InboxTriageModal
          tasks={inboxTasks}
          availableScopes={availableTaskScopes}
          configuredTrelloBoards={configuredTrelloBoards}
          linkingTrelloTaskId={trelloLinkingTaskId}
          busy={busy}
          onClose={() => setInboxTriageOpen(false)}
          onSaveTask={async (task, patch) => {
            const updated = await patchTask(task.id, patch);
            handleInboxTriageUpdate(updated, patch.scope && patch.scope !== "Inbox" ? "Inbox procesado" : "Tarea guardada");
            return updated;
          }}
          onSuggest={(task) => suggestTaskDetails(task.id)}
          onApplySuggestions={async (task, response, fields) => {
            const suggestions = Object.fromEntries(Object.entries(response.suggestions).map(([field, item]) => [field, item.value]));
            const result = await applyTaskSuggestions(task.id, suggestions, fields, false, response.source);
            handleInboxTriageUpdate(
              result.task,
              suggestionApplySummary(result.applied_fields.length, result.remaining_missing_fields.length),
            );
            return result.task;
          }}
          onComplete={handleInboxTriageComplete}
          onDiscard={handleInboxTriageDiscard}
          onCreateTrelloCard={linkTaskToTrello}
        />
      ) : null}
      {taskCreateOpen ? (
        <TaskCreateModal
          busy={busy}
          bulkText={bulkText}
          availableScopes={availableTaskScopes}
          trelloScopes={trelloScopeSet}
          onBulkTextChange={setBulkText}
          onCreateTask={createTaskFromModal}
          onBulkCreate={createBulkTasksFromText}
          onClose={() => setTaskCreateOpen(false)}
        />
      ) : null}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}

function ToastStack({ toasts, onDismiss }: { toasts: ToastMessage[]; onDismiss: (id: string) => void }) {
  return (
    <div className="toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onDismiss }: { toast: ToastMessage; onDismiss: (id: string) => void }) {
  useEffect(() => {
    if (toast.persistent || toast.kind === "loading") return undefined;
    const duration = toast.kind === "error" ? 8000 : toast.kind === "warning" ? 5600 : 3600;
    const timer = window.setTimeout(() => onDismiss(toast.id), duration);
    return () => window.clearTimeout(timer);
  }, [onDismiss, toast.id, toast.kind, toast.persistent]);

  return (
    <div className={`toast ${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
      {toast.kind === "loading" ? <span className="toast-spinner" aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}
      <span>{toast.message}</span>
      <button type="button" className="toast-close" onClick={() => onDismiss(toast.id)} aria-label="Cerrar notificación">
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

function TaskCreateModal({
  busy,
  bulkText,
  availableScopes,
  trelloScopes,
  onBulkTextChange,
  onCreateTask,
  onBulkCreate,
  onClose,
}: {
  busy: boolean;
  bulkText: string;
  availableScopes: string[];
  trelloScopes: Set<string>;
  onBulkTextChange: (value: string) => void;
  onCreateTask: (draft: TaskCreateDraft) => Promise<void>;
  onBulkCreate: (text: string) => Promise<void>;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"single" | "bulk">("single");
  const [draft, setDraft] = useState<TaskCreateDraft>(() => ({
    title: "",
    scope: "Inbox",
    priority_label: null,
    due_at: "",
    effort_bucket: null,
    context_bucket: null,
    estimated_minutes: "",
    create_in_trello: false,
  }));
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const scopeHasTrello = trelloScopes.has(draft.scope);
  const trelloEnabled = scopeHasTrello && draft.create_in_trello;

  function updateDraft(patch: Partial<TaskCreateDraft>) {
    setLocalError(null);
    setDraft((current) => {
      const next = { ...current, ...patch };
      if (patch.scope) {
        next.create_in_trello = trelloScopes.has(patch.scope);
      }
      return next;
    });
  }

  async function submitSingle(event: FormEvent) {
    event.preventDefault();
    if (!draft.title.trim()) return;
    try {
      await onCreateTask(draft);
      onClose();
    } catch (nextError) {
      setLocalError(readError(nextError));
    }
  }

  async function submitBulk(event: FormEvent) {
    event.preventDefault();
    if (!bulkText.trim()) return;
    try {
      await onBulkCreate(bulkText);
      onClose();
    } catch (nextError) {
      setLocalError(readError(nextError));
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="task-create-modal" role="dialog" aria-modal="true" aria-labelledby="task-create-title">
        <div className="sort-modal-header">
          <div>
            <span className="eyebrow">Crear</span>
            <h2 id="task-create-title">Nueva tarea</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Cerrar" disabled={busy}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <div className="task-create-tabs" role="tablist" aria-label="Modo de creación">
          <button type="button" className={tab === "single" ? "active" : ""} onClick={() => setTab("single")}>
            Una tarea
          </button>
          <button type="button" className={tab === "bulk" ? "active" : ""} onClick={() => setTab("bulk")}>
            Varias líneas
          </button>
        </div>

        {localError ? (
          <div className="notice" role="alert">
            {localError}
          </div>
        ) : null}

        {tab === "single" ? (
          <form className="task-create-form" onSubmit={submitSingle}>
            <label className="task-create-title-field">
              <span>Título</span>
              <input
                autoFocus
                value={draft.title}
                onChange={(event) => updateDraft({ title: event.target.value })}
                placeholder="Qué querés hacer..."
                disabled={busy}
              />
            </label>
            <div className="task-create-grid">
              <label>
                <span>Categoría</span>
                <select value={draft.scope} onChange={(event) => updateDraft({ scope: event.target.value })} disabled={busy}>
                  {availableScopes.map((scope) => (
                    <option key={scope} value={scope}>
                      {scope}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Prioridad</span>
                <select
                  value={draft.priority_label ?? ""}
                  onChange={(event) => updateDraft({ priority_label: (event.target.value || null) as Task["priority_label"] })}
                  disabled={busy}
                >
                  {priorities.map((priority) => (
                    <option key={priority.value} value={priority.value}>
                      {priority.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Deadline</span>
                <input
                  type="datetime-local"
                  value={draft.due_at}
                  onChange={(event) => updateDraft({ due_at: event.target.value })}
                  disabled={busy}
                />
              </label>
            </div>

            {scopeHasTrello ? (
              <label className="trello-create-toggle">
                <input
                  type="checkbox"
                  checked={draft.create_in_trello}
                  onChange={(event) => updateDraft({ create_in_trello: event.target.checked })}
                  disabled={busy}
                />
                <span>
                  <strong>Crear tarjeta en Trello</strong>
                  <small>Se usará el tablero {draft.scope} y su lista Pendiente configurada.</small>
                </span>
              </label>
            ) : null}

            <button type="button" className="secondary-action subtle" onClick={() => setAdvancedOpen((current) => !current)}>
              {advancedOpen ? "Ocultar detalles" : "Agregar detalles"}
            </button>

            {advancedOpen ? (
              <div className="task-create-grid">
                <label>
                  <span>Esfuerzo</span>
                  <select
                    value={draft.effort_bucket ?? ""}
                    onChange={(event) => updateDraft({ effort_bucket: (event.target.value || null) as Task["effort_bucket"] })}
                    disabled={busy || trelloEnabled}
                  >
                    <option value="">Sin estimar</option>
                    <option value="quick">Rápida</option>
                    <option value="medium">Media</option>
                    <option value="deep">Profunda</option>
                  </select>
                </label>
                <label>
                  <span>Contexto</span>
                  <select
                    value={draft.context_bucket ?? ""}
                    onChange={(event) => updateDraft({ context_bucket: (event.target.value || null) as Task["context_bucket"] })}
                    disabled={busy || trelloEnabled}
                  >
                    {contexts.map((context) => (
                      <option key={context.value} value={context.value}>
                        {context.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Minutos</span>
                  <input
                    type="number"
                    min="1"
                    value={draft.estimated_minutes}
                    onChange={(event) => updateDraft({ estimated_minutes: event.target.value })}
                    disabled={busy || trelloEnabled}
                  />
                </label>
              </div>
            ) : null}

            <div className="task-create-actions">
              <button type="submit" disabled={busy || !draft.title.trim()}>
                {trelloEnabled ? "Crear tarjeta en Trello" : "Crear tarea"}
              </button>
              <button type="button" className="secondary-action subtle" onClick={onClose} disabled={busy}>
                Cancelar
              </button>
            </div>
          </form>
        ) : (
          <form className="task-create-form" onSubmit={submitBulk}>
            <textarea
              value={bulkText}
              onChange={(event) => onBulkTextChange(event.target.value)}
              placeholder="Una tarea por línea..."
              rows={8}
              disabled={busy}
            />
            <div className="task-create-actions">
              <button type="submit" disabled={busy || !bulkText.trim()}>
                Importar líneas
              </button>
              <button type="button" className="secondary-action subtle" onClick={onClose} disabled={busy}>
                Cancelar
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function SettingsView({
  settings,
  schema,
  availableScopes,
  selectedWeekendScopes,
  discovery,
  boardDiscovery,
  trelloStatus,
  backupStatus,
  backupList,
  llmStatus,
  openAIModels,
  briefingStatus,
  systemStatus,
  systemStatusState,
  onRefreshSystemStatus,
  dirty,
  saveState,
  busy,
  onChange,
  onSave,
  onDiscard,
  onDiscoverTrello,
  onDiscoverTrelloBoards,
  onValidateTrello,
  onValidateLLM,
  onRefreshOpenAIModels,
  onSaveOpenAIKey,
  onUpdateOpenAI,
  onRevokeOpenAI,
  onCreateBackup,
  onValidateBackup,
  onOpenBackupRestorePlan,
  onBackToTodo,
}: {
  settings: Record<string, any> | null;
  schema: Record<string, any> | null;
  availableScopes: string[];
  selectedWeekendScopes: string[];
  discovery: TrelloDiscovery | null;
  boardDiscovery: TrelloAvailableBoard[];
  trelloStatus: TrelloStatus | null;
  backupStatus: BackupStatus | null;
  backupList: BackupMetadata[];
  llmStatus: LLMStatus | null;
  openAIModels: OpenAIModelCatalog | null;
  briefingStatus: BriefingStatus | null;
  systemStatus: SystemStatus | null;
  systemStatusState: Loadable<SystemStatus>;
  onRefreshSystemStatus: () => void;
  dirty: boolean;
  saveState: SettingsSaveState;
  busy: boolean;
  onChange: (path: string[], value: unknown) => void;
  onSave: () => void;
  onDiscard: () => void;
  onDiscoverTrello: () => void;
  onDiscoverTrelloBoards: () => void;
  onValidateTrello: () => void;
  onValidateLLM: () => void;
  onRefreshOpenAIModels: () => Promise<OpenAIModelCatalog>;
  onSaveOpenAIKey: (apiKey: string) => Promise<LLMStatus>;
  onUpdateOpenAI: (payload: { enabled?: boolean; model?: string }) => Promise<LLMStatus>;
  onRevokeOpenAI: () => Promise<LLMStatus>;
  onCreateBackup: () => void;
  onValidateBackup: (backupId: string) => void;
  onOpenBackupRestorePlan: (backupId: string) => void;
  onBackToTodo: () => void;
}) {
  const prioritySensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const [activeSettingsSection, setActiveSettingsSection] = useState(settingsSections[0].id);
  const [trelloBoardModalOpen, setTrelloBoardModalOpen] = useState(false);
  const [expandedTrelloBoard, setExpandedTrelloBoard] = useState<string | null>(null);
  const [trelloListsByBoardId, setTrelloListsByBoardId] = useState<Record<string, TrelloList[]>>({});
  const [autoConfirmAcknowledged, setAutoConfirmAcknowledged] = useState<Record<string, boolean>>({});
  const [telegramIntegration, setTelegramIntegration] = useState<TelegramIntegrationStatus | null>(null);
  const [telegramLinkCode, setTelegramLinkCode] = useState<TelegramLinkCode | null>(null);
  const [trelloIntegration, setTrelloIntegration] = useState<TrelloIntegrationStatus | null>(null);
  const [openAIApiKey, setOpenAIApiKey] = useState("");
  const [openAIModel, setOpenAIModel] = useState(llmStatus?.model ?? "");
  const [confirmOpenAIRevoke, setConfirmOpenAIRevoke] = useState(false);
  const [modelCatalogBusy, setModelCatalogBusy] = useState(false);
  const [installationReadiness, setInstallationReadiness] = useState<InstallationReadiness | null>(null);
  const [visibleBackupCount, setVisibleBackupCount] = useState(backupPageSize);
  const [trelloCredentialDraft, setTrelloCredentialDraft] = useState({ apiKey: "", token: "" });
  const [integrationBusy, setIntegrationBusy] = useState(false);
  const [integrationMessage, setIntegrationMessage] = useState<string | null>(null);
  const manualNavigationLockUntil = useRef(0);
  const settingsReady = Boolean(settings);
  const trelloBoardIds = useMemo(
    () =>
      Object.values(settings?.trello?.boards ?? {})
        .map((board: any) => String(board?.board_id ?? "").trim())
        .filter(Boolean)
        .join("|"),
    [settings?.trello?.boards],
  );

  useEffect(() => {
    if (!discovery) return;
    setTrelloListsByBoardId((current) => {
      const next = { ...current };
      for (const board of discovery.boards ?? []) {
        const boardId = settings?.trello?.boards?.[board.alias]?.board_id;
        if (boardId && Array.isArray(board.lists)) next[String(boardId)] = dedupeTrelloLists(board.lists);
      }
      return next;
    });
  }, [discovery, settings?.trello?.boards]);

  useEffect(() => {
    if (!settingsReady || !trelloBoardIds) return;
    for (const boardId of trelloBoardIds.split("|")) {
      if (trelloListsByBoardId[boardId] === undefined) {
        void loadTrelloListsForBoard(boardId);
      }
    }
  }, [settingsReady, trelloBoardIds, trelloListsByBoardId]);

  useEffect(() => {
    if (!settingsReady) return;
    void refreshIntegrationStatus();
  }, [settingsReady]);

  useEffect(() => {
    setOpenAIModel(llmStatus?.model ?? "");
  }, [llmStatus?.model]);

  useEffect(() => {
    if (!settingsReady) return undefined;
    const elements = settingsSections
      .map((section) => document.getElementById(section.id))
      .filter((element): element is HTMLElement => Boolean(element));
    if (!elements.length) return undefined;
    let frame = 0;

    function updateActiveSection() {
      frame = 0;
      if (Date.now() < manualNavigationLockUntil.current) return;
      const toolbarBottom = document.querySelector(".settings-toolbar")?.getBoundingClientRect().bottom ?? 0;
      const effectiveTop = Math.max(toolbarBottom + 16, 96);
      const viewportBottom = window.innerHeight;
      let activeId = elements[0].id;
      let closestDistance = Number.POSITIVE_INFINITY;

      for (const element of elements) {
        const rect = element.getBoundingClientRect();
        if (rect.bottom <= effectiveTop || rect.top >= viewportBottom) continue;
        const distance = Math.abs(rect.top - effectiveTop);
        if (rect.top <= effectiveTop && rect.bottom > effectiveTop) {
          activeId = element.id;
          break;
        }
        if (distance < closestDistance) {
          activeId = element.id;
          closestDistance = distance;
        }
      }

      setActiveSettingsSection((current) => (current === activeId ? current : activeId));
    }

    function scheduleUpdate() {
      if (frame) return;
      frame = window.requestAnimationFrame(updateActiveSection);
    }

    const observer = new IntersectionObserver(
      () => scheduleUpdate(),
      { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.1, 0.25, 0.5] },
    );
    elements.forEach((element) => observer.observe(element));
    updateActiveSection();
    window.addEventListener("scroll", scheduleUpdate, { passive: true });
    window.addEventListener("resize", scheduleUpdate);
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [settingsReady]);

  useEffect(() => {
    const aliases = Object.keys(settings?.trello?.boards ?? {});
    if (!aliases.length) {
      setExpandedTrelloBoard(null);
      return;
    }
    setExpandedTrelloBoard((current) => (current && aliases.includes(current) ? current : aliases[0]));
  }, [settings?.trello?.boards]);

  function handleSettingsNavigation(id: string) {
    manualNavigationLockUntil.current = Date.now() + 1400;
    setActiveSettingsSection(id);
  }

  if (!settings) {
    return (
      <TaskSection title="Configuración">
        <div className="empty">Cargando configuración…</div>
      </TaskSection>
    );
  }

  const weekdays = schema?.weekdays ?? ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
  const persistedWeekendScopes = settings.modes?.weekend?.active_scopes ?? selectedWeekendScopes;
  const availableScopeSet = new Set(availableScopes);
  const weekendScopeOptions = dedupeExactStrings([...availableScopes, ...persistedWeekendScopes]);
  const trelloStates = schema?.trello_states ?? ["pending", "in_progress", "review", "completed", "perpetual"];
  const trelloWorkflowRoles = schema?.trello_workflow_roles ?? ["pending", "in_progress", "review", "completed", "perpetual", "ignored", "custom_actionable", "custom_passive"];
  const priorityCriteria = settings.priority?.criteria ?? {};
  const orderedCriteria = orderPriorityCriteria(priorityCriteria);
  const selectedPriorityPreset = settings.priority?.preset ?? "balanced";
  const shownPriorityPreset = priorityPresetOrders[selectedPriorityPreset] ? selectedPriorityPreset : "custom";
  const priorityOrderIsCustom = isPriorityOrderCustom(selectedPriorityPreset, orderedCriteria.map(([key]) => key));
  const currentSettings = settings;
  const weekendPreview = buildWeekendMode(currentSettings);
  const advanced = normalizeAdvancedSettings(settings.advanced ?? {});
  const advancedEditable = advanced.editable;
  const advancedPrerequisites = advanced.prerequisites;
  const advancedInstance = advanced.instance;
  const trelloWriteEnabled = Boolean(advancedEditable.trello_write_enabled);
  const trelloAutoConfirmEnabled = Boolean(advancedEditable.trello_auto_confirm_writes);
  const trelloWriteReady = Boolean(advancedPrerequisites.trello_write_enabled);
  const trelloAutoConfirmReady = Boolean(advancedPrerequisites.trello_auto_confirm_writes);
  const saveStatusText = settingsSaveStatusText(dirty, saveState, busy);
  const showStickySaveBar = dirty || saveState.status === "error";
  const activeCriteriaCount = orderedCriteria.filter(([, item]) => Boolean(item.enabled)).length;
  const briefingTime = String(settings.briefing?.time ?? "");
  const briefingCutoff = String(settings.briefing?.late_cutoff ?? "");
  const briefingCutoffLooksInvalid = Boolean(briefingTime && briefingCutoff && briefingCutoff <= briefingTime);
  const systemStatusCopy = systemStatusCopyForState(systemStatusState);
  const systemStatusUpdatedLabel = systemStatusUpdatedLabelForState(systemStatusState);
  const systemStatusRefreshing = systemStatusState.state === "loading";
  const sortedBackups = [...backupList].sort((left, right) => right.created_at.localeCompare(left.created_at));
  const visibleBackups = sortedBackups.slice(0, visibleBackupCount);
  const llmProvider = "openai";

  function toggleArray(path: string[], value: string) {
    const current = getPath(currentSettings, path, []) as string[];
    onChange(path, current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  }

  function updateTrelloWorkflowState(boardAlias: string, index: number, patch: Record<string, unknown>) {
    const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
    const workflowStates = boardWorkflowStates(board, trelloStates);
    const nextStates = workflowStates.map((state, stateIndex) =>
      stateIndex === index
        ? {
            ...state,
            ...patch,
          }
        : state,
    );
    onChange(["trello", "boards", boardAlias, "workflow_states"], nextStates);
    onChange(["trello", "boards", boardAlias, "states"], legacyStatesFromWorkflow(nextStates));
  }

  function updateTrelloWorkflowList(boardAlias: string, index: number, listId: string) {
    const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
    const lists = listsForTrelloBoard(board, trelloListsByBoardId);
    const list = lists.find((item) => item.id === listId);
    updateTrelloWorkflowState(boardAlias, index, {
      list_id: listId || null,
      list_name: list?.name ?? null,
    });
  }

  function addTrelloWorkflowState(boardAlias: string) {
    const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
    const workflowStates = boardWorkflowStates(board, trelloStates);
    const nextIndex = workflowStates.length + 1;
    onChange(["trello", "boards", boardAlias, "workflow_states"], [
      ...workflowStates,
      {
        key: `custom_${nextIndex}`,
        label: "Nueva clasificación",
        role: "custom_actionable",
        enabled: true,
        required_for: [],
        list_id: null,
        list_name: null,
      },
    ]);
  }

  function removeTrelloWorkflowState(boardAlias: string, index: number) {
    const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
    const workflowStates = boardWorkflowStates(board, trelloStates);
    onChange(["trello", "boards", boardAlias, "workflow_states"], workflowStates.filter((_, stateIndex) => stateIndex !== index));
  }

  function addTrelloBoard(draft: { alias: string; name: string; boardId: string }) {
    onChange(["trello", "boards", draft.alias], createTrelloBoardSettings(draft.alias, draft.name, draft.boardId));
    void loadTrelloListsForBoard(draft.boardId);
    setTrelloBoardModalOpen(false);
  }

  function applyDiscoveredBoard(boardAlias: string, discoveredBoardId: string) {
    const discovered = boardDiscovery.find((board) => board.id === discoveredBoardId);
    if (!discovered) return;
    const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
    const previousBoardId = board.board_id;
    const workflowStates = boardWorkflowStates(board, trelloStates);
    const currentLists = listsForTrelloBoard(board, trelloListsByBoardId);
    const nextBoard: Record<string, any> = {
      ...board,
      alias: board.alias ?? boardAlias,
      board_id: discovered.id,
      name: discovered.name,
      workflow_states:
        previousBoardId && previousBoardId !== discovered.id
          ? remapWorkflowStatesForBoard(workflowStates, currentLists, trelloListsByBoardId[discovered.id] ?? [])
          : workflowStates,
    };
    nextBoard.states = legacyStatesFromWorkflow(nextBoard.workflow_states);
    onChange(["trello", "boards", boardAlias], nextBoard);
    void loadTrelloListsForBoard(discovered.id, workflowStates, boardAlias);
  }

  async function loadTrelloListsForBoard(boardId: string, workflowStates?: Array<Record<string, any>>, boardAlias?: string) {
    if (!boardId) return;
    try {
      const response = await fetchTrelloBoardLists(boardId);
      const lists = dedupeTrelloLists(response.lists);
      setTrelloListsByBoardId((current) => ({ ...current, [boardId]: lists }));
      if (workflowStates && boardAlias) {
        const board = currentSettings.trello?.boards?.[boardAlias] ?? {};
        const currentLists = listsForTrelloBoard(board, { ...trelloListsByBoardId, [boardId]: lists });
        const nextStates = remapWorkflowStatesForBoard(workflowStates, currentLists, lists);
        onChange(["trello", "boards", boardAlias, "workflow_states"], nextStates);
        onChange(["trello", "boards", boardAlias, "states"], legacyStatesFromWorkflow(nextStates));
      }
    } catch {
      setTrelloListsByBoardId((current) => ({ ...current, [boardId]: [] }));
    }
  }

  async function refreshIntegrationStatus() {
    try {
      const [telegramStatus, trelloIntegrationStatus, readiness] = await Promise.all([
        fetchTelegramIntegrationStatus().catch(() => null),
        fetchTrelloIntegrationStatus().catch(() => null),
        fetchInstallationReadiness().catch(() => null),
      ]);
      setTelegramIntegration(telegramStatus);
      setTrelloIntegration(trelloIntegrationStatus);
      setInstallationReadiness(readiness);
    } catch {
      setIntegrationMessage("No pude cargar el estado de integraciones.");
    }
  }

  async function handleCreateTelegramLinkCode() {
    setIntegrationBusy(true);
    setIntegrationMessage(null);
    try {
      const response = await createTelegramLinkCode();
      setTelegramLinkCode(response);
      setIntegrationMessage("Código generado. Usalo antes de que expire.");
      await refreshIntegrationStatus();
    } catch (error) {
      setIntegrationMessage(readError(error));
    } finally {
      setIntegrationBusy(false);
    }
  }

  async function handleUnlinkTelegram() {
    setIntegrationBusy(true);
    setIntegrationMessage(null);
    try {
      const response = await unlinkTelegramIntegration();
      setTelegramIntegration(response);
      setTelegramLinkCode(null);
      setIntegrationMessage("Telegram desvinculado para este usuario.");
    } catch (error) {
      setIntegrationMessage(readError(error));
    } finally {
      setIntegrationBusy(false);
    }
  }

  async function handleSaveTrelloCredentials() {
    setIntegrationBusy(true);
    setIntegrationMessage(null);
    try {
      const response = await saveTrelloManualCredentials({
        api_key: trelloCredentialDraft.apiKey,
        token: trelloCredentialDraft.token,
      });
      setTrelloIntegration(response);
      setTrelloCredentialDraft({ apiKey: "", token: "" });
      setIntegrationMessage("Credenciales Trello guardadas cifradas para este usuario.");
    } catch (error) {
      setIntegrationMessage(readError(error));
    } finally {
      setIntegrationBusy(false);
    }
  }

  async function handleRevokeTrelloCredentials() {
    setIntegrationBusy(true);
    setIntegrationMessage(null);
    try {
      const response = await revokeTrelloManualCredentials();
      setTrelloIntegration(response);
      setIntegrationMessage("Credenciales Trello revocadas para este usuario.");
    } catch (error) {
      setIntegrationMessage(readError(error));
    } finally {
      setIntegrationBusy(false);
    }
  }

  function applyPriorityPreset(preset: string) {
    if (preset === "custom") return;
    onChange(["priority"], {
      ...(currentSettings.priority ?? {}),
      preset,
      criteria: buildPriorityCriteria(priorityCriteria, priorityPresetOrders[preset] ?? Object.keys(priorityCriteria)),
    });
  }

  function markPriorityCustom(criteria: Record<string, any>) {
    onChange(["priority"], {
      ...(currentSettings.priority ?? {}),
      preset: "custom",
      criteria,
    });
  }

  function movePriorityCriterion(activeKey: string, overKey: string) {
    if (activeKey === overKey) return;
    const oldIndex = orderedCriteria.findIndex(([key]) => key === activeKey);
    const newIndex = orderedCriteria.findIndex(([key]) => key === overKey);
    if (oldIndex < 0 || newIndex < 0) return;
    const nextOrder = arrayMove(orderedCriteria.map(([key]) => key), oldIndex, newIndex);
    markPriorityCustom(buildPriorityCriteria(priorityCriteria, nextOrder));
  }

  function copySystemDiagnostic() {
    if (!systemStatus) return;
    const text = JSON.stringify(
      {
        stale: systemStatusState.state === "stale",
        stale_error: systemStatusState.state === "stale" ? systemStatusState.error : null,
        copied_at: new Date().toISOString(),
        diagnosis: systemStatus,
      },
      null,
      2,
    );
    void navigator.clipboard?.writeText(text);
  }

  async function exportSystemDiagnostic() {
    const diagnostics = await fetchSystemDiagnostics();
    const blob = new Blob([JSON.stringify(diagnostics, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `alphawave-diagnostics-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function togglePriorityCriterion(key: string, checked: boolean) {
    markPriorityCustom({
      ...priorityCriteria,
      [key]: {
        ...(priorityCriteria[key] ?? {}),
        enabled: checked,
      },
    });
  }

  function handleBoardAutoConfirmChange(boardAlias: string, checked: boolean) {
    if (checked && !autoConfirmAcknowledged[boardAlias]) return;
    onChange(["trello", "boards", boardAlias, "auto_confirm_writes"], checked);
  }

  return (
    <section className="settings-view">
      <div className="settings-toolbar">
        <div>
          <h2>Configuración</h2>
          <p>Ajustá comportamiento local, integraciones y automatismos.</p>
          <span className={saveState.status === "error" ? "settings-save-state error" : dirty ? "settings-save-state dirty" : "settings-save-state"}>
            {saveStatusText}
          </span>
        </div>
        <div className="settings-toolbar-actions">
          <button className="secondary-action subtle back-action" type="button" onClick={onBackToTodo}>
            <ArrowLeft size={16} aria-hidden="true" />
            Volver a TODO
          </button>
          <div className="settings-save-actions">
            <button className="secondary-action subtle" type="button" onClick={onDiscard} disabled={busy || !dirty}>
              Descartar
            </button>
            <button className="secondary-action" type="button" onClick={onSave} disabled={busy || !dirty}>
              {busy && dirty ? "Guardando…" : "Guardar cambios"}
            </button>
          </div>
        </div>
      </div>

      <div className="settings-content">
        <SettingsNav activeId={activeSettingsSection} onNavigate={handleSettingsNavigation} />
        <div className="settings-grid">
        <section id="settings-status" className="settings-card settings-card-wide system-status-card" data-settings-section data-testid="settings-section-status">
          <div className="settings-card-heading">
            <div>
              <SettingsCardTitle title="Estado del sistema" />
              <p className="settings-section-copy">{systemStatusCopy}</p>
            </div>
            <div className="settings-actions">
              <button type="button" className="secondary-action subtle" onClick={onRefreshSystemStatus} disabled={systemStatusRefreshing}>
                {systemStatusRefreshing ? "Actualizando…" : "Actualizar"}
              </button>
              <button type="button" className="secondary-action subtle" onClick={copySystemDiagnostic} disabled={!systemStatus} data-testid="system-status-copy">
                Copiar diagnóstico
              </button>
              <button type="button" className="secondary-action subtle" onClick={() => void exportSystemDiagnostic()} disabled={!systemStatus}>
                Exportar diagnóstico
              </button>
            </div>
          </div>
          <div className="system-status-meta">
            <span>{systemStatusUpdatedLabel}</span>
            <span data-testid="system-status-runtime">{systemStatusRuntimeLabel(systemStatus)}</span>
            {systemStatusState.state === "stale" ? <span className="status-pill warning">No se pudo actualizar recién</span> : null}
            {systemStatusState.state === "error" ? <span className="status-pill warning">No se pudo cargar</span> : null}
          </div>
          {systemStatus ? (
            <div className="system-observability-grid">
              {systemObservabilityCards(systemStatus, settings.advanced ?? {}, trelloStatus, briefingStatus, backupStatus).map((card) => (
              <article className="system-observability-card" key={card.title}>
                <div className="system-observability-card-header">
                  <strong>{card.title}</strong>
                  <span className={`status-pill ${statusToneClass(card.tone)}`}>{card.status}</span>
                </div>
                <p>{card.detail}</p>
                {card.meta.length ? (
                  <ul>
                    {card.meta.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </article>
              ))}
            </div>
          ) : systemStatusState.state === "error" ? (
            <div className="empty">
              <p>{systemStatusState.error}</p>
            </div>
          ) : (
            <div className="empty">Cargando diagnóstico…</div>
          )}
        </section>

        <section id="settings-general" className="settings-card" data-settings-section data-testid="settings-section-general">
          <SettingsCardTitle title="General" help="Preferencias locales de la UI y horarios base. Los workers pueden necesitar reinicio para tomar algunos cambios." />
          <p className="settings-section-copy">Preferencias locales que afectan cómo se muestran horarios y respuestas.</p>
          <label>
            <span>Zona horaria</span>
            <TimezoneCombobox
              name="general-timezone"
              value={settings.general?.timezone ?? ""}
              onChange={(value) => onChange(["general", "timezone"], value)}
            />
          </label>
          <p className="settings-inline-note">
            {timezoneDisplay(settings.general?.timezone)}. Puede requerir reiniciar el servicio para afectar todos los workers.
          </p>
          <div className="settings-readonly-grid">
            <ReadOnlySetting label="Idioma de respuestas" value="Español" />
            <ReadOnlySetting label="Formato horario" value="24 horas" />
            <ReadOnlySetting label="Tema" value="Oscuro" />
            <ReadOnlySetting label="Hora local actual" value={formatTimeForZone(settings.general?.timezone)} />
          </div>
        </section>

        <section id="settings-weekend" className="settings-card" data-settings-section data-testid="settings-section-weekend">
          <SettingsCardTitle title="Modo fin de semana" help="Reduce automatismos durante días no laborales. No cambia tareas ni escribe en Trello." />
          <p className="settings-section-copy">Reduce ruido visual y alertas automáticas durante días elegidos. Los comandos manuales de Telegram siguen funcionando.</p>
          <div className="settings-state-line">
            <span className={weekendPreview.active ? "status-pill active" : settings.modes?.weekend?.enabled ? "status-pill warning" : "status-pill inactive"}>
              {weekendPreview.active ? "Activo hoy" : settings.modes?.weekend?.enabled ? "Programado, no activo hoy" : "Desactivado"}
            </span>
            <span>{weekendPreview.enabled ? `Días: ${(settings.modes?.weekend?.active_days ?? []).map(weekdayLabel).join(", ") || "sin días"}` : "Alertas normales."}</span>
          </div>
          <WeekendModeToggleCard
            weekendMode={weekendPreview}
            enabled={Boolean(settings.modes?.weekend?.enabled)}
            onChange={(checked) => onChange(["modes", "weekend", "enabled"], checked)}
          />
          <div className="settings-subsection">
            <h4>Días activos</h4>
            <div className="settings-chip-grid">
              {weekdays.map((day: string) => (
                <label className="settings-chip" key={day}>
                  <input
                    type="checkbox"
                    checked={(settings.modes?.weekend?.active_days ?? []).includes(day)}
                    onChange={() => toggleArray(["modes", "weekend", "active_days"], day)}
                  />
                  {weekdayLabel(day)}
                </label>
              ))}
            </div>
          </div>
          <div className="settings-subsection">
            <SubsectionTitle title="Categorías que siguen activas" help="Sólo estas categorías aparecerán en alertas automáticas durante el fin de semana." />
            <p className="settings-section-copy">Sólo estas categorías aparecerán en alertas automáticas durante el fin de semana.</p>
            <div className="settings-chip-grid">
              {weekendScopeOptions.map((scope: string) => (
                <label className={availableScopeSet.has(scope) ? "settings-chip" : "settings-chip unavailable"} key={scope}>
                  <input
                    type="checkbox"
                    checked={persistedWeekendScopes.includes(scope)}
                    onChange={() => toggleArray(["modes", "weekend", "active_scopes"], scope)}
                  />
                  {scope}
                  {!availableScopeSet.has(scope) ? <small>No disponible</small> : null}
                </label>
              ))}
            </div>
          </div>
          <div className="settings-subsection">
            <h4>Alertas permitidas</h4>
            <CheckboxRow
              label="Briefing automático"
              checked={Boolean(settings.modes?.weekend?.notifications?.briefing_auto)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "briefing_auto"], checked)}
            />
            <CheckboxRow
              label="Briefing atrasado al iniciar"
              checked={Boolean(settings.modes?.weekend?.notifications?.briefing_late_startup)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "briefing_late_startup"], checked)}
            />
            <CheckboxRow
              label="Recordatorios explícitos"
              help="Recordatorios creados a mano en UI o Telegram, incluso si el modo silencioso está activo."
              checked={Boolean(settings.modes?.weekend?.notifications?.reminders_explicit)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "reminders_explicit"], checked)}
            />
            <CheckboxRow
              label="Agrupar recordatorios vencidos"
              checked={Boolean(settings.modes?.weekend?.notifications?.reminders_overdue)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "reminders_overdue"], checked)}
            />
            <CheckboxRow
              label="Tareas EN PROCESO estancadas"
              help="Avisos para tarjetas que llevan demasiado tiempo en EN PROCESO."
              checked={Boolean(settings.modes?.weekend?.notifications?.stale_in_progress)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "stale_in_progress"], checked)}
            />
            <CheckboxRow
              label="Check-ins de perpetuas"
              help="Check-ins de tareas recurrentes que no se completan de forma tradicional."
              checked={Boolean(settings.modes?.weekend?.notifications?.perpetual_checkins)}
              onChange={(checked) => onChange(["modes", "weekend", "notifications", "perpetual_checkins"], checked)}
            />
          </div>
        </section>

        <section id="settings-briefing" className="settings-card" data-settings-section data-testid="settings-section-briefing">
          <SettingsCardTitle title="Briefing" help="Resumen automático del día por Telegram." />
          <p className="settings-section-copy">Recibí un resumen automático del día por Telegram con límites claros por bloque.</p>
          <SubsectionTitle title="Horario" />
          <CheckboxRow label="Activado" checked={Boolean(settings.briefing?.enabled)} onChange={(checked) => onChange(["briefing", "enabled"], checked)} />
          <div className={briefingCutoffLooksInvalid ? "settings-inline has-warning" : "settings-inline"}>
            <TimeField
              name="briefing-time"
              label="Hora del briefing"
              value={settings.briefing?.time ?? ""}
              fallbackValue="13:00"
              onChange={(value) => onChange(["briefing", "time"], value)}
              data-testid="briefing-time-input"
            />
            <TimeField
              name="briefing-cutoff"
              label="No enviar después de esta hora"
              value={settings.briefing?.late_cutoff ?? ""}
              fallbackValue="19:00"
              onChange={(value) => onChange(["briefing", "late_cutoff"], value)}
              data-testid="briefing-cutoff-input"
            />
          </div>
          {briefingCutoffLooksInvalid ? <p className="settings-field-warning">La hora límite debería ser posterior a la hora del briefing.</p> : null}
          <label>
            <span>Zona horaria</span>
            <TimezoneCombobox
              name="briefing-timezone"
              value={settings.briefing?.timezone ?? ""}
              onChange={(value) => onChange(["briefing", "timezone"], value)}
            />
          </label>
          <SubsectionTitle title="Comportamiento" />
          <CheckboxRow label="Enviar al iniciar si quedó pendiente" checked={Boolean(settings.briefing?.send_on_startup)} onChange={(checked) => onChange(["briefing", "send_on_startup"], checked)} />
          <SubsectionTitle title="Contenido" />
          <div className="settings-chip-grid">
            {[
              ["include_inbox", "Inbox"],
              ["include_attention", "Requieren atención"],
              ["include_perpetuals", "Perpetuas"],
              ["include_stale_in_progress", "Estancadas"],
              ["include_confirmations", "Confirmaciones"],
            ].map(([key, label]) => (
              <label className="settings-chip" key={key}>
                <input
                  type="checkbox"
                  checked={Boolean(settings.briefing?.[key])}
                  onChange={(event) => onChange(["briefing", key], event.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
          <NumberGrid
            values={[
              ["max_deep_work", "Trabajo profundo", "tareas"],
              ["max_quick_tasks", "Tareas rápidas", "tareas"],
              ["max_reviews", "Revisiones", "tareas"],
              ["max_attention", "Requieren atención", "tareas"],
              ["max_inbox", "Inbox", "tareas"],
              ["max_perpetuals", "Perpetuas", "tareas"],
            ]}
            source={settings.briefing}
            onChange={(key, value) => onChange(["briefing", key], value)}
          />
        </section>

        <section id="settings-reminders" className="settings-card" data-settings-section data-testid="settings-section-reminders">
          <SettingsCardTitle title="Recordatorios" help="Horarios por defecto para recordatorios creados desde UI o Telegram." />
          <p className="settings-section-copy">Ajustá horarios y comportamiento de recordatorios explícitos.</p>
          <CheckboxRow label="Activados" checked={Boolean(settings.reminders?.enabled)} onChange={(checked) => onChange(["reminders", "enabled"], checked)} />
          <div className="settings-inline">
            <TimeField
              name="reminder-default-time"
              label="Hora por defecto"
              value={settings.reminders?.default_time ?? ""}
              fallbackValue="09:00"
              onChange={(value) => onChange(["reminders", "default_time"], value)}
            />
            <TimeField
              name="reminder-snooze-time"
              label="Posponer hasta"
              value={settings.reminders?.snooze_default_time ?? ""}
              fallbackValue="09:00"
              onChange={(value) => onChange(["reminders", "snooze_default_time"], value)}
            />
          </div>
          <p className="settings-inline-note">La hora por defecto se usa cuando decís “mañana” sin especificar hora. “Posponer hasta” se usa al mover un recordatorio a otro día.</p>
          <NumberGrid
            values={[
              ["later_delay_hours", "“Más tarde” significa", "horas", "Cantidad de horas que se agregan cuando pedís más tarde."],
              ["group_overdue_threshold", "Agrupar vencidos desde", "recordatorios", "A partir de este número, los vencidos se agrupan para bajar ruido."],
            ]}
            source={settings.reminders}
            onChange={(key, value) => onChange(["reminders", key], value)}
          />
          <CheckboxRow
            label="Enviar recordatorios explícitos en modos silenciosos"
            help="Permite enviar recordatorios creados por vos aunque haya un modo quiet activo."
            checked={Boolean(settings.reminders?.send_explicit_in_quiet_modes)}
            onChange={(checked) => onChange(["reminders", "send_explicit_in_quiet_modes"], checked)}
          />
        </section>

        <section id="settings-trello" className="settings-card settings-card-wide" data-settings-section data-testid="settings-section-trello">
          <div className="settings-card-heading">
            <div className="trello-settings-intro">
              <SettingsCardTitle title="Trello" help="Mapea estados internos a listas reales de Trello. Guardar list_id evita romper escrituras si cambia el nombre visible de una lista." />
              <p>Configurá qué boards se sincronizan y cómo se mapean sus listas.</p>
            </div>
            <div className="settings-actions">
              <button type="button" onClick={onDiscoverTrelloBoards} disabled={busy}>
                Descubrir boards
              </button>
              <button type="button" onClick={() => setTrelloBoardModalOpen(true)} disabled={busy}>
                Agregar board
              </button>
              <button type="button" onClick={onValidateTrello} disabled={busy}>
                Validar todos
              </button>
              <button type="button" className="secondary-action subtle" onClick={onDiscoverTrello} disabled={busy}>
                Descubrir listas
              </button>
            </div>
          </div>
          <div className="settings-status-note">
            <strong>Trello sync:</strong> {trelloStatusLabel(trelloStatus)}
          </div>
          <div className="trello-board-list" aria-label="Boards conectados">
            {Object.keys(settings.trello?.boards ?? {}).length === 0 ? (
              <div className="empty">No hay boards conectados. Usá Descubrir boards o Agregar board para configurar el primero.</div>
            ) : null}
            {Object.entries(settings.trello?.boards ?? {}).map(([alias, board]: [string, any]) => {
              const workflowStates = boardWorkflowStates(board, trelloStates);
              const lists = listsForTrelloBoard(board, trelloListsByBoardId);
              const boardStatus = trelloBoardMappingStatus(board, trelloStates);
              const duplicateBoardAlias = duplicateBoardOwner(settings.trello?.boards ?? {}, alias, board.board_id);
              const enabledStates = workflowStates.filter((state) => state.enabled !== false);
              const disabledStates = workflowStates.filter((state) => state.enabled === false);
              const createCardReady = enabledStates.some((state) => state.required_for?.includes("create_card") && workflowStateHasListId(state));
              const completeReady = enabledStates.some((state) => state.required_for?.includes("complete") && workflowStateHasListId(state));
              const isExpanded = expandedTrelloBoard === alias;
              const displayName = board.name || alias;
              const boardOptions = trelloBoardSelectOptions(board, boardDiscovery);
              const autoConfirmAcked = Boolean(autoConfirmAcknowledged[alias] || board.auto_confirm_writes);
              return (
                <article className={isExpanded ? "trello-board-card expanded" : "trello-board-card"} key={alias} data-testid="trello-board-card">
                  <div className="trello-board-summary">
                    <button
                      type="button"
                      className="trello-board-summary-button"
                      onClick={() => setExpandedTrelloBoard(isExpanded ? null : alias)}
                      aria-expanded={isExpanded}
                      aria-controls={`trello-board-${alias}`}
                    >
                      <span className="trello-board-caret" aria-hidden="true">
                        {isExpanded ? "▾" : "▸"}
                      </span>
                      <span className="trello-board-summary-main">
                        <strong>{alias} · {displayName}</strong>
                        <span>
                          {enabledStates.length} clasificaciones activas · Crear cards {createCardReady ? "OK" : "sin mapear"} · Completar {completeReady ? "OK" : "sin mapear"}
                          {disabledStates.length ? ` · ${disabledStates.map((state) => state.label).join(", ")} desactivado` : ""}
                        </span>
                      </span>
                    </button>
                    <span className={boardStatus.ok ? "status-pill active trello-board-status-pill" : "status-pill inactive trello-board-status-pill"}>{boardStatus.label}</span>
                    <div className="trello-board-summary-actions">
                      <button type="button" className="secondary-action subtle" onClick={() => setExpandedTrelloBoard(isExpanded ? null : alias)}>
                        {isExpanded ? "Cerrar" : "Editar"}
                      </button>
                      <button type="button" className="secondary-action subtle" onClick={onValidateTrello} disabled={busy}>
                        Validar mapeos
                      </button>
                    </div>
                  </div>
                  {isExpanded ? (
                    <div className="trello-board-details" id={`trello-board-${alias}`}>
                      <div className="trello-board-panel">
                        <h4>General</h4>
                        <div className="trello-board-general-grid">
                          <label>
                            <span>Alias</span>
                            <input
                              value={board.alias ?? alias}
                              readOnly
                              disabled
                              title="El alias identifica el scope interno. Para cambiarlo, desactivá este board y agregá uno nuevo."
                            />
                          </label>
                          <label>
                            <span>Nombre visible</span>
                            <input
                              value={board.name ?? ""}
                              onChange={(event) => onChange(["trello", "boards", alias, "name"], event.target.value)}
                              disabled={busy}
                            />
                          </label>
                          <label>
                            <span>Board Trello</span>
                            <select
                              value={board.board_id ?? ""}
                              onChange={(event) => applyDiscoveredBoard(alias, event.target.value)}
                              disabled={busy || (!boardDiscovery.length && !board.board_id)}
                            >
                              <option value="">{boardDiscovery.length ? "Seleccionar board…" : "Usá Descubrir boards para elegir"}</option>
                              {boardOptions.map((discovered) => (
                                <option key={discovered.id} value={discovered.id}>
                                  {discovered.name}
                                </option>
                              ))}
                            </select>
                          </label>
                          <CheckboxRow
                            label="Activo"
                            help="Desactivar deja de sincronizar y escribir en este board. No borra tarjetas de Trello ni tareas locales."
                            checked={board.enabled !== false}
                            onChange={(checked) => onChange(["trello", "boards", alias, "enabled"], checked)}
                          />
                        </div>
                        {boardStatus.detail ? <p className="trello-board-detail">{boardStatus.detail}</p> : null}
                        <div className="settings-warning-box">
                          <div>
                            <strong>Auto-confirmar acciones de este board</strong>
                            <p>Ejecuta acciones de este board sin confirmación pendiente. Usalo sólo si confiás en los mappings.</p>
                          </div>
                          <div className="settings-warning-actions">
                            <CheckboxRow
                              label="Entiendo el riesgo"
                              checked={autoConfirmAcked}
                              disabled={Boolean(board.auto_confirm_writes)}
                              onChange={(checked) => setAutoConfirmAcknowledged((current) => ({ ...current, [alias]: checked }))}
                            />
                            <CheckboxRow
                              label="Auto-confirmar acciones"
                              checked={Boolean(board.auto_confirm_writes)}
                              disabled={!autoConfirmAcked && !board.auto_confirm_writes}
                              onChange={(checked) => handleBoardAutoConfirmChange(alias, checked)}
                            />
                          </div>
                        </div>
                        <details className={duplicateBoardAlias ? "trello-technical-details warning" : "trello-technical-details"}>
                          <summary>Detalles técnicos</summary>
                          <div className="trello-board-id-note">
                            <strong>Board ID</strong>
                            <code>{board.board_id || "Se completa automáticamente al elegir un board de Trello."}</code>
                            <small>
                              {duplicateBoardAlias
                                ? `Este board ya está configurado como ${duplicateBoardAlias}.`
                                : "Usá el campo manual sólo si Trello discovery no encuentra el board."}
                            </small>
                            <input
                              value={board.board_id ?? ""}
                              onChange={(event) => onChange(["trello", "boards", alias, "board_id"], event.target.value)}
                              placeholder="id largo o shortLink"
                              disabled={busy}
                            />
                          </div>
                        </details>
                      </div>
                      <div className="trello-board-panel">
                        <div className="trello-workflow-header">
                          <div>
                            <h4>Clasificaciones</h4>
                            <p>Ordená qué listas representan trabajo accionable, revisión, completado o estados pasivos.</p>
                          </div>
                          <button type="button" className="secondary-action subtle" onClick={() => addTrelloWorkflowState(alias)} disabled={busy}>
                            Agregar clasificación
                          </button>
                        </div>
                        <div className="trello-workflow-cards">
                          {workflowStates.map((workflowState: any, index: number) => {
                            const removable = !["pending", "in_progress", "review", "completed", "perpetual"].includes(workflowState.role);
                            const isDisabled = workflowState.enabled === false;
                            const hasMapping = workflowStateHasListId(workflowState);
                            return (
                              <div className={isDisabled ? "trello-workflow-card disabled" : "trello-workflow-card"} key={workflowState.key ?? `${workflowState.role}-${index}`}>
                                <div className="trello-workflow-main">
                                  <label className="settings-check trello-workflow-toggle">
                                    <input
                                      type="checkbox"
                                      checked={!isDisabled}
                                      onChange={(event) => updateTrelloWorkflowState(alias, index, { enabled: event.target.checked })}
                                      disabled={busy}
                                    />
                                    <span>{isDisabled ? "Desactivada" : "Activa"}</span>
                                  </label>
                                  <label>
                                    <span>Nombre visible</span>
                                    <input
                                      value={workflowState.label ?? trelloStateLabel(workflowState.role)}
                                      onChange={(event) => updateTrelloWorkflowState(alias, index, { label: event.target.value })}
                                      disabled={busy}
                                    />
                                  </label>
                                  <label>
                                    <span>Uso en AlphaWave TaskD</span>
                                    <select
                                      value={workflowState.role ?? "custom_actionable"}
                                      onChange={(event) => updateTrelloWorkflowState(alias, index, { role: event.target.value, key: workflowState.key || event.target.value })}
                                      disabled={busy}
                                    >
                                      {trelloWorkflowRoles.map((role: string) => (
                                        <option key={role} value={role}>
                                          {workflowRoleLabel(role)}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    <span>Lista Trello</span>
                                    <select
                                      value={workflowState.list_id ?? ""}
                                      onChange={(event) => updateTrelloWorkflowList(alias, index, event.target.value)}
                                      disabled={busy || isDisabled}
                                    >
                                      <option value="">Sin mapear</option>
                                      {lists.map((list) => (
                                        <option key={`${alias}-${workflowState.key}-${list.id}`} value={list.id}>
                                          {trelloListOptionLabel(list, lists)}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                </div>
                                <p className={isDisabled || hasMapping ? "trello-workflow-note" : "trello-workflow-note warning"}>
                                  {isDisabled
                                    ? "Desactivado para este board."
                                    : hasMapping
                                      ? `Mapeado como ${workflowRoleLabel(workflowState.role)}.`
                                      : `Sin mapear. Elegí una lista para activar "${workflowState.label ?? trelloStateLabel(workflowState.role)}".`}
                                </p>
                                {removable ? (
                                  <button type="button" className="secondary-action subtle" onClick={() => removeTrelloWorkflowState(alias, index)} disabled={busy}>
                                    Eliminar clasificación
                                  </button>
                                ) : null}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
          {trelloBoardModalOpen ? (
            <TrelloBoardModal
              existingAliases={Object.keys(settings.trello?.boards ?? {})}
              existingBoardIds={Object.values(settings.trello?.boards ?? {}).map((board: any) => String(board?.board_id ?? "")).filter(Boolean)}
              discoveredBoards={boardDiscovery}
              onDiscoverBoards={onDiscoverTrelloBoards}
              onAdd={addTrelloBoard}
              onClose={() => setTrelloBoardModalOpen(false)}
            />
          ) : null}
        </section>

        <section id="settings-priority" className="settings-card settings-card-wide" data-settings-section data-testid="settings-section-priority">
          <SettingsCardTitle title="Prioridad" help="El orden define qué señales pesan más para Hoy, Ahora y Priorizar. Las primeras pesan más." />
          <p className="settings-section-copy">La priorización ordena tareas según señales como fecha, impacto, bloqueo y esfuerzo. Los criterios de arriba pesan más cuando usás Priorizar.</p>
          <div className="priority-preset-row">
            <label>
              <span>Preset</span>
              <select name="priority-preset" value={shownPriorityPreset} onChange={(event) => applyPriorityPreset(event.target.value)}>
                {shownPriorityPreset === "custom" ? <option value="custom">Personalizado</option> : null}
                {(schema?.priority_presets ?? ["balanced", "deadlines_first", "quick_wins", "deep_work"]).map((preset: string) => (
                  <option key={preset} value={preset}>
                    {presetLabel(preset)}
                  </option>
                ))}
              </select>
            </label>
            <button className="secondary-action subtle" type="button" onClick={() => applyPriorityPreset("balanced")} disabled={busy}>
              Restaurar Balanceado
            </button>
          </div>
          <div className="priority-custom-state">
            {shownPriorityPreset === "custom" || priorityOrderIsCustom ? (
              <>
                <span>Preset: Personalizado</span>
                <small>Basado en: {presetLabel(selectedPriorityPreset === "custom" ? "balanced" : selectedPriorityPreset)}</small>
              </>
            ) : (
              <span>Preset: {presetLabel(shownPriorityPreset)}</span>
            )}
            <small>{activeCriteriaCount} de {orderedCriteria.length} criterios activos.</small>
          </div>
          <div className="priority-order-labels vertical" aria-hidden="true">
            <span>Más importante</span>
            <span>Menos importante</span>
          </div>
          <DndContext
            sensors={prioritySensors}
            collisionDetection={closestCenter}
            onDragEnd={(event) => {
              if (event.over) movePriorityCriterion(String(event.active.id), String(event.over.id));
            }}
          >
            <SortableContext items={orderedCriteria.map(([key]) => key)} strategy={verticalListSortingStrategy}>
              <div className="priority-criteria-list">
                {orderedCriteria.map(([key, item], index) => (
                  <PriorityCriterionRow
                    key={key}
                    id={key}
                    rank={index + 1}
                    label={item.label ?? key}
                    description={priorityCriterionDescriptions[key] ?? "Señal usada para calcular prioridad local."}
                    enabled={Boolean(item.enabled)}
                    onEnabledChange={(checked) => togglePriorityCriterion(key, checked)}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </section>

        <section id="settings-backups" className="settings-card" data-settings-section data-testid="settings-section-backups">
          <SettingsCardTitle title="Backups" help="Copias locales de la base SQLite." />
          <p className="settings-section-copy">Los backups automáticos protegen la base local. Crear backup ahora sigue siendo manual aunque los automáticos estén desactivados.</p>
          <div className="backup-summary-grid">
            <ReadOnlySetting label="Automáticos" value={backupStatus?.automatic_enabled === false ? "Desactivados" : "Activos"} />
            <ReadOnlySetting label="Último backup" value={backupStatusLabel(backupStatus)} />
            <ReadOnlySetting label="Backups" value={`${backupStatus?.count ?? backupList.length} · ${formatBytes(backupStatus?.total_size_bytes ?? totalBackupSize(backupList))}`} />
            <ReadOnlySetting label="Retención" value={`${backupStatus?.retention_days ?? settings.backups?.retention_days ?? 0} días`} />
          </div>
          <CheckboxRow label="Backups automáticos activados" checked={Boolean(settings.backups?.enabled)} onChange={(checked) => onChange(["backups", "enabled"], checked)} />
          <label>
            <span>Retención</span>
            <div className="settings-number-field">
              <input name="backup-retention-days" type="number" value={settings.backups?.retention_days ?? 0} onChange={(event) => onChange(["backups", "retention_days"], Number(event.target.value))} />
              <span>días</span>
            </div>
          </label>
          <small>Se conservan durante {settings.backups?.retention_days ?? 0} días.</small>
          <button className="secondary-action" type="button" onClick={onCreateBackup} disabled={busy}>
            {busy ? "Creando backup…" : "Crear backup ahora"}
          </button>
          <div className="backup-list" aria-label="Backups disponibles">
            <div className="backup-list-header">
              <strong>Backups disponibles</strong>
              <span>{backupList.length ? `Mostrando ${visibleBackups.length} de ${backupList.length}` : "Sin backups"}</span>
            </div>
            {backupList.length ? (
              visibleBackups.map((backup) => (
                <article className="backup-row" key={backup.id} data-testid="backup-row">
                  <div>
                    <strong>{formatDateTime(backup.created_at)}</strong>
                    <span>{backupSourceLabel(backup.source)} · {formatBytes(backup.size_bytes)} · {backup.path_redacted}</span>
                  </div>
                  <span className={backup.valid ? "status-pill active" : backup.validation.sqlite_integrity === "failed" ? "status-pill warning" : "status-pill inactive"}>
                    {backup.valid ? "Válido" : backup.validation.sqlite_integrity === "failed" ? "Inválido" : "Sin validar"}
                  </span>
                  <div className="backup-row-actions">
                    <button type="button" className="secondary-action subtle" onClick={() => onValidateBackup(backup.id)} disabled={busy}>
                      Validar
                    </button>
                    <button type="button" className="secondary-action subtle" onClick={() => onOpenBackupRestorePlan(backup.id)} disabled={busy}>
                      Ver plan
                    </button>
                  </div>
                </article>
              ))
            ) : (
              <div className="empty">Todavía no hay backups registrados.</div>
            )}
            {backupList.length > backupPageSize ? (
              <div className="backup-list-pagination">
                {visibleBackupCount < backupList.length ? (
                  <button className="secondary-action subtle" type="button" onClick={() => setVisibleBackupCount((count) => count + backupPageSize)}>
                    Mostrar más
                  </button>
                ) : null}
                {visibleBackupCount > backupPageSize ? (
                  <button className="secondary-action subtle" type="button" onClick={() => setVisibleBackupCount(backupPageSize)}>
                    Mostrar menos
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
          <details className="settings-technical-details">
            <summary>Detalles técnicos</summary>
            <small>Ubicación local: <code>{backupStatus?.backup_dir ?? "data/backups"}</code></small>
            <small>Los backups manuales se conservan; la retención automática limpia backups automáticos antiguos y nunca borra el más reciente.</small>
          </details>
        </section>

        <section id="settings-advanced" className="settings-card" data-settings-section data-testid="settings-section-advanced">
          <SettingsCardTitle title="Avanzado" />
          <p className="settings-section-copy">Los tokens y claves siguen viviendo en <code>.env</code> por seguridad.</p>

          <div className="settings-subsection">
            <SubsectionTitle title="Integraciones de usuario" help="Vinculaciones y credenciales privadas para este usuario. Nunca se muestran tokens completos." />
            {installationReadiness ? (
              <div className="installation-readiness" data-testid="installation-readiness">
                <ReadinessGroup title="Instalación y runtime" items={installationReadiness.instance} />
                <ReadinessGroup title="Este usuario" items={installationReadiness.user} />
              </div>
            ) : null}
            <div className="settings-warning-box compact">
              <strong>Secretos cifrados</strong>
              <p>Las credenciales manuales requieren <code>ALPHAWAVE_SECRET_ENCRYPTION_KEY</code>. El fallback legacy de instancia sigue disponible sólo para el owner local.</p>
            </div>
            <div className="settings-readonly-grid compact">
              <ReadOnlySetting label="Telegram" value={integrationStatusLabel(telegramIntegration?.status)} />
              <ReadOnlySetting label="Chat" value={telegramIntegration?.chat_redacted || "No vinculado"} />
              <ReadOnlySetting label="Trello" value={trelloIntegration?.configured ? "Configurado" : integrationStatusLabel(trelloIntegration?.status)} />
              <ReadOnlySetting label="Credenciales" value={credentialsSourceLabel(trelloIntegration?.credentials_source)} />
            </div>
            {integrationMessage ? <p className="settings-inline-note">{integrationMessage}</p> : null}
            <div className="settings-split-grid">
              <div className="settings-status-note">
                <div className="settings-card-heading compact">
                  <div>
                    <strong>Telegram</strong>
                    <p>Generá un código y envialo al bot con <code>/link CODIGO</code>.</p>
                  </div>
                  <button type="button" className="secondary-action subtle" onClick={handleCreateTelegramLinkCode} disabled={integrationBusy}>
                    Generar código
                  </button>
                </div>
                {telegramLinkCode ? (
                  <div className="settings-code-callout">
                    <strong>{telegramLinkCode.code}</strong>
                    <span>{telegramLinkCode.instructions}</span>
                    <small>Expira {formatDateTime(telegramLinkCode.expires_at)}. Se muestra una sola vez.</small>
                  </div>
                ) : null}
                <button type="button" className="secondary-action subtle" onClick={handleUnlinkTelegram} disabled={integrationBusy || !telegramIntegration?.linked}>
                  Desvincular Telegram
                </button>
              </div>
              <div className="settings-status-note">
                <div className="settings-card-heading compact">
                  <div>
                    <strong>Credenciales Trello</strong>
                    <p>Onboarding manual para beta privada. No ejecuta writes ni OAuth.</p>
                  </div>
                  <button type="button" className="secondary-action subtle" onClick={handleRevokeTrelloCredentials} disabled={integrationBusy || trelloIntegration?.credentials_source !== "user_encrypted"}>
                    Revocar
                  </button>
                </div>
                <label>
                  <span>API key</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={trelloCredentialDraft.apiKey}
                    onChange={(event) => setTrelloCredentialDraft((current) => ({ ...current, apiKey: event.target.value }))}
                    disabled={integrationBusy || trelloIntegration?.secret_store_available === false}
                    placeholder={secretHintPlaceholder(trelloIntegration?.api_key_hint, "No guardada")}
                  />
                </label>
                <label>
                  <span>Token</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={trelloCredentialDraft.token}
                    onChange={(event) => setTrelloCredentialDraft((current) => ({ ...current, token: event.target.value }))}
                    disabled={integrationBusy || trelloIntegration?.secret_store_available === false}
                    placeholder={secretHintPlaceholder(trelloIntegration?.token_hint, "No guardado")}
                  />
                </label>
                {trelloIntegration?.secret_store_available === false ? (
                  <small>Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY para guardar credenciales por usuario.</small>
                ) : null}
                <button
                  type="button"
                  className="secondary-action"
                  onClick={handleSaveTrelloCredentials}
                  disabled={integrationBusy || !trelloCredentialDraft.apiKey.trim() || !trelloCredentialDraft.token.trim() || trelloIntegration?.secret_store_available === false}
                >
                  Guardar credenciales cifradas
                </button>
              </div>
            </div>
          </div>

          <div className="settings-subsection">
            <SubsectionTitle title="Estado técnico" help="Resumen efectivo de integraciones, workers y toggles operativos." />
            <div className="settings-status-list">
              {advancedStatusRows(advanced, trelloStatus, briefingStatus, backupStatus).map((row) => (
                <div className="settings-status-row" key={row.label}>
                  <span>{row.label}</span>
                  <strong className={`status-pill ${statusToneClass(row.tone)}`}>{row.value}</strong>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-subsection">
            <SubsectionTitle title="Controles avanzados" help="Estos cambios modifican preferencias internas guardadas en SQLite, no archivos de configuración." />
            <p className="settings-section-copy">Estos cambios modifican preferencias internas, no archivos de configuración.</p>
            <div className="settings-warning-box compact">
              <strong>Integraciones con efectos externos</strong>
              <p>Activar escritura o auto-confirmación puede ejecutar acciones en Trello. Revisá mapeos y credenciales antes de guardar.</p>
            </div>
            <AdvancedToggleRow
              title="Escritura en Trello"
              description="Las acciones en Trello siempre requieren confirmación."
              checked={trelloWriteEnabled}
              disabled={!trelloWriteReady && !trelloWriteEnabled}
              disabledReason="Configurá credenciales y boards Trello en .env para habilitar writes."
              onChange={(checked) => onChange(["advanced", "editable", "trello_write_enabled"], checked)}
            />
            <AdvancedToggleRow
              title="Auto-confirmar acciones Trello"
              description="Ejecuta acciones Trello directamente sin crear confirmaciones pendientes. Úsalo sólo si confiás en tus comandos."
              checked={trelloAutoConfirmEnabled}
              disabled={!trelloAutoConfirmReady && !trelloAutoConfirmEnabled}
              disabledReason="Primero activá Escritura en Trello."
              warning="Las acciones en Trello seguirán registrándose, pero no pedirán confirmación previa."
              onChange={(checked) => onChange(["advanced", "editable", "trello_auto_confirm_writes"], checked)}
            />
            <div className="openai-settings-card" data-testid="openai-settings-card">
              <div className="settings-card-heading">
                <div>
                  <strong>OpenAI</strong>
                  <p>{llmStatus?.reason ?? "Consultando estado…"}</p>
                </div>
                <strong className={`status-pill ${statusToneClass(statusTone(llmStatus?.status ?? "disabled"))}`}>
                  {openAIStatusLabel(llmStatus?.status)}
                </strong>
              </div>

              <p className="settings-privacy-note">
                Los datos seleccionados de la tarea se envían a OpenAI para generar sugerencias. La API key se cifra
                en el servidor y nunca vuelve a mostrarse.
              </p>

              <div className="openai-settings-grid">
                <label>
                  API key
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={openAIApiKey}
                    onChange={(event) => setOpenAIApiKey(event.target.value)}
                    placeholder="Ingresá una nueva API key"
                    disabled={busy || !llmStatus?.secret_store_available}
                    data-testid="openai-api-key-input"
                  />
                  <small>
                    {llmStatus?.api_key_configured
                      ? `Clave guardada: ${llmStatus.api_key_hint ?? "configurada"}`
                      : "No hay una API key guardada."}
                  </small>
                </label>
                <label>
                  Modelo
                  <OpenAIModelCombobox
                    value={openAIModel}
                    options={openAIModels?.models ?? []}
                    recommendedModel={openAIModels?.recommended_model}
                    onChange={setOpenAIModel}
                    disabled={busy || modelCatalogBusy || openAIModels?.status !== "ready"}
                  />
                  <small>
                    {!openAIModels
                      ? "Cargando modelos disponibles..."
                      : openAIModels.models.find((option) => option.id === openAIModel)?.compatibility === "unavailable"
                        ? "El modelo actual ya no está disponible. Elegí otro y validalo."
                      : openAIModels?.stale
                      ? "Mostrando el último catálogo disponible; no se pudo actualizar."
                      : openAIModels?.status === "requires_api_key"
                        ? "Guardá una API key para consultar los modelos de tu cuenta."
                        : `${openAIModels.cached ? "Catálogo en caché" : "Catálogo actualizado"}${
                          openAIModels.fetched_at ? ` · ${relativeTimeLabel(openAIModels.fetched_at)}` : ""
                        }. Política AlphaWave ${openAIModels.policy_version}.`}
                  </small>
                </label>
              </div>

              <div className="openai-settings-actions">
                <button
                  type="button"
                  className="secondary-action"
                  disabled={busy || !openAIApiKey.trim() || !llmStatus?.secret_store_available}
                  onClick={() => {
                    void onSaveOpenAIKey(openAIApiKey)
                      .then(() => setOpenAIApiKey(""))
                      .catch(() => undefined);
                  }}
                  data-testid="openai-save-key"
                >
                  Guardar API key cifrada
                </button>
                <button
                  type="button"
                  className="secondary-action subtle"
                  disabled={busy || modelCatalogBusy || !llmStatus?.api_key_configured}
                  onClick={() => {
                    setModelCatalogBusy(true);
                    void onRefreshOpenAIModels()
                      .catch(() => undefined)
                      .finally(() => setModelCatalogBusy(false));
                  }}
                  data-testid="openai-refresh-models"
                >
                  <RefreshCw size={15} className={modelCatalogBusy ? "spin" : ""} aria-hidden="true" />
                  {modelCatalogBusy ? "Actualizando…" : "Actualizar modelos"}
                </button>
                <button
                  type="button"
                  className="secondary-action subtle"
                  disabled={
                    busy
                    || !openAIModel.trim()
                    || openAIModel === llmStatus?.model
                    || openAIModels?.models.find((option) => option.id === openAIModel)?.compatibility === "unavailable"
                  }
                  onClick={() => void onUpdateOpenAI({ model: openAIModel.trim() }).catch(() => undefined)}
                  data-testid="openai-save-model"
                >
                  Guardar modelo
                </button>
                <button
                  type="button"
                  className="secondary-action subtle"
                  onClick={onValidateLLM}
                  disabled={busy || !llmStatus?.api_key_configured}
                  data-testid="openai-validate"
                >
                  Validar modelo
                </button>
              </div>

              <AdvancedToggleRow
                title="Usar OpenAI"
                description="Cuando está desactivada o falla, AlphaWave usa el fallback heurístico."
                checked={Boolean(llmStatus?.user_enabled)}
                disabled={
                  busy
                  || (
                    !llmStatus?.user_enabled
                    && (
                      llmStatus?.validation_status !== "ready"
                      || llmStatus?.validated_model !== llmStatus?.model
                      || llmStatus?.model_available === false
                      || llmStatus?.model_supported === false
                    )
                  )
                }
                disabledReason="Guardá la clave y validá la conexión antes de habilitar OpenAI."
                onChange={(enabled) => void onUpdateOpenAI({ enabled }).catch(() => undefined)}
              />

              <div className="settings-readonly-grid compact">
                <ReadOnlySetting label="Provider" value={llmProvider} />
                <ReadOnlySetting label="Modelo" value={llmStatus?.model ?? "—"} />
                <ReadOnlySetting label="Uso seguro" value={llmStatus?.safe_to_use ? "Sí" : "No"} />
                <ReadOnlySetting label="Fallback heurístico" value={llmStatus?.fallback_available ? "Disponible" : "No disponible"} />
                <ReadOnlySetting label="Última validación" value={llmStatus?.last_validated_at ? relativeTimeLabel(llmStatus.last_validated_at) : "—"} />
                <ReadOnlySetting label="Último uso exitoso" value={llmStatus?.last_success_at ? relativeTimeLabel(llmStatus.last_success_at) : "—"} />
              </div>
              {llmStatus?.last_error ? <small role="status">Último error: {llmStatus.last_error}</small> : null}

              {llmStatus?.api_key_configured ? (
                <div className="openai-revoke-row">
                  {confirmOpenAIRevoke ? (
                    <>
                      <span>¿Revocar la API key guardada?</span>
                      <button
                        type="button"
                        className="danger-action"
                        disabled={busy}
                        onClick={() => {
                          void onRevokeOpenAI()
                            .then(() => setConfirmOpenAIRevoke(false))
                            .catch(() => undefined);
                        }}
                        data-testid="openai-revoke-confirm"
                      >
                        Confirmar revocación
                      </button>
                      <button type="button" className="secondary-action subtle" onClick={() => setConfirmOpenAIRevoke(false)}>
                        Cancelar
                      </button>
                    </>
                  ) : (
                    <button type="button" className="secondary-action subtle" onClick={() => setConfirmOpenAIRevoke(true)}>
                      Revocar API key
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          </div>

          <div className="settings-subsection" id="settings-advanced-instance">
            <SubsectionTitle title="Configuración de instancia" help="Datos del deployment. La UI sólo muestra estado y nunca expone secretos." />
            <div className="settings-readonly-grid">
              <ReadOnlySetting label="Telegram token" value={configuredLabel(advancedInstance.telegram_token_configured)} />
              <ReadOnlySetting label="Trello credenciales" value={configuredLabel(advancedInstance.trello_credentials_configured)} />
              <ReadOnlySetting label="Trello boards" value={configuredLabel(advancedInstance.trello_boards_configured)} />
              <ReadOnlySetting label="Proveedor IA" value="openai" />
              <ReadOnlySetting label="Base de datos" value={String(advancedInstance.database_path ?? "No disponible")} />
              <ReadOnlySetting label="Host" value={String(advancedInstance.host ?? "No disponible")} />
              <ReadOnlySetting label="Puerto" value={String(advancedInstance.port ?? "No disponible")} />
            </div>
            <small>Los secretos de usuario se guardan cifrados. Las rutas sensibles permanecen fuera de la UI.</small>
          </div>
        </section>
        </div>
      </div>
      {showStickySaveBar ? (
        <div className={saveState.status === "error" ? "settings-save-bar error" : "settings-save-bar"} role="status" aria-live="polite" data-testid="settings-save-bar">
          <div>
            <strong>{saveState.status === "error" ? "No se pudieron guardar los cambios" : "Cambios sin guardar"}</strong>
            <span>{saveState.status === "error" ? saveState.message : "Guardá o descartá antes de salir de Configuración."}</span>
          </div>
          <div className="settings-save-bar-actions">
            <button className="secondary-action subtle" type="button" onClick={onDiscard} disabled={busy}>
              Descartar
            </button>
            <button className="secondary-action" type="button" onClick={onSave} disabled={busy || !dirty}>
              {busy ? "Guardando…" : "Guardar cambios"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function TrelloBoardModal({
  existingAliases,
  existingBoardIds,
  discoveredBoards,
  onDiscoverBoards,
  onAdd,
  onClose,
}: {
  existingAliases: string[];
  existingBoardIds: string[];
  discoveredBoards: TrelloAvailableBoard[];
  onDiscoverBoards: () => void;
  onAdd: (draft: { alias: string; name: string; boardId: string }) => void;
  onClose: () => void;
}) {
  const [alias, setAlias] = useState("");
  const [name, setName] = useState("");
  const [boardId, setBoardId] = useState("");
  const cleanAlias = alias.trim();
  const cleanBoardId = boardId.trim();
  const aliasExists = existingAliases.some((value) => value.toLowerCase() === cleanAlias.toLowerCase());
  const boardIdExists = cleanBoardId && existingBoardIds.includes(cleanBoardId);
  const cleanName = name.trim();
  const validAlias = /^[A-Za-z0-9_]{2,24}$/.test(cleanAlias);
  const error = !validAlias
    ? "Usá 2 a 24 letras, números o guión bajo."
    : aliasExists
      ? "Ese alias ya existe."
      : boardIdExists
        ? "Ese board_id ya está usado."
        : !cleanName
          ? "El board necesita un nombre visible."
          : !cleanBoardId
            ? "Elegí un board Trello o pegá un Board ID."
            : "";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (error) return;
    onAdd({ alias: cleanAlias, name: cleanName, boardId: cleanBoardId });
  }

  function selectDiscoveredBoard(boardId: string) {
    const board = discoveredBoards.find((item) => item.id === boardId);
    if (!board) return;
    setBoardId(board.id);
    setName(board.name);
    const suggested = suggestBoardAlias(board.name);
    if (!existingAliases.some((value) => value.toLowerCase() === suggested.toLowerCase())) {
      setAlias(suggested);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="trello-modal trello-board-modal" role="dialog" aria-modal="true" aria-labelledby="trello-board-modal-title" onSubmit={submit}>
        <div className="sort-modal-header">
          <div>
            <span className="eyebrow">Trello</span>
            <h2 id="trello-board-modal-title">Agregar board</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Cerrar">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="trello-discovery-picker">
          <label>
            Board Trello
            <select value={boardId} onChange={(event) => selectDiscoveredBoard(event.target.value)}>
              <option value="">{discoveredBoards.length ? "Seleccionar board..." : "Primero descubrí boards"}</option>
              {discoveredBoards.map((board) => (
                <option key={board.id} value={board.id}>
                  {board.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="secondary-action subtle" onClick={onDiscoverBoards}>
            Descubrir boards
          </button>
        </div>
        <label>
          Alias
          <input value={alias} onChange={(event) => setAlias(event.target.value)} autoFocus />
        </label>
        <label>
          Nombre
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Board ID
          <input value={boardId} onChange={(event) => setBoardId(event.target.value)} placeholder="Ej: 64abc..." />
        </label>
        <small>Se completa automáticamente al elegir un board de Trello. Manualmente acepta ID largo o shortLink de trello.com/b/AbCd1234/...</small>
        {boardIdExists ? <div className="notice">Este board ya está configurado.</div> : null}
        {error ? <div className="notice">{error}</div> : null}
        <div className="trello-modal-actions">
          <button type="submit" disabled={Boolean(error)}>
            Agregar
          </button>
          <button type="button" className="secondary-action subtle" onClick={onClose}>
            Cancelar
          </button>
        </div>
      </form>
    </div>
  );
}

function SettingsCardTitle({ title, help }: { title: string; help?: string }) {
  return (
    <div className="settings-title-row">
      <h3>{title}</h3>
      {help ? <HelpTooltip text={help} label={`Ayuda: ${title}`} /> : null}
    </div>
  );
}

function ReadinessGroup({ title, items }: { title: string; items: Record<string, { status: string; detail: string; fallback?: string | null }> }) {
  const labels: Record<string, string> = { runtime: "Aplicación", telegram: "Telegram", trello: "Trello", llm: "IA" };
  return (
    <div className="readiness-group">
      <strong>{title}</strong>
      {Object.entries(items).map(([key, item]) => (
        <div className="readiness-row" key={key}>
          <div>
            <span>{labels[key] ?? key}</span>
            <small>{item.detail}{item.fallback ? ` ${item.fallback}` : ""}</small>
          </div>
          <span className={`status-pill ${statusToneClass(item.status)}`}>{statusLabel(item.status)}</span>
        </div>
      ))}
    </div>
  );
}

function WeekendModeToggleCard({
  weekendMode,
  enabled,
  onChange,
}: {
  weekendMode: WeekendModeContext;
  enabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const title = enabled
    ? weekendMode.activeToday
      ? "🌿 Fin de semana activo"
      : "🌿 Fin de semana configurado"
    : "🌿 Modo fin de semana desactivado";
  const description = enabled
    ? weekendMode.activeToday
      ? "Hoy la app reduce ruido y prioriza tus categorías elegidas."
      : "No aplica hoy. Se activará en tus días seleccionados."
    : "La app opera con alertas normales.";
  return (
    <button
      className={enabled ? "mode-toggle-card active" : "mode-toggle-card"}
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={() => onChange(!enabled)}
    >
      <span>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <span className={enabled ? "mode-switch on" : "mode-switch"} aria-hidden="true">
        <span />
      </span>
    </button>
  );
}

function WeekendModeBadge({ onConfigure }: { onConfigure: () => void }) {
  return (
    <button
      className="weekend-mode-badge"
      type="button"
      title="Modo fin de semana activo"
      aria-label="Modo fin de semana activo. Abrir configuración"
      onClick={onConfigure}
    >
      <span aria-hidden="true">🌿</span>
      Fin de semana
    </button>
  );
}

function WeekendModeBanner({ weekendMode, onConfigure }: { weekendMode: WeekendModeContext; onConfigure: () => void }) {
  const scopesText = formatScopeList(weekendMode.activeScopes);
  return (
    <section className="weekend-mode-banner" aria-label="Modo fin de semana activo">
      <div>
        <strong>
          <span aria-hidden="true">🌿</span>
          Modo fin de semana activo
        </strong>
        <p>
          {weekendMode.activeScopes.length
            ? `Se priorizan tus categorías de descanso: ${scopesText}. Las demás categorías quedan en segundo plano.`
            : "Todas las categorías quedan visibles, con una señal de descanso activa para hoy."}
        </p>
      </div>
      <button type="button" onClick={onConfigure}>
        Configurar
      </button>
    </section>
  );
}

function AdvancedToggleRow({
  title,
  description,
  checked,
  disabled,
  disabledReason,
  warning,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  disabledReason: string;
  warning?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      className={checked ? "advanced-toggle-row active" : "advanced-toggle-row"}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span>
        <strong>{title}</strong>
        <small>{disabled ? disabledReason : description}</small>
        {warning && !disabled ? <small className="advanced-warning">{warning}</small> : null}
      </span>
      <span className={checked ? "mode-switch on" : "mode-switch"} aria-hidden="true">
        <span />
      </span>
    </button>
  );
}

function SettingsNav({ activeId, onNavigate }: { activeId: string; onNavigate: (id: string) => void }) {
  function handleNavigate(event: React.MouseEvent<HTMLAnchorElement>, id: string) {
    event.preventDefault();
    onNavigate(id);
    scrollToSettingsSection(id);
  }

  return (
    <nav className="settings-nav" aria-label="Secciones de configuración">
      <strong>Configuración</strong>
      {settingsSections.map((section) => (
        <a
          key={section.id}
          className={activeId === section.id ? "active" : ""}
          href={`#${section.id}`}
          aria-current={activeId === section.id ? "true" : undefined}
          onClick={(event) => handleNavigate(event, section.id)}
        >
          {section.label}
        </a>
      ))}
    </nav>
  );
}

function ReadOnlySetting({ label, value }: { label: string; value: string }) {
  return (
    <div className="readonly-setting">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function SubsectionTitle({ title, help }: { title: string; help?: string }) {
  return (
    <div className="settings-title-row">
      <h4>{title}</h4>
      {help ? <HelpTooltip text={help} label={`Ayuda: ${title}`} /> : null}
    </div>
  );
}

function LabelWithHelp({ label, help }: { label: string; help?: string }) {
  return (
    <span className="settings-label-with-help">
      <span>{label}</span>
      {help ? <HelpTooltip text={help} label={`Ayuda: ${label}`} /> : null}
    </span>
  );
}

function HelpTooltip({ text, label }: { text: string; label: string }) {
  return (
    <button className="help-tooltip" type="button" aria-label={label} title={text}>
      <CircleHelp size={15} aria-hidden="true" />
    </button>
  );
}

function CheckboxRow({
  label,
  help,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  help?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={disabled ? "settings-check disabled" : "settings-check"}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
      {help ? <HelpTooltip text={help} label={`Ayuda: ${label}`} /> : null}
    </label>
  );
}

function NumberGrid({
  values,
  source,
  onChange,
}: {
  values: Array<[string, string, string, string?]>;
  source: Record<string, any>;
  onChange: (key: string, value: number) => void;
}) {
  return (
    <div className="settings-number-grid">
      {values.map(([key, label, unit, help]) => (
        <label key={key}>
          <LabelWithHelp label={label} help={help} />
          <div className="settings-number-field">
            <input name={`setting-${key}`} type="number" value={source?.[key] ?? 0} onChange={(event) => onChange(key, Number(event.target.value))} />
            <span>{unit}</span>
          </div>
        </label>
      ))}
    </div>
  );
}

function PriorityCriterionRow({
  id,
  rank,
  label,
  description,
  enabled,
  onEnabledChange,
}: {
  id: string;
  rank: number;
  label: string;
  description: string;
  enabled: boolean;
  onEnabledChange: (checked: boolean) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  return (
    <div ref={setNodeRef} className={isDragging ? "priority-criterion-row dragging" : "priority-criterion-row"} style={style}>
      <button className="drag-handle" type="button" title="Reordenar criterio" aria-label={`Reordenar ${label}`} {...attributes} {...listeners}>
        <GripVertical size={16} aria-hidden="true" />
      </button>
      <span className="priority-rank">{rank}</span>
      <label>
        <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
        <span>
          <strong>{label}</strong>
          <small>{description}</small>
        </span>
      </label>
    </div>
  );
}

function getPath(source: Record<string, any>, path: string[], fallback: unknown) {
  let current: any = source;
  for (const key of path) {
    current = current?.[key];
  }
  return current ?? fallback;
}

function orderPriorityCriteria(criteria: Record<string, any>) {
  return Object.entries(criteria).sort((entryA, entryB) => Number(entryB[1]?.weight ?? 0) - Number(entryA[1]?.weight ?? 0));
}

function buildPriorityCriteria(criteria: Record<string, any>, orderedKeys: string[]) {
  const knownKeys = new Set(orderedKeys);
  const remainingKeys = Object.keys(criteria).filter((key) => !knownKeys.has(key));
  const allKeys = [...orderedKeys.filter((key) => criteria[key]), ...remainingKeys];
  return Object.fromEntries(
    allKeys.map((key, index) => [
      key,
      {
        ...criteria[key],
        weight: Math.max(1, 120 - index * 8),
      },
    ]),
  );
}

function isPriorityOrderCustom(preset: string, orderedKeys: string[]) {
  const presetOrder = priorityPresetOrders[preset];
  if (!presetOrder) return true;
  const comparablePreset = presetOrder.filter((key) => orderedKeys.includes(key));
  return comparablePreset.join("|") !== orderedKeys.join("|");
}

function advancedStatusRows(
  rawAdvanced: Record<string, any>,
  trelloStatus: TrelloStatus | null,
  briefingStatus: BriefingStatus | null,
  backupStatus: BackupStatus | null,
) {
  const { status } = normalizeAdvancedSettings(rawAdvanced);
  const remindersInterval = technicalIntervalLabel(status.reminders_interval_seconds, "segundos");
  const trelloSyncInterval = technicalIntervalLabel(status.trello_sync_interval_minutes, "minutos");
  const rows = [
    {
      label: "Telegram",
      value: statusLabel(status.telegram),
      tone: statusTone(status.telegram),
    },
    {
      label: "Trello",
      value: statusLabel(status.trello),
      tone: statusTone(status.trello),
    },
    {
      label: "Escritura en Trello",
      value: statusLabel(status.trello_write),
      tone: statusTone(status.trello_write),
    },
    {
      label: "Auto-confirmación Trello",
      value: statusLabel(status.trello_auto_confirm),
      tone: statusTone(status.trello_auto_confirm),
    },
    {
      label: "IA",
      value: statusLabel(status.llm),
      tone: statusTone(status.llm),
    },
    {
      label: "Recordatorios",
      value: remindersInterval.label,
      tone: remindersInterval.tone,
    },
    {
      label: "Trello sync",
      value: trelloSyncInterval.label,
      tone: trelloSyncInterval.tone,
    },
  ];
  if (trelloStatus?.last_sync_completed_at) {
    rows.push({
      label: "Último sync",
      value: relativeTimeLabel(trelloStatus.last_sync_completed_at),
      tone: trelloStatus.last_sync_status !== "error" ? "active" : "inactive",
    });
  }
  if (briefingStatus) {
    rows.push({
      label: "Último briefing",
      value: briefingStatus.last_run?.sent_at
        ? relativeTimeLabel(briefingStatus.last_run.sent_at)
        : briefingStatus.today_sent
          ? "Enviado hoy"
          : "No enviado hoy",
      tone: briefingStatus.today_sent ? "active" : "inactive",
    });
  }
  if (backupStatus?.latest_created_at) {
    rows.push({
      label: "Último backup",
      value: relativeTimeLabel(backupStatus.latest_created_at),
      tone: "active",
    });
  }
  return rows;
}

function systemStatusRows(
  systemStatus: SystemStatus | null,
  rawAdvanced: Record<string, any>,
  trelloStatus: TrelloStatus | null,
  briefingStatus: BriefingStatus | null,
  backupStatus: BackupStatus | null,
) {
  if (!systemStatus) {
    return advancedStatusRows(rawAdvanced, trelloStatus, briefingStatus, backupStatus);
  }
  return [
    { label: "Telegram", value: statusLabel(systemStatus.telegram.status), tone: statusTone(systemStatus.telegram.status) },
    { label: "Trello", value: statusLabel(systemStatus.trello.status), tone: statusTone(systemStatus.trello.status) },
    { label: "Escritura en Trello", value: statusLabel(systemStatus.trello_write.status), tone: statusTone(systemStatus.trello_write.status) },
    { label: "IA", value: statusLabel(systemStatus.llm.status), tone: statusTone(systemStatus.llm.status) },
    {
      label: "Recordatorios",
      value: systemStatus.reminders.status === "active" ? technicalIntervalLabel(systemStatus.reminders.interval_seconds, "segundos").label : statusLabel(systemStatus.reminders.status),
      tone: systemStatus.reminders.status === "active" ? "active" : statusTone(systemStatus.reminders.status),
    },
    {
      label: "Trello sync",
      value: systemStatus.trello_sync.status === "active" ? technicalIntervalLabel(systemStatus.trello_sync.interval_minutes, "minutos").label : statusLabel(systemStatus.trello_sync.status),
      tone: systemStatus.trello_sync.status === "active" ? "active" : statusTone(systemStatus.trello_sync.status),
    },
    {
      label: "Último sync",
      value: systemStatus.trello_sync.last_sync ? relativeTimeLabel(systemStatus.trello_sync.last_sync) : "Sin sync registrada",
      tone: systemStatus.trello_sync.last_sync ? "active" : "warning",
    },
    {
      label: "Briefing",
      value: statusLabel(systemStatus.briefing.status),
      tone: statusTone(systemStatus.briefing.status),
    },
    {
      label: "Último briefing",
      value: systemStatus.briefing.last_run ? relativeTimeLabel(systemStatus.briefing.last_run) : "Sin briefing registrado",
      tone: systemStatus.briefing.last_run ? "active" : "warning",
    },
    {
      label: "Backup automático",
      value: statusLabel(systemStatus.backup.status),
      tone: statusTone(systemStatus.backup.status),
    },
    {
      label: "Último backup",
      value: systemStatus.backup.last_backup ? relativeTimeLabel(systemStatus.backup.last_backup) : "Sin backups registrados",
      tone: systemStatus.backup.last_backup ? "active" : "warning",
    },
  ];
}

function systemObservabilityCards(
  systemStatus: SystemStatus | null,
  rawAdvanced: Record<string, any>,
  trelloStatus: TrelloStatus | null,
  briefingStatus: BriefingStatus | null,
  backupStatus: BackupStatus | null,
) {
  const services = systemStatus?.services ?? {};
  if (!systemStatus?.services) {
    return systemStatusRows(systemStatus, rawAdvanced, trelloStatus, briefingStatus, backupStatus).map((row) => ({
      title: row.label,
      status: row.value,
      tone: row.tone,
      detail: legacySystemRowDetail(row.label),
      meta: [] as string[],
    }));
  }
  const database = services.database ?? {};
  const telegram = services.telegram ?? {};
  const trello = services.trello ?? {};
  const reminders = services.reminders ?? {};
  const briefing = services.briefing ?? {};
  const backups = services.backups ?? {};
  const llm = services.llm ?? {};
  const weekendMode = systemStatus.weekend_mode ?? services.weekend_mode ?? {};
  const trelloBoards = Array.isArray(trello.boards) ? trello.boards : [];
  const trelloBoardSummary = trelloBoards.length
    ? trelloBoards.map((board: any) => `${board.alias}: ${board.enabled === false ? "desactivado" : statusLabel(board.status === "ok" ? "active" : board.status)}`).join(" · ")
    : "Sin boards configurados";
  const autoConfirmSummary = trelloBoards.length
    ? trelloBoards.map((board: any) => `${board.alias}: ${board.auto_confirm_writes ? "on" : "off"}`).join(" · ")
    : "Sin boards configurados";
  return [
    {
      title: "General",
      status: statusLabel(systemStatus.overall?.status === "ok" ? "active" : systemStatus.overall?.status),
      tone: statusTone(systemStatus.overall?.status === "ok" ? "active" : systemStatus.overall?.status),
      detail: database.readable ? "Backend y DB local accesibles" : "La DB no responde",
      meta: [
        `Timezone ${systemStatus.environment?.timezone ?? "no disponible"}`,
        `DB ${database.writable_check === "not_run_read_only" ? "sin writes en polling" : "verificada"}`,
      ],
    },
    {
      title: "Telegram",
      status: statusLabel(telegram.status),
      tone: statusTone(telegram.status),
      detail: telegram.enabled ? "Manual commands disponibles si está configurado" : "Desactivado desde Settings",
      meta: [
        telegram.last_update_at ? `Última actividad ${relativeTimeLabel(telegram.last_update_at)}` : "Sin actividad reciente",
        telegram.last_error ? `Último error: ${telegram.last_error}` : "Sin errores recientes",
      ],
    },
    {
      title: "Trello",
      status: statusLabel(trello.status),
      tone: statusTone(trello.status),
      detail: trelloBoardSummary,
      meta: [
        trello.last_sync_at ? `Último sync ${relativeTimeLabel(trello.last_sync_at)}` : "Sin sync registrada",
        trello.last_error ? `Último error: ${trello.last_error}` : "Sin errores recientes",
      ],
    },
    {
      title: "Escritura en Trello",
      status: statusLabel(trello.write_enabled ? "active" : "disabled"),
      tone: statusTone(trello.write_enabled ? "active" : "disabled"),
      detail: trello.write_enabled ? "Confirmación requerida salvo boards con auto-confirm" : "Writes desactivados desde Settings",
      meta: [trello.configured ? "Credenciales y boards configurados" : "Requiere configuración Trello"],
    },
    {
      title: "Auto-confirmación Trello",
      status: statusLabel(trelloBoards.some((board: any) => board.auto_confirm_writes) ? "warning" : "disabled"),
      tone: trelloBoards.some((board: any) => board.auto_confirm_writes) ? "warning" : "inactive",
      detail: trelloBoards.some((board: any) => board.auto_confirm_writes) ? "Hay boards que ejecutan sin confirmación pendiente" : "Desactivada en todos los boards",
      meta: [autoConfirmSummary],
    },
    {
      title: "Modo fin de semana",
      status: statusLabel(weekendMode.status ?? (weekendMode.active_today ? "active" : weekendMode.enabled ? "scheduled" : "disabled")),
      tone: weekendMode.active_today ? "active" : weekendMode.enabled ? "warning" : "inactive",
      detail: weekendMode.active_today ? "Menos ruido automático · tareas no ocultas" : weekendMode.enabled ? "Programado, no activo hoy" : "Sin tratamiento visual ni silencios",
      meta: [
        weekendMode.timezone ? `Timezone ${weekendMode.timezone}` : "Timezone no disponible",
        Array.isArray(weekendMode.muted) && weekendMode.muted.length ? `Muteado: ${weekendMode.muted.join(", ")}` : "Telegram manual siempre responde",
      ],
    },
    {
      title: "Recordatorios",
      status: statusLabel(reminders.status),
      tone: statusTone(reminders.status),
      detail: reminders.enabled ? `${reminders.pending_count ?? 0} pendientes · ${reminders.overdue_count ?? 0} vencidos` : "Desactivados desde Settings",
      meta: [`Próximo check: ${technicalIntervalLabel(reminders.interval_seconds, "segundos").label}`, reminders.last_sent_at ? `Último enviado ${relativeTimeLabel(reminders.last_sent_at)}` : "Sin envíos registrados"],
    },
    {
      title: "Briefing",
      status: statusLabel(briefing.status),
      tone: statusTone(briefing.status),
      detail: briefing.enabled ? `Horario ${briefing.time ?? "--:--"} · cutoff ${briefing.late_cutoff ?? "--:--"}` : "Desactivado desde Settings",
      meta: [briefing.last_run_at ? `Última corrida ${relativeTimeLabel(briefing.last_run_at)}` : "Sin briefing registrado", briefing.last_error ? `Último error: ${briefing.last_error}` : "Sin errores recientes"],
    },
    {
      title: "Backups",
      status: statusLabel(backups.status),
      tone: statusTone(backups.status),
      detail: backups.automatic_enabled ? `Automáticos activos · retención ${backups.retention_days ?? "?"} días` : "Automáticos desactivados",
      meta: [backups.last_backup_at ? `Último backup ${relativeTimeLabel(backups.last_backup_at)}` : "Sin backups registrados", backups.latest_backup_path_redacted ? `Archivo ${backups.latest_backup_path_redacted}` : "Sin archivo reciente"],
    },
    {
      title: "IA",
      status: statusLabel(llm.status),
      tone: statusTone(llm.status),
      detail: llmStatusDetail(llm),
      meta: [llm.readiness ? `Readiness: ${statusLabel(llm.readiness)}` : "Sin verificación", llm.last_error ? `Último error: ${llm.last_error}` : "Sin errores recientes"],
    },
  ];
}

function normalizeAdvancedSettings(advanced: Record<string, any>) {
  const hasNestedStatus = advanced.status && typeof advanced.status === "object";
  const status = hasNestedStatus
    ? advanced.status
    : {
        telegram: booleanStatus(advanced.telegram_enabled),
        trello: booleanStatus(advanced.trello_enabled),
        trello_write: booleanStatus(advanced.trello_write_enabled),
        trello_auto_confirm: "disabled",
        llm: "disabled",
        reminders_interval_seconds: advanced.reminders_poll_interval_seconds,
        trello_sync_interval_minutes: advanced.trello_sync_interval_minutes,
      };
  const editable =
    advanced.editable && typeof advanced.editable === "object"
      ? advanced.editable
      : {
          trello_write_enabled: Boolean(advanced.trello_write_enabled),
          trello_auto_confirm_writes: false,
        };
  const prerequisites =
    advanced.prerequisites && typeof advanced.prerequisites === "object"
      ? advanced.prerequisites
      : {
          trello_write_enabled: true,
          trello_auto_confirm_writes: Boolean(editable?.trello_write_enabled),
        };
  const instance = advanced.instance && typeof advanced.instance === "object" ? advanced.instance : {};
  return { status, editable, prerequisites, instance };
}

function booleanStatus(value: unknown) {
  if (value === true) return "active";
  if (value === false) return "disabled";
  return "unknown";
}

function statusLabel(value: unknown) {
  if (value === "active") return "Activo";
  if (value === "ready") return "Lista";
  if (value === "requires_encryption_key") return "Falta cifrado";
  if (value === "requires_api_key") return "Falta API key";
  if (value === "not_validated") return "Sin validar";
  if (value === "requires_configuration") return "Requiere configuración";
  if (value === "requires_restart") return "Requiere reinicio";
  if (value === "unverified") return "No verificado";
  if (value === "unavailable") return "No disponible";
  if (value === "disabled") return "Desactivado";
  if (value === "scheduled") return "Programado";
  if (value === "warning") return "Advertencia";
  if (value === "ok") return "Activo";
  if (value === "error") return "Error";
  if (value === "unknown") return "Requiere validación";
  if (typeof value === "boolean") return value ? "Activo" : "Desactivado";
  return "Requiere validación";
}

function integrationStatusLabel(value: unknown) {
  if (value === "linked") return "Vinculado";
  if (value === "linked_inbound_only") return "Vinculado entrada";
  if (value === "legacy_instance") return "Legacy instancia";
  if (value === "requires_encryption_key") return "Falta key cifrado";
  if (value === "not_linked") return "No vinculado";
  if (value === "configured") return "Configurado";
  if (value === "not_configured") return "No configurado";
  return statusLabel(value);
}

function credentialsSourceLabel(value: unknown) {
  if (value === "user_encrypted") return "Usuario cifrado";
  if (value === "instance_env") return "Instancia .env";
  if (value === "none") return "Sin credenciales";
  return String(value ?? "No disponible");
}

function secretHintPlaceholder(value: unknown, fallback: string) {
  if (typeof value !== "string" || !value.trim()) return fallback;
  const hint = value.trim();
  if (hint.includes("<redacted>") || hint.includes("...") || /^.{0,4}\*{3,}.{0,4}$/.test(hint)) {
    return hint;
  }
  return "Guardada";
}

function statusTone(value: unknown) {
  if (value === "active" || value === true) return "active";
  if (value === "ready") return "active";
  if (value === "scheduled") return "warning";
  if (value === "requires_configuration" || value === "requires_encryption_key" || value === "requires_api_key" || value === "not_validated" || value === "requires_restart" || value === "unknown" || value == null || value === "unavailable" || value === "unverified" || value === "warning") return "warning";
  return "inactive";
}

function statusToneClass(tone: unknown) {
  if (tone === "warning") return "warning";
  if (tone === "active") return "active";
  return "inactive";
}

function legacySystemRowDetail(label: string) {
  return (
    {
      Telegram: "Estado de Telegram y comandos manuales.",
      Trello: "Estado de credenciales, boards y sync.",
      "Escritura en Trello": "Controla writes con confirmación.",
      IA: "Estado del provider LLM configurado.",
      Recordatorios: "Scheduler local de recordatorios.",
      "Trello sync": "Intervalo y último sync Trello.",
      Briefing: "Última corrida de briefing.",
      Backups: "Último backup local registrado.",
      "Último sync": "Última sincronización completada.",
      "Último briefing": "Último briefing enviado.",
      "Último backup": "Último backup local.",
    }[label] ?? "Estado del servicio."
  );
}

function llmStatusDetail(llm: Record<string, any>) {
  if (!llm.enabled) return "OpenAI desactivada; fallback heurístico disponible";
  if (llm.readiness === "ready") return "OpenAI lista para uso";
  if (llm.readiness === "not_validated") return "API key guardada, pendiente de validación";
  if (llm.readiness === "requires_api_key") return "Falta guardar una API key";
  if (llm.readiness === "requires_encryption_key") return "Falta configurar el cifrado de secretos";
  return llm.reason ?? "OpenAI requiere atención";
}

function llmStatusMessage(status: LLMStatus) {
  if (status.status === "ready") return "Conexión con OpenAI validada.";
  return status.reason || "No se pudo validar OpenAI.";
}

function reconcileLLMStatusWithCatalog(
  status: LLMStatus | null,
  catalog: OpenAIModelCatalog,
): LLMStatus | null {
  if (!status) return status;
  const current = catalog.models.find((option) => option.id === status.model);
  if (current?.compatibility !== "unavailable") return status;
  return {
    ...status,
    status: "not_validated",
    model_available: false,
    safe_to_use: false,
    reason: "El modelo configurado ya no está disponible para esta API key.",
  };
}

function openAIStatusLabel(value: LLMStatus["status"] | undefined) {
  if (value === "ready") return "Lista";
  if (value === "not_validated") return "Sin validar";
  if (value === "requires_api_key") return "Falta API key";
  if (value === "requires_encryption_key") return "Falta cifrado";
  if (value === "error") return "Error";
  return "Desactivada";
}

function configuredLabel(value: unknown) {
  if (value === true) return "Configurado";
  if (value === false) return "Falta";
  return "No disponible";
}

function technicalIntervalLabel(value: unknown, unit: string) {
  const numeric = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  if (Number.isFinite(numeric) && numeric > 0) {
    return { label: `Cada ${numeric} ${unit}`, tone: "active" };
  }
  return { label: "No configurado", tone: "warning" };
}

function settingsPatchFromDraft(settings: Record<string, any>, original?: Record<string, any>) {
  const payload = structuredClone(settings);
  const advancedEditable = payload.advanced?.editable;
  const originalEditable = original?.advanced?.editable;
  if (advancedEditable && JSON.stringify(advancedEditable) !== JSON.stringify(originalEditable ?? null)) {
    payload.advanced = { editable: advancedEditable };
  } else {
    delete payload.advanced;
  }
  return payload;
}

function trelloBoardsFromSettings(settings: Record<string, any> | undefined | null): TrelloBoardSummary[] {
  const boards = settings?.trello?.boards;
  if (!boards || typeof boards !== "object") return [];
  const parsed = Object.entries(boards)
    .map(([key, board]: [string, any]) => ({
      alias: String(board?.alias || key).trim(),
      name: String(board?.name || board?.alias || key).trim(),
      enabled: board?.enabled !== false,
      configured: Boolean(String(board?.board_id ?? "").trim()),
      workflow_states: Array.isArray(board?.workflow_states) ? board.workflow_states : undefined,
      states: board?.states && typeof board.states === "object" ? board.states : undefined,
    }))
    .filter((board) => board.alias);
  return parsed;
}

function trelloBoardSelectOptions(board: Record<string, any>, discoveredBoards: TrelloAvailableBoard[]) {
  const options = new Map<string, TrelloAvailableBoard>();
  const currentId = String(board?.board_id ?? "").trim();
  if (currentId) {
    options.set(currentId, {
      id: currentId,
      name: String(board?.name || board?.alias || currentId),
      url: null,
      shortLink: null,
    });
  }
  for (const discovered of discoveredBoards) {
    options.set(discovered.id, discovered);
  }
  return Array.from(options.values());
}

function trelloLinkInfo(task: Task, boards: TrelloBoardSummary[]): TrelloTaskLinkInfo {
  if (task.status !== "active" || task.source_type !== "local" || task.source_id || task.source_url) {
    return { status: "not_applicable" };
  }
  const board = boards.find((item) => item.enabled && item.configured && item.alias.toLowerCase() === task.scope.toLowerCase());
  if (!board) return { status: "not_applicable" };
  const createState = trelloCreateCardState(board);
  if (!createState) return { status: "missing_mapping", board };
  return { status: "linkable", board, listName: createState.list_name ?? "TAREAS" };
}

function isTrelloLinkedTask(task: Task) {
  return task.source_type === "trello" && Boolean(task.source_id?.trim());
}

function trelloCreateCardState(board: TrelloBoardSummary) {
  const states = boardWorkflowStates(board, ["pending"]);
  return (
    states.find((state) => state.enabled && state.role === "pending" && workflowStateHasListId(state)) ??
    states.find((state) => state.enabled && state.required_for?.includes("create_card") && workflowStateHasListId(state)) ??
    null
  );
}

function availableScopesFromSettings(availableScopes: string[] | undefined | null) {
  return dedupeStrings(availableScopes?.length ? availableScopes : fallbackScopes);
}

function dedupeStrings(values: string[]) {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = String(value).trim();
    const key = normalized.toLocaleLowerCase();
    if (!normalized || seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
  }
  return result;
}

function dedupeExactStrings(values: string[]) {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = String(value).trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function suggestBoardAlias(name: string) {
  const compact = name.replace(/[^a-zA-Z0-9 ]/g, " ").trim();
  const genericProject = compact.match(/^project\s+(alpha|beta|gamma|delta)$/i);
  if (genericProject) return genericProject[1].toUpperCase();
  const words = compact.split(/\s+/).filter(Boolean);
  if (words.length > 1) return words.map((word) => word[0]).join("").slice(0, 4).toUpperCase();
  return compact.slice(0, 4).toUpperCase() || "BD";
}

function duplicateBoardOwner(boards: Record<string, any>, currentAlias: string, boardId: string | null | undefined) {
  if (!boardId) return null;
  const duplicate = Object.entries(boards).find(([alias, board]: [string, any]) => alias !== currentAlias && board?.board_id === boardId);
  return duplicate?.[0] ?? null;
}

function createTrelloBoardSettings(alias: string, name: string, boardId: string) {
  const workflow_states = [
    createWorkflowState("pending", "Tareas", "TAREAS"),
    createWorkflowState("in_progress", "En proceso", "EN PROCESO"),
    createWorkflowState("review", "En revisión", "EN REVISION"),
    createWorkflowState("completed", "Terminadas", "TERMINADAS"),
    createWorkflowState("perpetual", "Perpetuas", "Perpetuas", false),
  ];
  return {
    alias,
    name,
    board_id: boardId,
    enabled: true,
    auto_confirm_writes: false,
    ignored_list_ids: [],
    states: legacyStatesFromWorkflow(workflow_states),
    workflow_states,
  };
}

function createWorkflowState(role: string, label: string, listName: string | null, enabled = true) {
  return {
    key: role,
    label,
    role,
    enabled,
    required_for: workflowRequiredFor(role),
    list_id: null,
    list_name: listName,
  };
}

function boardWorkflowStates(board: Record<string, any>, fallbackStates: string[]) {
  if (Array.isArray(board.workflow_states)) {
    return board.workflow_states.map((state: any, index: number) => normalizeWorkflowState(state, index));
  }
  return fallbackStates.map((role) => {
    const mapping = board.states?.[role] ?? {};
    return createWorkflowState(role, trelloStateLabel(role), mapping.list_name ?? defaultWorkflowListName(role), Boolean(mapping.enabled ?? true));
  });
}

function normalizeWorkflowState(state: Record<string, any>, index: number) {
  const role = String(state.role || state.key || "custom_actionable");
  return {
    key: String(state.key || role || `state_${index + 1}`),
    label: String(state.label || trelloStateLabel(role)),
    role,
    enabled: state.enabled !== false,
    required_for: Array.isArray(state.required_for) ? state.required_for : workflowRequiredFor(role),
    list_id: state.list_id ?? null,
    list_name: state.list_name ?? null,
  };
}

function legacyStatesFromWorkflow(workflowStates: Array<Record<string, any>>) {
  const states: Record<string, any> = {};
  for (const state of workflowStates) {
    if (state.enabled === false || state.role === "ignored" || states[state.role]) continue;
    states[state.role] = { list_id: state.list_id ?? null, list_name: state.list_name ?? null };
  }
  return states;
}

function listsForTrelloBoard(board: Record<string, any>, listsByBoardId: Record<string, TrelloList[]>) {
  const boardId = String(board?.board_id ?? "");
  const lists = new Map<string, TrelloList>();
  if (boardId) {
    for (const list of listsByBoardId[boardId] ?? []) {
      lists.set(list.id, list);
    }
  }
  for (const state of boardWorkflowStates(board, Object.keys(board?.states ?? {}))) {
    const listId = String(state.list_id ?? "").trim();
    const listName = String(state.list_name ?? "").trim();
    if (listId && !lists.has(listId)) {
      lists.set(listId, { id: listId, name: listName || `Lista ${listId.slice(-6)}` });
    }
  }
  return Array.from(lists.values());
}

function dedupeTrelloLists(lists: TrelloList[]) {
  const seen = new Set<string>();
  const result: TrelloList[] = [];
  for (const list of lists) {
    if (!list.id || seen.has(list.id) || list.closed) continue;
    seen.add(list.id);
    result.push({ id: String(list.id), name: String(list.name), closed: Boolean(list.closed) });
  }
  return result;
}

function trelloListOptionLabel(list: TrelloList, lists: TrelloList[]) {
  const duplicateName = lists.filter((item) => item.name === list.name).length > 1;
  return duplicateName ? `${list.name} · ${list.id.slice(-6)}` : list.name;
}

function remapWorkflowStatesForBoard(workflowStates: Array<Record<string, any>>, previousLists: TrelloList[], nextLists: TrelloList[]) {
  const previousIds = new Set(previousLists.map((list) => list.id));
  const byName = new Map<string, TrelloList[]>();
  for (const list of nextLists) {
    const key = list.name.toLowerCase();
    byName.set(key, [...(byName.get(key) ?? []), list]);
  }
  return workflowStates.map((state) => {
    const currentId = String(state.list_id ?? "");
    const currentName = String(state.list_name ?? "");
    if (currentId && nextLists.some((list) => list.id === currentId)) return state;
    if (currentId && !previousIds.has(currentId)) return { ...state, list_id: null, list_name: null };
    const matches = currentName ? byName.get(currentName.toLowerCase()) ?? [] : [];
    if (matches.length === 1) return { ...state, list_id: matches[0].id, list_name: matches[0].name };
    return { ...state, list_id: null, list_name: null };
  });
}

function defaultWorkflowListName(role: string) {
  return (
    {
      pending: "TAREAS",
      in_progress: "EN PROCESO",
      review: "EN REVISION",
      completed: "TERMINADAS",
      perpetual: "Perpetuas",
    }[role] ?? null
  );
}

function workflowRequiredFor(role: string) {
  return (
    {
      pending: ["create_card", "start_transition"],
      in_progress: ["start_transition", "stale_checkins"],
      review: ["finish_from_in_progress"],
      completed: ["complete"],
    }[role] ?? []
  );
}

function buildWeekendMode(settings: Record<string, any> | undefined | null): WeekendModeContext {
  const timezone = String(settings?.general?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const weekend = settings?.modes?.weekend ?? {};
  const activeDays = normalizeWeekdays(weekend.active_days);
  const activeScopes = normalizeStringArray(weekend.active_scopes);
  const enabled = Boolean(weekend.enabled);
  const today = currentWeekdayName(timezone);
  const activeToday = enabled && Boolean(today && activeDays.includes(today));
  return {
    enabled,
    active: activeToday,
    activeToday,
    activeDays,
    activeScopes,
    timezone,
    nextActiveDay: enabled && !activeToday ? nextActiveWeekday(activeDays, timezone) : null,
  };
}

function normalizeWeekdays(days: unknown) {
  const values = Array.isArray(days) ? days : [];
  return values
    .map((day) => {
      if (typeof day === "number") return weekdayOrder[day] ?? "";
      return String(day).trim().toLowerCase();
    })
    .filter((day) => weekdayOrder.includes(day));
}

function normalizeStringArray(values: unknown) {
  return Array.isArray(values) ? values.map(String).filter(Boolean) : [];
}

function currentWeekdayName(timezone: string) {
  try {
    return new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: timezone }).format(new Date()).toLowerCase();
  } catch {
    return new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(new Date()).toLowerCase();
  }
}

function nextActiveWeekday(activeDays: string[], timezone: string) {
  if (!activeDays.length) return null;
  const now = new Date();
  for (let offset = 1; offset <= 7; offset += 1) {
    const candidate = new Date(now);
    candidate.setDate(now.getDate() + offset);
    const day = (() => {
      try {
        return new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: timezone }).format(candidate).toLowerCase();
      } catch {
        return new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(candidate).toLowerCase();
      }
    })();
    if (activeDays.includes(day)) return day;
  }
  return null;
}

function formatScopeList(values: string[]) {
  if (!values.length) return "";
  if (values.length === 1) return values[0];
  if (values.length === 2) return `${values[0]} y ${values[1]}`;
  return `${values.slice(0, -1).join(", ")} y ${values[values.length - 1]}`;
}

function weekendTaskScopeState(task: Task, weekendMode?: WeekendModeContext) {
  if (!weekendMode?.active) return "normal";
  if (!weekendMode.activeScopes.length) return "active";
  return weekendMode.activeScopes.includes(task.scope) ? "active" : "muted";
}

function trelloStatusLabel(status: TrelloStatus | null) {
  if (!status) return "Cargando estado…";
  if (!status.enabled) return "Desactivado";
  if (!status.configured) return "Faltan credenciales o boards";
  if (status.last_sync_error) return `Error: ${status.last_sync_error}`;
  if (status.last_sync_completed_at) return `${status.last_sync_status ?? "ok"} · ${relativeTimeLabel(status.last_sync_completed_at)}`;
  return "Sin sync registrada";
}

function trelloBoardMappingStatus(board: Record<string, any>, trelloStates: string[]) {
  const workflowStates = boardWorkflowStates(board, trelloStates);
  const enabledStates = workflowStates.filter((state) => state.enabled !== false);
  const missing = enabledStates.filter((state) => !workflowStateHasListId(state));
  if (!missing.length) {
    const disabled = workflowStates.filter((state) => state.enabled === false);
    return {
      ok: true,
      label: "Mapeos completos",
      detail: disabled.length ? `${disabled.map((state) => state.label).join(", ")} desactivado para este board.` : "Listo para validar o escribir con list_id.",
    };
  }
  const missingLabels = missing.map((state) => state.label || trelloStateLabel(state.role)).join(", ");
  return {
    ok: false,
    label: "Pendiente",
    detail: `Faltan mappings para ${missingLabels}. Los writes relacionados quedan bloqueados hasta corregirlo.`,
  };
}

function workflowStateHasListId(state: Record<string, any>) {
  return Boolean(String(state.list_id ?? "").trim());
}

function backupStatusLabel(status: BackupStatus | null) {
  if (!status) return "Cargando estado…";
  if (!status.latest_created_at) return "Todavía no hay backups registrados.";
  return relativeTimeLabel(status.latest_created_at);
}

function loadableData<T>(state: Loadable<T>): T | null {
  if (state.state === "ready" || state.state === "stale") return state.data;
  if (state.state === "loading") return state.data ?? null;
  return null;
}

function loadableUpdatedAt<T>(state: Loadable<T>): string {
  if ((state.state === "ready" || state.state === "stale" || state.state === "loading") && state.updatedAt) return state.updatedAt;
  const data = loadableData(state) as SystemStatus | null;
  return statusGeneratedAt(data);
}

function statusGeneratedAt(data: SystemStatus | null | undefined) {
  return data?.generated_at || new Date().toISOString();
}

function systemStatusCopyForState(state: Loadable<SystemStatus>) {
  if (state.state === "loading" && !state.data) return "Cargando diagnóstico read-only...";
  if (state.state === "loading" && state.data) return "Diagnóstico read-only actualizado.";
  if (state.state === "ready") return "Diagnóstico read-only actualizado.";
  if (state.state === "stale") return "Mostrando último diagnóstico disponible.";
  if (state.state === "error") return "No se pudo cargar el diagnóstico.";
  return "Sin diagnóstico cargado.";
}

function systemStatusUpdatedLabelForState(state: Loadable<SystemStatus>) {
  const data = loadableData(state);
  if (!data) return "Sin diagnóstico cargado";
  const updatedAt = loadableUpdatedAt(state);
  if (!updatedAt) return "Actualizado recientemente";
  return `Actualizado ${relativeTimeLabel(updatedAt)}`;
}

function systemStatusRuntimeLabel(status: SystemStatus | null) {
  const environment = status?.environment ?? {};
  const mode = String(environment.mode ?? "local");
  const branch = environment.git_branch ? ` · ${String(environment.git_branch)}` : "";
  const commit = environment.git_commit ? ` · ${String(environment.git_commit)}` : " · commit desconocido";
  const dirty = environment.git_dirty === true ? " · cambios locales" : environment.git_dirty === false ? " · limpio" : "";
  const ahead = Number.isFinite(Number(environment.git_ahead)) && Number(environment.git_ahead) > 0 ? ` · ahead ${Number(environment.git_ahead)}` : "";
  const uptime = Number.isFinite(Number(environment.uptime_seconds)) ? ` · uptime ${durationLabel(Number(environment.uptime_seconds))}` : "";
  return `Runtime ${mode}${branch}${commit}${dirty}${ahead}${uptime}`;
}

function durationLabel(seconds: number) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function totalBackupSize(backups: BackupMetadata[]) {
  return backups.reduce((total, backup) => total + (backup.size_bytes || 0), 0);
}

function formatBytes(value: number | null | undefined) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let amount = bytes / 1024;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function backupSourceLabel(source: BackupMetadata["source"]) {
  return (
    {
      manual: "Manual",
      automatic: "Automático",
      pre_restore: "Pre-restore",
      unknown: "Sin metadata",
    }[source] ?? "Sin metadata"
  );
}

function relativeTimeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const diffMinutes = Math.round(Math.abs(diffMs) / 60000);
  const suffix = diffMs >= 0 ? "hace" : "en";
  if (diffMinutes < 1) return "recién";
  if (diffMinutes < 60) return `${suffix} ${diffMinutes} min`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${suffix} ${diffHours} h`;
  const diffDays = Math.round(diffHours / 24);
  return `${suffix} ${diffDays} día${diffDays === 1 ? "" : "s"}`;
}

function settingsSaveStatusText(dirty: boolean, saveState: SettingsSaveState, busy: boolean) {
  if (busy && dirty) return "Guardando cambios…";
  if (saveState.status === "error") return "No se pudieron guardar los cambios";
  if (dirty) return "Cambios sin guardar";
  if (saveState.status === "saved") return `Guardado ${relativeTimeLabel(saveState.savedAt)}`;
  return "Guardado";
}

function timezoneDisplay(timezone?: string) {
  if (!timezone) return "Zona horaria no configurada";
  try {
    const [offset] = formatTimezoneLabel(timezone).split(" — ");
    return `${timezone} · ${offset}`;
  } catch {
    return timezone;
  }
}

function formatTimeForZone(timezone?: string) {
  try {
    return new Intl.DateTimeFormat("es", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: timezone || undefined,
    }).format(new Date());
  } catch {
    return new Intl.DateTimeFormat("es", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
  }
}

function weekdayLabel(day: string) {
  return (
    {
      monday: "Lun",
      tuesday: "Mar",
      wednesday: "Mié",
      thursday: "Jue",
      friday: "Vie",
      saturday: "Sáb",
      sunday: "Dom",
    }[day] ?? day
  );
}

function trelloStateLabel(state: string) {
  return (
    {
      pending: "Tareas",
      in_progress: "En proceso",
      review: "En revisión",
      completed: "Terminadas",
      perpetual: "Perpetuas",
    }[state] ?? state
  );
}

function workflowRoleLabel(role: string) {
  return (
    {
      pending: "Pendiente",
      in_progress: "En proceso",
      review: "Revisión",
      completed: "Completadas",
      perpetual: "Perpetua",
      ignored: "Ignorar",
      blocked: "Bloqueadas",
      backlog: "Backlog",
      custom_actionable: "Custom accionable",
      custom_passive: "Custom pasiva",
    }[role] ?? role
  );
}

function presetLabel(preset: string) {
  return (
    {
      balanced: "Balanceado",
      deadlines_first: "Deadlines primero",
      quick_wins: "Quick wins",
      deep_work: "Trabajo profundo",
      custom: "Personalizado",
    }[preset] ?? preset
  );
}

function BriefingView({
  status,
  runs,
  preview,
  busy,
  onGenerate,
  onSendTest,
}: {
  status: BriefingStatus | null;
  runs: BriefingRun[];
  preview: BriefingPayload | null;
  busy: boolean;
  onGenerate: () => void;
  onSendTest: () => void;
}) {
  return (
    <section className="task-section">
      <div className="section-heading">
        <h2>Briefing</h2>
      </div>
      <div className="briefing-actions">
        <button type="button" onClick={onGenerate} disabled={busy}>
          Generar preview
        </button>
        <button type="button" onClick={onSendTest} disabled={busy}>
          Enviar test
        </button>
      </div>
      {status ? (
        <div className="briefing-status">
          <span>Hora: {status.time}</span>
          <span>Cutoff: {status.late_cutoff}</span>
          <span>Timezone: {status.timezone}</span>
          <span>Enviado hoy: {status.today_sent ? "sí" : "no"}</span>
        </div>
      ) : null}
      {preview ? <pre className="briefing-preview">{preview.text}</pre> : null}
      <div className="confirmation-list">
        {runs.length === 0 ? (
          <div className="empty">Sin runs de briefing</div>
        ) : (
          runs.map((run) => (
            <article className="confirmation-row" key={run.id}>
              <div>
                <strong>
                  {run.briefing_date} · {run.status}
                </strong>
                <span>{run.reason ?? "sin motivo"} · {run.sent_at ?? run.created_at}</span>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function ConfirmationView({
  confirmations,
  trelloBoards,
  trelloBoard,
  trelloCardTitle,
  busy,
  onBoardChange,
  onTitleChange,
  onCreateCard,
  onConfirm,
  onCancel,
  onBulkConfirm,
  onBulkCancel,
}: {
  confirmations: Confirmation[];
  trelloBoards: Array<{ alias: string; name: string; enabled: boolean }>;
  trelloBoard: string;
  trelloCardTitle: string;
  busy: boolean;
  onBoardChange: (value: string) => void;
  onTitleChange: (value: string) => void;
  onCreateCard: (event: FormEvent) => void;
  onConfirm: (confirmation: Confirmation) => void;
  onCancel: (confirmation: Confirmation) => void;
  onBulkConfirm: (confirmations: Confirmation[]) => Promise<BackgroundJobQueued>;
  onBulkCancel: (confirmations: Confirmation[]) => Promise<ConfirmationBulkResponse>;
}) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkAction, setBulkAction] = useState<"confirm" | "cancel" | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);
  const [bulkResult, setBulkResult] = useState<ConfirmationBulkResponse | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const selectedConfirmations = useMemo(
    () => confirmations.filter((confirmation) => selectedIds.includes(confirmation.id)),
    [confirmations, selectedIds],
  );
  const allSelected = confirmations.length > 0 && selectedIds.length === confirmations.length;
  const bulkActionLabel = bulkAction === "confirm" ? "Confirmar" : "Cancelar";

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => confirmations.some((confirmation) => confirmation.id === id)));
  }, [confirmations]);

  function toggleSelection(confirmationId: string) {
    setBulkResult(null);
    setBulkError(null);
    setSelectedIds((current) =>
      current.includes(confirmationId)
        ? current.filter((selectedId) => selectedId !== confirmationId)
        : [...current, confirmationId],
    );
  }

  function selectAllConfirmations() {
    setBulkResult(null);
    setBulkError(null);
    setSelectedIds(confirmations.map((confirmation) => confirmation.id));
  }

  function clearSelection() {
    setSelectedIds([]);
    setBulkResult(null);
    setBulkError(null);
  }

  async function processBulkAction() {
    if (!bulkAction || selectedConfirmations.length === 0) return;
    const action = bulkAction;
    setBulkProcessing(true);
    setBulkError(null);
    setBulkResult(null);
    setBulkProgress({ done: 0, total: selectedConfirmations.length });
    try {
      if (action === "confirm") {
        await onBulkConfirm(selectedConfirmations);
      } else {
        const result = await onBulkCancel(selectedConfirmations);
        setBulkResult(result);
      }
      setSelectedIds([]);
      setBulkAction(null);
      setBulkProgress(null);
    } catch (nextError) {
      setBulkError(readError(nextError));
      setBulkProgress(null);
    } finally {
      setBulkProcessing(false);
    }
  }

  function openBulkModal(action: "confirm" | "cancel") {
    if (selectedConfirmations.length === 0) return;
    setBulkAction(action);
    setBulkError(null);
    setBulkProgress(null);
  }

  function closeBulkModal() {
    setBulkAction(null);
    setBulkProgress(null);
  }

  return (
    <section className="task-section">
      <div className="section-heading">
        <div>
          <h2>Confirmaciones</h2>
          <p>{confirmations.length} pendiente(s). Seleccioná una o más para operar en lote.</p>
        </div>
        {confirmations.length > 0 ? (
          <div className="confirmation-tools">
            {confirmations.length > 1 ? (
              <button type="button" className="secondary-action subtle" onClick={selectAllConfirmations} disabled={allSelected || bulkProcessing}>
                Seleccionar todas
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <form className="trello-create-form" onSubmit={onCreateCard}>
        <select value={trelloBoard} onChange={(event) => onBoardChange(event.target.value)} disabled={busy || trelloBoards.length === 0}>
          {trelloBoards.length === 0 ? <option value="">Sin boards activos</option> : null}
          {trelloBoards.map((board) => (
            <option key={board.alias} value={board.alias}>
              {board.alias}
            </option>
          ))}
        </select>
        <input
          value={trelloCardTitle}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="Nueva card Trello"
          disabled={busy}
        />
        <button type="submit" disabled={busy || trelloBoards.length === 0 || !trelloCardTitle.trim()}>
          Proponer
        </button>
      </form>
      {bulkResult ? (
        <div className={bulkResult.failed > 0 ? "bulk-result warning" : "bulk-result"} role="status">
          <strong>{formatBulkConfirmationSummary(bulkResult, bulkResult.cancelled > 0 ? "cancel" : "confirm")}</strong>
          {bulkResult.failed > 0 ? (
            <ul className="bulk-result-list">
              {bulkResult.results
                .filter((result) => result.status === "failed")
                .map((result) => (
                  <li key={result.id}>{toHumanError(result.error ?? "No se pudo procesar esta confirmación.")}</li>
                ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {bulkError ? (
        <div className="notice" role="alert">
          {bulkError}
        </div>
      ) : null}
      {confirmations.length > 0 ? (
        <div className="confirmation-bulk-toolbar" aria-live="polite">
          <span>{selectedIds.length} de {confirmations.length} seleccionada(s)</span>
          <div className="confirmation-bulk-actions">
            <button
              type="button"
              onClick={() => openBulkModal("confirm")}
              disabled={busy || bulkProcessing || selectedConfirmations.length === 0}
            >
              Confirmar seleccionadas
            </button>
            <button
              type="button"
              className="secondary-action danger"
              onClick={() => openBulkModal("cancel")}
              disabled={busy || bulkProcessing || selectedConfirmations.length === 0}
            >
              Cancelar seleccionadas
            </button>
            <button type="button" className="secondary-action subtle" onClick={clearSelection} disabled={selectedIds.length === 0}>
              Limpiar
            </button>
          </div>
        </div>
      ) : null}
      {confirmations.length === 0 ? (
        <div className="empty">Sin confirmaciones pendientes. Las acciones Trello que requieran aprobación aparecerán acá.</div>
      ) : (
        <div className="confirmation-list">
          {confirmations.map((confirmation) => {
            const detail = confirmationDetail(confirmation);
            return (
              <article className="confirmation-row" key={confirmation.id}>
                <div className="confirmation-main">
                  <label className="confirmation-select" aria-label={`Seleccionar ${confirmation.summary ?? confirmation.action_type}`}>
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(confirmation.id)}
                      onChange={() => toggleSelection(confirmation.id)}
                      disabled={busy || bulkProcessing}
                    />
                  </label>
                  <div>
                    <strong>{confirmation.summary ?? confirmation.action_type}</strong>
                    {detail ? <small>{detail}</small> : null}
                    <span>vence: {formatDateTime(confirmation.expires_at)}</span>
                  </div>
                </div>
                <div className="row-actions">
                  <button type="button" onClick={() => onConfirm(confirmation)} disabled={busy || bulkProcessing}>
                    Confirmar
                  </button>
                  <button type="button" onClick={() => onCancel(confirmation)} disabled={busy || bulkProcessing}>
                    Cancelar
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {bulkAction ? (
        <div className="modal-backdrop" role="presentation">
          <div className="bulk-modal" role="dialog" aria-modal="true" aria-labelledby="bulk-confirmation-title">
            <div className="sort-modal-header">
              <div>
                <span className="eyebrow">Acción masiva</span>
                <h2 id="bulk-confirmation-title">{bulkActionLabel} confirmaciones</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={closeBulkModal}
                aria-label="Cerrar"
                disabled={bulkProcessing}
              >
                <X size={18} />
              </button>
            </div>
            <p className="sort-summary">
              Vas a {bulkAction === "confirm" ? "ejecutar" : "cancelar"} {selectedConfirmations.length} confirmación(es).
              {bulkAction === "confirm" ? ` ${bulkConfirmHint(selectedConfirmations)}` : " No se aplicará ningún cambio."}
              {" "}Se procesarán una por una.
            </p>
            <ul className="bulk-modal-list">
              {selectedConfirmations.slice(0, 6).map((confirmation) => (
                <li key={confirmation.id}>{confirmation.summary ?? confirmation.action_type}</li>
              ))}
              {selectedConfirmations.length > 6 ? <li>Y {selectedConfirmations.length - 6} más...</li> : null}
            </ul>
            {bulkProgress ? (
              <div className="bulk-progress" role="status">
                Procesando {bulkProgress.done}/{bulkProgress.total}
              </div>
            ) : null}
            <div className="trello-modal-actions">
              <button type="button" onClick={processBulkAction} disabled={bulkProcessing}>
                {bulkProcessing
                  ? "Procesando..."
                  : bulkAction === "confirm"
                    ? `Confirmar ${selectedConfirmations.length} acciones`
                    : `Cancelar ${selectedConfirmations.length} confirmaciones`}
              </button>
              <button type="button" className="secondary-action subtle" onClick={closeBulkModal} disabled={bulkProcessing}>
                Volver
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ReminderView({
  reminders,
  message,
  remindAt,
  busy,
  onMessageChange,
  onRemindAtChange,
  onCreate,
  onCancel,
}: {
  reminders: Reminder[];
  message: string;
  remindAt: string;
  busy: boolean;
  onMessageChange: (value: string) => void;
  onRemindAtChange: (value: string) => void;
  onCreate: (event: FormEvent) => void;
  onCancel: (reminder: Reminder) => void;
}) {
  const pending = reminders.filter((reminder) => reminder.status === "pending");
  const sent = [...reminders]
    .filter((reminder) => reminder.status === "sent")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 12);

  return (
    <section className="task-section">
      <div className="section-heading">
        <h2>Recordatorios</h2>
      </div>
      <form className="reminder-form" onSubmit={onCreate}>
        <input
          value={message}
          onChange={(event) => onMessageChange(event.target.value)}
          placeholder="Mensaje"
          disabled={busy}
        />
        <input
          type="datetime-local"
          value={remindAt}
          onChange={(event) => onRemindAtChange(event.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !message.trim() || !remindAt}>
          Crear
        </button>
      </form>

      <div className="reminder-columns">
        <div>
          <h3>Pendientes</h3>
          <ReminderList reminders={pending} onCancel={onCancel} />
        </div>
        <div>
          <h3>Enviados recientes</h3>
          <ReminderList reminders={sent} />
        </div>
      </div>
    </section>
  );
}

function PlanTodayView({
  plan,
  selectedId,
  onSelect,
  onComplete,
  onDelete,
  exitIntentById,
  weekendMode,
  trelloLinkInfoForTask,
}: {
  plan: TodayPlan;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onComplete: (task: Task) => void;
  onDelete: (task: Task) => void;
  exitIntentById?: Record<string, "restore" | "delete">;
  weekendMode: WeekendModeContext;
  trelloLinkInfoForTask?: (task: Task) => TrelloTaskLinkInfo;
}) {
  if (plan.groups.length === 0) {
    return (
      <TaskSection title="Hoy">
        <div className="empty">No hay tareas activas para planificar.</div>
      </TaskSection>
    );
  }

  return (
    <section className="task-section" data-testid="today-plan-view">
      <div className="section-heading">
        <div>
          <h2>Hoy</h2>
          {plan.summary ? <p>{plan.summary}</p> : null}
        </div>
      </div>
      {plan.warnings?.length ? <div className="notice">{plan.warnings.join(" ")}</div> : null}
      <div className="plan-grid">
        {plan.groups.map((group) => (
          <div className="plan-group" key={group.key} data-testid={`today-plan-group-${group.key}`}>
            <div className="plan-group-heading">
              <h3>{group.title}</h3>
              {group.summary ? <span>{group.summary}</span> : null}
            </div>
            <PlanItemList
              items={group.items}
              selectedId={selectedId}
              onSelect={onSelect}
              onComplete={onComplete}
              onDelete={onDelete}
              exitIntentById={exitIntentById}
              weekendMode={weekendMode}
              trelloLinkInfoForTask={trelloLinkInfoForTask}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

function PlanNowView({
  plan,
  selectedId,
  onSelect,
  onComplete,
  onDelete,
  exitIntentById,
  weekendMode,
  trelloLinkInfoForTask,
}: {
  plan: NowPlan;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onComplete: (task: Task) => void;
  onDelete: (task: Task) => void;
  exitIntentById?: Record<string, "restore" | "delete">;
  weekendMode: WeekendModeContext;
  trelloLinkInfoForTask?: (task: Task) => TrelloTaskLinkInfo;
}) {
  const primary = plan.recommended[0] ?? null;
  return (
    <section className="task-section" data-testid="now-plan-view">
      <div className="section-heading">
        <div>
          <h2>Ahora</h2>
          {plan.summary ? <p>{plan.summary}</p> : null}
        </div>
      </div>
      {plan.warnings?.length ? <div className="notice">{plan.warnings.join(" ")}</div> : null}
      <div className="plan-grid">
        <div className="plan-group now-primary" data-testid="now-next-action">
          <h3>Siguiente acción recomendada</h3>
          {primary ? (
            <PlanItemCard
              item={primary}
              selectedId={selectedId}
              onSelect={onSelect}
              onComplete={onComplete}
              onDelete={onDelete}
              exitIntentById={exitIntentById}
              weekendMode={weekendMode}
              trelloLinkInfoForTask={trelloLinkInfoForTask}
            />
          ) : (
            <div className="empty">No hay una próxima acción clara.</div>
          )}
        </div>
        <div className="plan-group" data-testid="now-alternatives">
          <h3>Alternativas</h3>
          {plan.alternatives?.length ? (
            <div className="plan-alternatives">
              {plan.alternatives.map((item) => (
                <article className="plan-alternative" key={`${item.kind}-${item.title}`}>
                  <strong>{item.title}</strong>
                  <span>{item.reason}</span>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty">Sin alternativas claras.</div>
          )}
        </div>
        {plan.afterwards.length ? (
          <div className="plan-group">
          <h3>Después</h3>
          <PlanItemList
            items={plan.afterwards}
            selectedId={selectedId}
            onSelect={onSelect}
            onComplete={onComplete}
            onDelete={onDelete}
            exitIntentById={exitIntentById}
            weekendMode={weekendMode}
            trelloLinkInfoForTask={trelloLinkInfoForTask}
          />
          </div>
        ) : null}
        {plan.avoid.length ? (
          <div className="plan-group">
          <h3>Evitar ahora</h3>
          <PlanItemList
            items={plan.avoid}
            selectedId={selectedId}
            onSelect={onSelect}
            weekendMode={weekendMode}
            trelloLinkInfoForTask={trelloLinkInfoForTask}
          />
          </div>
        ) : null}
      </div>
    </section>
  );
}

function PlanItemList({
  items,
  selectedId,
  onSelect,
  onComplete,
  onDelete,
  exitIntentById,
  weekendMode,
  trelloLinkInfoForTask,
}: {
  items: PlanItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onComplete?: (task: Task) => void;
  onDelete?: (task: Task) => void;
  exitIntentById?: Record<string, "restore" | "delete">;
  weekendMode: WeekendModeContext;
  trelloLinkInfoForTask?: (task: Task) => TrelloTaskLinkInfo;
}) {
  return (
    <div className="plan-item-list">
      {items.map((item) => (
        <PlanItemCard
          key={item.task.id}
          item={item}
          selectedId={selectedId}
          onSelect={onSelect}
          onComplete={onComplete}
          onDelete={onDelete}
          exitIntentById={exitIntentById}
          weekendMode={weekendMode}
          trelloLinkInfoForTask={trelloLinkInfoForTask}
        />
      ))}
    </div>
  );
}

function PlanItemCard({
  item,
  selectedId,
  onSelect,
  onComplete,
  onDelete,
  exitIntentById,
  weekendMode,
  trelloLinkInfoForTask,
}: {
  item: PlanItem;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onComplete?: (task: Task) => void;
  onDelete?: (task: Task) => void;
  exitIntentById?: Record<string, "restore" | "delete">;
  weekendMode: WeekendModeContext;
  trelloLinkInfoForTask?: (task: Task) => TrelloTaskLinkInfo;
}) {
  const task = item.task;
  return (
    <article className="plan-item-card">
      <TaskList
        tasks={[task]}
        selectedId={selectedId}
        onSelect={onSelect}
        onComplete={onComplete}
        onDelete={onDelete}
        exitIntentById={exitIntentById}
        weekendMode={weekendMode}
        trelloLinkInfoForTask={trelloLinkInfoForTask}
      />
      <div className="plan-item-reason">
        <span>{item.reason}</span>
        <div>
          {item.priority?.priority_band ? <strong>{priorityBandLabel(item.priority.priority_band)}</strong> : null}
          {task.estimated_minutes ? <strong>{task.estimated_minutes} min</strong> : null}
          {task.context_bucket ? <strong>{contextLabel(task.context_bucket)}</strong> : null}
        </div>
      </div>
    </article>
  );
}

function ReminderList({ reminders, onCancel }: { reminders: Reminder[]; onCancel?: (reminder: Reminder) => void }) {
  if (reminders.length === 0) {
    return <div className="empty">Sin recordatorios</div>;
  }

  return (
    <div className="reminder-list">
      {reminders.map((reminder) => (
        <article className="reminder-row" key={reminder.id}>
          <div className="reminder-icon">
            <Bell size={17} />
          </div>
          <div>
            <strong>{reminder.message}</strong>
            <span>{formatDue(reminder.remind_at)}</span>
          </div>
          {onCancel ? (
            <button
              className="icon-button danger"
              type="button"
              title="Cancelar"
              aria-label="Cancelar recordatorio"
              onClick={() => onCancel(reminder)}
            >
              <X size={17} />
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function PrimaryNav({
  view,
  onChange,
  todoCount,
  todayCount,
  nowCount,
}: {
  view: View;
  onChange: (view: View) => void;
  todoCount: number;
  todayCount: number;
  nowCount: number;
}) {
  return (
    <nav className="primary-nav" aria-label="Modos de trabajo">
      <PrimaryNavButton active={view === "todo"} onClick={() => onChange("todo")} label="TODO" count={todoCount} />
      <PrimaryNavButton active={view === "today"} onClick={() => onChange("today")} label="Hoy" count={todayCount} />
      <PrimaryNavButton active={view === "now"} onClick={() => onChange("now")} label="Ahora" count={nowCount} />
    </nav>
  );
}

function PrimaryNavButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button className={active ? "primary-tab active" : "primary-tab"} onClick={onClick} type="button">
      <span>{label}</span>
      <strong>{count}</strong>
    </button>
  );
}

function UtilityDock({
  view,
  onChange,
  completedCount,
  trashCount,
  reminderCount,
  confirmationCount,
  briefingCount,
}: {
  view: View;
  onChange: (view: View) => void;
  completedCount: number;
  trashCount: number;
  reminderCount: number;
  confirmationCount: number;
  briefingCount: number;
}) {
  return (
    <nav className="utility-dock" aria-label="Vistas secundarias">
      <DockButton
        active={view === "completed"}
        count={completedCount}
        icon={<CheckCircle2 size={18} aria-hidden="true" />}
        label="Completadas"
        onClick={() => onChange("completed")}
      />
      <DockButton
        active={view === "trash"}
        count={trashCount}
        icon={<Trash2 size={18} aria-hidden="true" />}
        label="Papelera"
        onClick={() => onChange("trash")}
      />
      <DockButton
        active={view === "reminders"}
        count={reminderCount}
        hideBadgeWhenZero
        icon={<Bell size={18} aria-hidden="true" />}
        label="Recordatorios"
        onClick={() => onChange("reminders")}
      />
      <DockButton
        active={view === "confirmations"}
        attention={confirmationCount > 0}
        count={confirmationCount}
        hideBadgeWhenZero
        icon={<BadgeCheck size={18} aria-hidden="true" />}
        label="Confirmaciones"
        onClick={() => onChange("confirmations")}
      />
      <DockButton
        active={view === "briefing"}
        count={briefingCount}
        hideBadgeWhenZero
        icon={<SunMedium size={18} aria-hidden="true" />}
        label="Briefing"
        onClick={() => onChange("briefing")}
      />
      <DockButton
        active={view === "settings"}
        showBadge={false}
        icon={<Settings size={18} aria-hidden="true" />}
        label="Configuración"
        onClick={() => onChange("settings")}
      />
    </nav>
  );
}

function DockButton({
  active,
  attention = false,
  count,
  hideBadgeWhenZero = false,
  icon,
  label,
  onClick,
  showBadge = true,
}: {
  active: boolean;
  attention?: boolean;
  count?: number;
  hideBadgeWhenZero?: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  showBadge?: boolean;
}) {
  const visibleBadge = Boolean(showBadge && count !== undefined && (!hideBadgeWhenZero || count > 0));
  const countText = visibleBadge
    ? label === "Confirmaciones"
      ? `${count} pendiente${count === 1 ? "" : "s"}`
      : `${count}`
    : "";
  return (
    <button
      className={["dock-button", active ? "active" : "", attention ? "attention" : ""].join(" ")}
      type="button"
      title={label}
      aria-label={visibleBadge ? `${label}, ${countText}` : label}
      onClick={onClick}
      data-testid={label === "Configuración" ? "settings-button" : undefined}
    >
      {icon}
      <span className="dock-label">{label}</span>
      {visibleBadge ? <strong>{count}</strong> : null}
    </button>
  );
}

function TrashView({
  tasks,
  selectedId,
  selectedTrashIds,
  selectionMode,
  busy,
  exitIntentById,
  onSelect,
  onToggleSelection,
  onStartSelection,
  onCancelSelection,
  onRestore,
  onRestoreSelected,
  onPermanentDelete,
  onPermanentDeleteSelected,
  onEmptyTrash,
}: {
  tasks: Task[];
  selectedId: string | null;
  selectedTrashIds: string[];
  selectionMode: boolean;
  busy: boolean;
  exitIntentById: Record<string, "restore" | "delete">;
  onSelect: (id: string) => void;
  onToggleSelection: (id: string) => void;
  onStartSelection: () => void;
  onCancelSelection: () => void;
  onRestore: (task: Task) => void;
  onRestoreSelected: () => void;
  onPermanentDelete: (task: Task) => void;
  onPermanentDeleteSelected: () => void;
  onEmptyTrash: () => void;
}) {
  const selectedCount = selectedTrashIds.length;
  const actions = selectionMode ? (
    <div className="trash-actions" aria-label="Acciones de selección">
      <span className="trash-selection-count">{selectedCount} seleccionada{selectedCount === 1 ? "" : "s"}</span>
      <button className="secondary-action" type="button" onClick={onRestoreSelected} disabled={busy || !selectedCount}>
        Restaurar
      </button>
      <button
        className="secondary-action danger"
        type="button"
        onClick={onPermanentDeleteSelected}
        disabled={busy || !selectedCount}
      >
        Eliminar definitivamente
      </button>
      <button className="secondary-action subtle" type="button" onClick={onCancelSelection} disabled={busy}>
        Cancelar
      </button>
    </div>
  ) : (
    <div className="trash-actions">
      <button className="secondary-action" type="button" onClick={onStartSelection} disabled={busy || !tasks.length}>
        Seleccionar
      </button>
      <button className="secondary-action danger" type="button" onClick={onEmptyTrash} disabled={busy || !tasks.length}>
        Vaciar Papelera
      </button>
    </div>
  );

  return (
    <TaskSection title="Papelera" meta={`${tasks.length} tarea${tasks.length === 1 ? "" : "s"} eliminada${tasks.length === 1 ? "" : "s"}`} actions={tasks.length ? actions : null}>
      <TaskList
        tasks={tasks}
        selectedId={selectedId}
        onSelect={onSelect}
        onRestore={onRestore}
        onPermanentDelete={onPermanentDelete}
        selectionMode={selectionMode}
        selectedIds={selectedTrashIds}
        onToggleSelection={onToggleSelection}
        showDragHandle={false}
        emptyLabel="Papelera vacía. Las tareas eliminadas aparecerán acá antes de borrarlas definitivamente."
        exitIntentById={exitIntentById}
      />
    </TaskSection>
  );
}

function TaskSection({
  title,
  meta,
  actions,
  children,
}: {
  title: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="task-section">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {meta ? <p>{meta}</p> : null}
        </div>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

function TaskList({
  tasks,
  selectedId,
  onSelect,
  onComplete,
  onDelete,
  onRestore,
  onPermanentDelete,
  deleteLabel = "Borrar",
  selectionMode = false,
  selectedIds = [],
  onToggleSelection,
  showDragHandle = true,
  emptyLabel = "Sin tareas",
  exitIntentById = {},
  weekendMode,
  incompleteFieldsByTaskId,
  trelloLinkInfoForTask,
}: {
  tasks: Task[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onComplete?: (task: Task) => void;
  onDelete?: (task: Task) => void;
  onRestore?: (task: Task) => void;
  onPermanentDelete?: (task: Task) => void;
  deleteLabel?: string;
  selectionMode?: boolean;
  selectedIds?: string[];
  onToggleSelection?: (id: string) => void;
  showDragHandle?: boolean;
  emptyLabel?: string;
  exitIntentById?: Record<string, "restore" | "delete">;
  weekendMode?: WeekendModeContext;
  incompleteFieldsByTaskId?: Map<string, number>;
  trelloLinkInfoForTask?: (task: Task) => TrelloTaskLinkInfo;
}) {
  if (tasks.length === 0) {
    return <div className="empty">{emptyLabel}</div>;
  }

  const selectedSet = new Set(selectedIds);

  return (
    <div className="task-list">
      {tasks.map((task) => (
        <SortableTaskItem
          key={task.id}
          task={task}
          selected={task.id === selectedId}
          onSelect={onSelect}
          onComplete={onComplete}
          onDelete={onDelete}
          onRestore={onRestore}
          onPermanentDelete={onPermanentDelete}
          deleteLabel={deleteLabel}
          selectionMode={selectionMode}
          selectionChecked={selectedSet.has(task.id)}
          onToggleSelection={onToggleSelection}
          showDragHandle={showDragHandle}
          exitIntent={exitIntentById[task.id] ?? null}
          weekendMode={weekendMode}
          incompleteFieldCount={incompleteFieldsByTaskId?.get(task.id)}
          trelloLinkInfo={trelloLinkInfoForTask?.(task) ?? { status: "not_applicable" }}
        />
      ))}
    </div>
  );
}

function SortableTaskItem({
  task,
  selected,
  onSelect,
  onComplete,
  onDelete,
  onRestore,
  onPermanentDelete,
  deleteLabel,
  selectionMode,
  selectionChecked,
  onToggleSelection,
  showDragHandle,
  exitIntent,
  weekendMode,
  incompleteFieldCount,
  trelloLinkInfo,
}: {
  task: Task;
  selected: boolean;
  onSelect: (id: string) => void;
  onComplete?: (task: Task) => void;
  onDelete?: (task: Task) => void;
  onRestore?: (task: Task) => void;
  onPermanentDelete?: (task: Task) => void;
  deleteLabel: string;
  selectionMode: boolean;
  selectionChecked: boolean;
  onToggleSelection?: (id: string) => void;
  showDragHandle: boolean;
  exitIntent: "restore" | "delete" | null;
  weekendMode?: WeekendModeContext;
  incompleteFieldCount?: number;
  trelloLinkInfo: TrelloTaskLinkInfo;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: task.id });
  const isTrello = isTrelloLinkedTask(task);
  const executable = task.task_kind !== "attention";
  const weekendScopeState = weekendTaskScopeState(task, weekendMode);
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <article
      ref={setNodeRef}
      style={style}
      className={[
        "task-row",
        `status-${task.status}`,
        selected ? "selected" : "",
        selectionMode ? "selectable" : "",
        isDragging ? "dragging" : "",
        exitIntent ? `exiting exit-${exitIntent}` : "",
        weekendScopeState === "active" ? "task-row--weekend-active" : "",
        weekendScopeState === "muted" ? "task-row--weekend-muted" : "",
      ].join(" ")}
      onClick={() => onSelect(task.id)}
      data-testid="task-card"
      data-task-title={task.title}
    >
      {selectionMode ? (
        <label className="select-check" onClick={(event) => event.stopPropagation()}>
          <input
            type="checkbox"
            checked={selectionChecked}
            onChange={() => onToggleSelection?.(task.id)}
            aria-label={`Seleccionar ${task.title}`}
          />
        </label>
      ) : showDragHandle ? (
        <button
          className="drag-handle"
          type="button"
          title="Mover"
          aria-label="Mover tarea"
          {...attributes}
          {...listeners}
        >
          <GripVertical size={17} />
        </button>
      ) : (
        <span className="status-dot muted">
          <Trash2 size={15} />
        </span>
      )}
      {onComplete && executable ? (
        <button
          className="icon-button check"
          type="button"
          title={isTrello ? "Acción Trello" : "Completar"}
          aria-label={isTrello ? "Elegir acción Trello" : "Completar tarea"}
          data-testid="task-complete-checkbox"
          onClick={(event) => {
            event.stopPropagation();
            onComplete(task);
          }}
        >
          <Circle size={18} />
        </button>
      ) : (
        <span className="status-dot">
          <Check size={15} />
        </span>
      )}
      <div className="task-main">
        <div className="task-title-line">
          {task.priority_label === "high" ? <span className="priority">🔥</span> : null}
          {task.task_kind === "attention" ? <span className="attention-mark">!</span> : null}
          <span className="task-title">{task.title}</span>
        </div>
        <div className="task-meta">
          <span
            className={[
              "scope-badge",
              weekendScopeState === "active" ? "scope-badge--weekend-active" : "",
              weekendScopeState === "muted" ? "scope-badge--weekend-muted" : "",
            ].join(" ")}
          >
            {task.scope}
          </span>
          {weekendScopeState === "muted" ? <span className="weekend-scope-note">Fuera de fin de semana</span> : null}
          {task.due_at ? <span>{formatDue(task.due_at)}</span> : null}
          {task.completed_at ? <span>Completada {formatDateTime(task.completed_at)}</span> : null}
          {task.deleted_at ? <span>Eliminada {formatDateTime(task.deleted_at)}</span> : null}
          {task.task_kind === "perpetual" ? <span>Perpetua</span> : null}
          {isTrello ? <span className="trello-hint">{task.trello_state ?? "Trello"}</span> : null}
          {incompleteFieldCount ? <span className="incomplete-details-badge">Faltan {incompleteFieldCount} datos</span> : null}
          {trelloLinkInfo.status === "linkable" ? (
            <span
              className="trello-link-pending-badge"
              title={`Esta tarea pertenece a ${task.scope}, pero todavía no tiene tarjeta Trello asociada.`}
            >
              Sin card Trello
            </span>
          ) : null}
        </div>
      </div>
      <div className="row-actions">
        {onRestore ? (
          <button
            className="icon-button"
            type="button"
            title="Restaurar"
            aria-label="Restaurar tarea"
            onClick={(event) => {
              event.stopPropagation();
              onRestore(task);
            }}
          >
            <ArchiveRestore size={17} />
          </button>
        ) : null}
        {onDelete && (executable || task.status === "completed") ? (
          <button
            className="icon-button danger"
            type="button"
            title={deleteLabel}
            aria-label={deleteLabel === "Borrar" ? "Borrar tarea" : deleteLabel}
            onClick={(event) => {
              event.stopPropagation();
              onDelete(task);
            }}
          >
            <Trash2 size={17} />
          </button>
        ) : null}
        {onPermanentDelete ? (
          <button
            className="icon-button danger"
            type="button"
            title="Eliminar definitivamente"
            aria-label="Eliminar definitivamente"
            onClick={(event) => {
              event.stopPropagation();
              onPermanentDelete(task);
            }}
          >
            <Trash2 size={17} />
          </button>
        ) : null}
        <button className="icon-button" type="button" title="Detalle" aria-label="Ver detalle">
          <PanelRight size={17} />
        </button>
      </div>
    </article>
  );
}

function InboxTriageModal({
  tasks,
  availableScopes,
  configuredTrelloBoards,
  linkingTrelloTaskId,
  busy,
  onClose,
  onSaveTask,
  onSuggest,
  onApplySuggestions,
  onComplete,
  onDiscard,
  onCreateTrelloCard,
}: {
  tasks: Task[];
  availableScopes: string[];
  configuredTrelloBoards: TrelloBoardSummary[];
  linkingTrelloTaskId: string | null;
  busy: boolean;
  onClose: () => void;
  onSaveTask: (task: Task, patch: Parameters<typeof patchTask>[1]) => Promise<Task>;
  onSuggest: (task: Task) => Promise<TaskSuggestionResponse>;
  onApplySuggestions: (task: Task, response: TaskSuggestionResponse, fields: string[]) => Promise<Task>;
  onComplete: (task: Task) => void;
  onDiscard: (task: Task) => void;
  onCreateTrelloCard: (task: Task) => void;
}) {
  const orderedTasks = useMemo(
    () => [...tasks].filter((task) => task.status === "active" && task.scope === "Inbox").sort((left, right) => left.created_at.localeCompare(right.created_at)),
    [tasks],
  );
  const [doneIds, setDoneIds] = useState<Set<string>>(() => new Set());
  const [skippedIds, setSkippedIds] = useState<Set<string>>(() => new Set());
  const [stats, setStats] = useState({ moved: 0, completed: 0, discarded: 0, skipped: 0 });
  const queue = orderedTasks.filter((task) => !doneIds.has(task.id) && !skippedIds.has(task.id));
  const currentTask = queue[0] ?? null;
  const [draft, setDraft] = useState(() => (currentTask ? draftFromTask(currentTask) : draftFromTask(emptyInboxDraftTask())));
  const [suggestion, setSuggestion] = useState<TaskSuggestionResponse | null>(null);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(() => new Set());
  const [status, setStatus] = useState<"idle" | "saving" | "suggesting" | "applying" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!currentTask) return;
    setDraft(draftFromTask(currentTask));
    setSuggestion(null);
    setSelectedFields(new Set());
    setStatus("idle");
    setError(null);
  }, [currentTask?.id]);

  const draftTask = currentTask ? ({ ...currentTask, scope: draft.scope } as Task) : null;
  const linkInfo = draftTask ? trelloLinkInfo(draftTask, configuredTrelloBoards) : { status: "not_applicable" as const };
  const canSaveNext = Boolean(currentTask && draft.scope && draft.scope !== "Inbox" && status !== "saving");
  const fields = suggestion ? applicableSuggestionFieldsForResponse(suggestion) : [];

  function markDone(taskId: string) {
    setDoneIds((current) => new Set(current).add(taskId));
  }

  function toggleSuggestionField(field: string) {
    setSelectedFields((current) => {
      const next = new Set(current);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  }

  async function suggest() {
    if (!currentTask) return;
    setStatus("suggesting");
    setError(null);
    try {
      const response = await onSuggest(currentTask);
      setSuggestion(response);
      setSelectedFields(new Set(safeDefaultFields(response)));
      setStatus("idle");
    } catch (nextError) {
      setError(readError(nextError));
      setStatus("error");
    }
  }

  async function applySelectedSuggestions() {
    if (!currentTask || !suggestion || !selectedFields.size) return;
    setStatus("applying");
    setError(null);
    try {
      const updated = await onApplySuggestions(currentTask, suggestion, Array.from(selectedFields));
      setDraft(draftFromTask(updated));
      setSuggestion(null);
      setSelectedFields(new Set());
      setStatus("idle");
    } catch (nextError) {
      setError(readError(nextError));
      setStatus("error");
    }
  }

  async function saveAndNext() {
    if (!currentTask || !canSaveNext) return;
    setStatus("saving");
    setError(null);
    try {
      await onSaveTask(currentTask, patchFromDraft(draft));
      markDone(currentTask.id);
      setStats((current) => ({ ...current, moved: current.moved + 1 }));
      setStatus("idle");
    } catch (nextError) {
      setError(readError(nextError));
      setStatus("error");
    }
  }

  function skip() {
    if (!currentTask) return;
    setSkippedIds((current) => new Set(current).add(currentTask.id));
    setStats((current) => ({ ...current, skipped: current.skipped + 1 }));
  }

  function completeCurrent() {
    if (!currentTask) return;
    onComplete(currentTask);
    markDone(currentTask.id);
    setStats((current) => ({ ...current, completed: current.completed + 1 }));
  }

  function discardCurrent() {
    if (!currentTask || !window.confirm("Descartar mueve la tarea a Papelera. ¿Continuar?")) return;
    onDiscard(currentTask);
    markDone(currentTask.id);
    setStats((current) => ({ ...current, discarded: current.discarded + 1 }));
  }

  async function createCard() {
    if (!currentTask || linkInfo.status !== "linkable") return;
    setStatus("saving");
    setError(null);
    try {
      const updated = draft.scope === currentTask.scope ? currentTask : await onSaveTask(currentTask, patchFromDraft(draft));
      onCreateTrelloCard(updated);
      markDone(currentTask.id);
      setStats((current) => ({ ...current, moved: current.moved + 1 }));
      setStatus("idle");
    } catch (nextError) {
      setError(readError(nextError));
      setStatus("error");
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="inbox-triage-modal" role="dialog" aria-modal="true" aria-labelledby="inbox-triage-title" data-testid="inbox-triage-panel">
        <div className="modal-header">
          <div>
            <p className="eyebrow">Inbox</p>
            <h2 id="inbox-triage-title">Procesar Inbox</h2>
            <small>{currentTask ? `${doneIds.size + 1} de ${orderedTasks.length}` : "Inbox procesado"}</small>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Cerrar">
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {!currentTask ? (
          <div className="inbox-triage-done">
            <h3>Inbox procesado</h3>
            <p>
              {stats.moved} movida{stats.moved === 1 ? "" : "s"} · {stats.completed} completada{stats.completed === 1 ? "" : "s"} · {stats.discarded} descartada{stats.discarded === 1 ? "" : "s"} · {stats.skipped} omitida{stats.skipped === 1 ? "" : "s"}
            </p>
            <button type="button" onClick={onClose} data-testid="inbox-triage-close-done">
              Cerrar
            </button>
          </div>
        ) : (
          <>
            {error ? <div className="notice danger">{error}</div> : null}
            <section className="inbox-triage-card">
              <div className="inbox-triage-current">
                <span>Tarea actual</span>
                <strong>{currentTask.title}</strong>
              </div>
              <div className="inbox-triage-title-row">
                <label>
                  Título
                  <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} data-testid="inbox-triage-title-input" />
                </label>
              </div>
              <div className="score-grid two">
                <label>
                  Mover a scope
                  <select value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} data-testid="inbox-triage-scope-select">
                    {availableScopes.map((scope) => (
                      <option key={scope} value={scope}>
                        {scope}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Prioridad
                  <select value={draft.priority_label} onChange={(event) => setDraft({ ...draft, priority_label: event.target.value })}>
                    {priorities.map((priority) => (
                      <option key={priority.value} value={priority.value}>
                        {priority.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="score-grid two">
                <label>
                  Esfuerzo
                  <select value={draft.effort_bucket} onChange={(event) => setDraft({ ...draft, effort_bucket: event.target.value })}>
                    <option value="">Sin estimar</option>
                    <option value="quick">Rápida</option>
                    <option value="medium">Media</option>
                    <option value="deep">Profunda</option>
                  </select>
                </label>
                <label>
                  Minutos
                  <input type="number" min="1" value={draft.estimated_minutes} onChange={(event) => setDraft({ ...draft, estimated_minutes: event.target.value })} />
                </label>
                <label>
                  Contexto
                  <select value={draft.context_bucket} onChange={(event) => setDraft({ ...draft, context_bucket: event.target.value })}>
                    {contexts.map((context) => (
                      <option key={context.value} value={context.value}>
                        {context.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Fecha
                  <input type="datetime-local" value={draft.due_at} onChange={(event) => setDraft({ ...draft, due_at: event.target.value })} />
                </label>
              </div>
              <label>
                Notas
                <textarea value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} placeholder="Sin notas" rows={4} data-testid="inbox-triage-notes" />
              </label>
            </section>

            <section className="inbox-triage-card">
              <div className="inbox-triage-card-header">
                <div>
                  <p className="eyebrow">Sugerencias</p>
                  <h3>Detalles rápidos</h3>
                </div>
                <button type="button" className="secondary-action" onClick={suggest} disabled={busy || status === "suggesting"}>
                  <Sparkles size={16} aria-hidden="true" />
                  {status === "suggesting" ? "Sugiriendo…" : "Sugerir detalles"}
                </button>
              </div>
              {suggestion ? (
                <div className="inbox-suggestion-list">
                  <small>
                    Fuente: {suggestionSourceLabel(suggestion)}. Revisá antes de aplicar. No se hará ningún cambio en Trello automáticamente.
                  </small>
                  {fields.length ? (
                    fields.map((field) => (
                      <label className="inbox-suggestion-row" key={field}>
                        <input type="checkbox" checked={selectedFields.has(field)} onChange={() => toggleSuggestionField(field)} />
                        <span>
                          <strong>{suggestionLabel(field)}</strong>
                          <small>{formatSuggestionValue(suggestion.suggestions[field]?.value)}</small>
                        </span>
                      </label>
                    ))
                  ) : (
                    <p className="muted-copy">No hay sugerencias aplicables para esta tarea.</p>
                  )}
                  <button type="button" onClick={applySelectedSuggestions} disabled={status === "applying" || !selectedFields.size} data-testid="inbox-triage-apply-suggestions">
                    {status === "applying" ? "Aplicando…" : "Aplicar sugerencias seleccionadas"}
                  </button>
                </div>
              ) : (
                <p className="muted-copy">Usá sugerencias cuando quieras completar scope, prioridad, esfuerzo o notas sin escribir todo desde cero.</p>
              )}
            </section>

            {linkInfo.status !== "not_applicable" ? (
              <section className={linkInfo.status === "linkable" ? "inbox-triage-trello" : "inbox-triage-trello warning"}>
                <div>
                  <p className="eyebrow">Trello</p>
                  <h3>{linkInfo.status === "linkable" ? "Sin card Trello" : "Falta mapear lista Pendiente"}</h3>
                  <p>
                    {linkInfo.status === "linkable"
                      ? `Si guardás en ${draft.scope}, seguirá siendo local hasta que crees la card explícitamente. Destino: ${linkInfo.board.name} · ${linkInfo.listName}.`
                      : `Configurá la lista Pendiente para ${linkInfo.board.alias} antes de crear cards desde este scope.`}
                  </p>
                </div>
                {linkInfo.status === "linkable" ? (
                  <button type="button" className="secondary-action" onClick={createCard} disabled={status === "saving" || linkingTrelloTaskId === currentTask.id}>
                    {linkingTrelloTaskId === currentTask.id ? "Proponiendo…" : "Proponer card Trello"}
                  </button>
                ) : null}
              </section>
            ) : null}

            <div className="inbox-triage-actions">
              <button type="button" className="secondary-action subtle" onClick={skip} data-testid="inbox-triage-skip">
                Omitir por ahora
              </button>
              <button type="button" className="secondary-action subtle" onClick={completeCurrent}>
                Completar
              </button>
              <button type="button" className="secondary-action danger" onClick={discardCurrent}>
                Descartar
              </button>
              <button type="button" onClick={saveAndNext} disabled={!canSaveNext} data-testid="inbox-triage-save-next">
                {status === "saving" ? "Guardando…" : "Guardar y siguiente"}
              </button>
            </div>
            {draft.scope === "Inbox" ? <p className="detail-inline-hint">Elegí un scope distinto de Inbox para procesar esta tarea.</p> : null}
          </>
        )}
      </div>
    </div>
  );
}

function emptyInboxDraftTask(): Task {
  const now = new Date().toISOString();
  return {
    id: "empty",
    title: "",
    status: "active",
    task_kind: "normal",
    source_type: "local",
    source_id: null,
    source_url: null,
    scope: "Inbox",
    origin_label: null,
    manual_order: 0,
    trello_board_name: null,
    trello_list_name: null,
    trello_board_id: null,
    trello_list_id: null,
    trello_state: null,
    checklist_done: null,
    checklist_total: null,
    priority_label: null,
    impact_score: null,
    urgency_score: null,
    blocking_score: null,
    effort_bucket: null,
    estimated_minutes: null,
    context_bucket: null,
    due_at: null,
    snoozed_until: null,
    last_trello_activity_at: null,
    next_checkin_at: null,
    metadata_json: null,
    created_at: now,
    updated_at: now,
    completed_at: null,
    deleted_at: null,
  };
}

function applicableSuggestionFieldsForResponse(response: TaskSuggestionResponse) {
  return Object.keys(response.suggestions).filter(isApplicableSuggestion);
}

function DetailPanel({
  task,
  priorityExplanation,
  availableScopes,
  trelloLinkInfo,
  linkingTrelloTaskId,
  onClose,
  onSave,
  onSaveError,
  onTrelloMove,
  onTrelloRename,
  onTrelloDue,
  onComplete,
  onLocalComplete,
  onCreateTrelloCard,
  onSuggestDetails,
  suggestingDetails,
  onOpenTrelloSettings,
  onRestore,
  onDelete,
}: {
  task: Task | null;
  priorityExplanation: PriorityExplanation | null;
  availableScopes: string[];
  trelloLinkInfo: TrelloTaskLinkInfo;
  linkingTrelloTaskId: string | null;
  onClose: () => void;
  onSave: (task: Task, patch: Parameters<typeof patchTask>[1], reason: "auto" | "manual") => Promise<void>;
  onSaveError: (message: string) => void;
  onTrelloMove: (task: Task, targetState: string) => void;
  onTrelloRename: (task: Task, title: string) => void;
  onTrelloDue: (task: Task, dueAt: string | null) => void;
  onComplete: (task: Task) => void;
  onLocalComplete: (task: Task) => void;
  onCreateTrelloCard: (task: Task) => void;
  onSuggestDetails: (task: Task) => void;
  suggestingDetails: boolean;
  onOpenTrelloSettings: () => void;
  onRestore: (task: Task) => void;
  onDelete: (task: Task) => void;
}) {
  const [draft, setDraft] = useState({
    title: "",
    scope: "Inbox",
    priority_label: "",
    due_at: "",
    notes: "",
    impact_score: "",
    urgency_score: "",
    blocking_score: "",
    effort_bucket: "",
    estimated_minutes: "",
    context_bucket: "",
  });
  const [baseline, setBaseline] = useState(draft);
  const [saveStatus, setSaveStatus] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const saveSeqRef = useRef(0);
  const latestTaskRef = useRef<Task | null>(task);
  const latestDraftRef = useRef(draft);
  const latestBaselineRef = useRef(baseline);
  const notesTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!task) return;
    const nextDraft = draftFromTask(task);
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setSaveStatus("saved");
  }, [task?.id]);

  const dirty = useMemo(() => !sameDraft(draft, baseline), [baseline, draft]);
  const taskDraftKey = task ? JSON.stringify(draftFromTask(task)) : "";

  useEffect(() => {
    if (!task || saveStatus === "saving" || saveStatus === "error") return;
    const serverDraft = draftFromTask(task);
    setDraft((currentDraft) => {
      const merged = mergeDraftWithServer(currentDraft, baseline, serverDraft);
      setSaveStatus(sameDraft(merged, serverDraft) ? "saved" : "dirty");
      return merged;
    });
    setBaseline(serverDraft);
  }, [saveStatus, taskDraftKey]);

  useEffect(() => {
    latestTaskRef.current = task;
    latestDraftRef.current = draft;
    latestBaselineRef.current = baseline;
  });

  useEffect(() => {
    if (saveStatus === "saving" || saveStatus === "error") return;
    setSaveStatus(dirty ? "dirty" : "saved");
  }, [dirty, saveStatus]);

  useEffect(() => {
    if (!task || !dirty || saveStatus === "saving") return undefined;
    const timer = window.setTimeout(() => {
      void saveCurrentDraft("auto");
    }, 850);
    return () => window.clearTimeout(timer);
  }, [dirty, draft, task?.id, saveStatus]);

  useEffect(() => {
    return () => {
      const previousTask = latestTaskRef.current;
      const previousDraft = latestDraftRef.current;
      const previousBaseline = latestBaselineRef.current;
      if (!previousTask || sameDraft(previousDraft, previousBaseline)) return;
      void onSave(previousTask, patchFromDraft(previousDraft), "auto").catch((nextError: unknown) => onSaveError(`Error al guardar task: ${readError(nextError)}`));
    };
  }, [task?.id]);

  if (!task) {
    return (
      <aside className="detail-panel empty-panel">
        <p>Seleccioná una tarea</p>
      </aside>
    );
  }

  const currentTask = task;
  const trelloLinked = isTrelloLinkedTask(currentTask);
  const canComplete = currentTask.status === "active";
  const canCreateTrelloCard = currentTask.status === "active" && trelloLinkInfo.status === "linkable";
  const originLabel = trelloLinked ? "Trello" : trelloLinkInfo.status === "linkable" ? "Local · scope Trello" : "Local";
  const statusLabel = taskStatusLabel(currentTask.status);

  async function saveCurrentDraft(reason: "auto" | "manual") {
    return saveDraftSnapshot(draft, reason);
  }

  async function saveDraftSnapshot(snapshot: ReturnType<typeof draftFromTask>, reason: "auto" | "manual", force = false) {
    if (!currentTask || (!force && sameDraft(snapshot, baseline))) return true;
    if (saveStatus === "saving") return false;
    const seq = saveSeqRef.current + 1;
    saveSeqRef.current = seq;
    setSaveStatus("saving");
    try {
      await onSave(currentTask, patchFromDraft(snapshot), reason);
      if (saveSeqRef.current === seq) {
        setBaseline(snapshot);
        setSaveStatus("saved");
      }
      return true;
    } catch (nextError) {
      if (saveSeqRef.current === seq) setSaveStatus("error");
      onSaveError(`Error al guardar task: ${readError(nextError)}`);
      return false;
    }
  }

  function saveNotesManually() {
    const snapshot = { ...draft, notes: notesTextareaRef.current?.value ?? draft.notes };
    setDraft(snapshot);
    void saveDraftSnapshot(snapshot, "manual", true);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void saveCurrentDraft("manual");
  }

  function restoreDraft() {
    setDraft(baseline);
    setSaveStatus("saved");
  }

  function restoreNotes() {
    setDraft({ ...draft, notes: baseline.notes });
  }

  function handleTitleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void saveCurrentDraft("manual");
    }
    if (event.key === "Escape") {
      event.preventDefault();
      restoreDraft();
      event.currentTarget.blur();
    }
  }

  return (
    <aside className="detail-panel" data-testid="task-detail-panel">
      <div className="detail-header">
        <div className="detail-heading">
          <p className="eyebrow">Detalle</p>
          <h2>{currentTask.title}</h2>
          <div className="detail-meta-chips" aria-label="Resumen de tarea">
            <span>{currentTask.scope}</span>
            <span>{statusLabel}</span>
            <span>{originLabel}</span>
            {canCreateTrelloCard ? <span className="trello-link-pending-badge">Sin card Trello</span> : null}
          </div>
          <span className={`save-status ${saveStatus}`}>{saveStatusLabel(saveStatus)}</span>
        </div>
        <button className="icon-button" type="button" title="Cerrar" aria-label="Cerrar detalle" onClick={onClose}>
          <X size={18} />
        </button>
      </div>

      <form className="detail-form" onSubmit={submit}>
        <div className="detail-primary-actions" aria-label="Acciones principales">
          {canComplete ? (
            <button
              type="button"
              className="secondary-action"
              onClick={() => {
                void (async () => {
                  const saved = await saveCurrentDraft("manual");
                  if (saved) onComplete(currentTask);
                })();
              }}
              disabled={saveStatus === "saving"}
              data-testid="task-complete-button"
            >
              <CheckCircle2 size={16} aria-hidden="true" />
              Completar
            </button>
          ) : null}
          <button
            type="button"
            className="secondary-action"
            onClick={() => onSuggestDetails(currentTask)}
            disabled={saveStatus === "saving" || suggestingDetails}
            data-testid="task-suggest-details-button"
          >
            <Sparkles size={16} aria-hidden="true" />
            {suggestingDetails ? "Analizando tarea…" : "Sugerir detalles"}
          </button>
          {canCreateTrelloCard ? (
            <button
              type="button"
              className="secondary-action"
              disabled={linkingTrelloTaskId === currentTask.id || saveStatus === "saving"}
              onClick={() => {
                void (async () => {
                  const saved = await saveCurrentDraft("manual");
                  if (saved) onCreateTrelloCard(currentTask);
                })();
              }}
              data-testid="task-create-trello-card-button"
            >
              <Plus size={16} aria-hidden="true" />
              {linkingTrelloTaskId === currentTask.id ? "Proponiendo…" : "Proponer card Trello"}
            </button>
          ) : null}
        </div>
        {!trelloLinked && trelloLinkInfo.status === "linkable" ? (
          <p className="detail-inline-hint">Esta tarea no está vinculada a Trello. Si la completás, se marca sólo localmente.</p>
        ) : null}
        <PriorityExplanationCard task={currentTask} explanation={priorityExplanation} />
        {currentTask.status === "completed" ? (
          <div className="detail-actions" aria-label="Acciones de tarea completada">
            <button type="button" className="secondary-action" onClick={() => onRestore(currentTask)}>
              <ArchiveRestore size={16} aria-hidden="true" />
              Restaurar tarea
            </button>
            <button type="button" className="secondary-action danger" onClick={() => onDelete(currentTask)}>
              <Trash2 size={16} aria-hidden="true" />
              Mover a Papelera
            </button>
          </div>
        ) : null}
        <label>
          Título
          <input
            value={draft.title}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            onKeyDown={handleTitleKeyDown}
            data-testid="task-title-input"
          />
        </label>
        <label>
          Scope
          <select value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })}>
            {availableScopes.map((scope) => (
              <option key={scope} value={scope}>
                {scope}
              </option>
            ))}
          </select>
        </label>
        <label>
          Deadline
          <input
            type="datetime-local"
            value={draft.due_at}
            onChange={(event) => setDraft({ ...draft, due_at: event.target.value })}
          />
        </label>
        <label>
          Prioridad
          <select
            value={draft.priority_label}
            onChange={(event) => setDraft({ ...draft, priority_label: event.target.value })}
            data-testid="task-metadata-priority"
          >
            {priorities.map((priority) => (
              <option key={priority.value} value={priority.value}>
                {priority.label}
              </option>
            ))}
          </select>
        </label>
        <div className="score-grid">
          <label>
            Impacto
            <input
              type="number"
              min="1"
              max="5"
              value={draft.impact_score}
              onChange={(event) => setDraft({ ...draft, impact_score: event.target.value })}
            />
          </label>
          <label>
            Urgencia
            <input
              type="number"
              min="1"
              max="5"
              value={draft.urgency_score}
              onChange={(event) => setDraft({ ...draft, urgency_score: event.target.value })}
            />
          </label>
          <label>
            Bloqueo
            <input
              type="number"
              min="1"
              max="5"
              value={draft.blocking_score}
              onChange={(event) => setDraft({ ...draft, blocking_score: event.target.value })}
            />
          </label>
        </div>
        <div className="score-grid two">
          <label>
            Esfuerzo
            <select
              value={draft.effort_bucket}
              onChange={(event) => setDraft({ ...draft, effort_bucket: event.target.value })}
              data-testid="task-metadata-effort"
            >
              <option value="">Sin estimar</option>
              <option value="quick">Rápida</option>
              <option value="medium">Media</option>
              <option value="deep">Profunda</option>
            </select>
          </label>
          <label>
            Contexto
            <select
              value={draft.context_bucket}
              onChange={(event) => setDraft({ ...draft, context_bucket: event.target.value })}
            >
              {contexts.map((context) => (
                <option key={context.value} value={context.value}>
                  {context.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Minutos
            <input
              type="number"
              min="1"
              value={draft.estimated_minutes}
              onChange={(event) => setDraft({ ...draft, estimated_minutes: event.target.value })}
              data-testid="task-metadata-minutes"
            />
          </label>
        </div>
        <section className="detail-notes-section" aria-label="Notas">
          <div className="detail-section-header">
            <div>
              <p className="eyebrow">Notas</p>
              <h3>{draft.notes.trim() ? "Descripción local" : "Sin notas"}</h3>
            </div>
            <div className="detail-inline-actions">
              <button
                type="button"
                className="secondary-action subtle"
                onClick={restoreNotes}
                disabled={draft.notes === baseline.notes || saveStatus === "saving"}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="secondary-action"
                onClick={saveNotesManually}
                disabled={!dirty || saveStatus === "saving"}
                data-testid="task-notes-save"
              >
                Guardar notas
              </button>
            </div>
          </div>
          <textarea
            ref={notesTextareaRef}
            value={draft.notes}
            onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
            placeholder="Sin notas"
            rows={5}
            data-testid="task-notes-textarea"
          />
        </section>
        {trelloLinked ? (
          <div className="trello-detail">
            <div className="trello-detail-row">
              <span>Origen</span>
              <strong>Trello</strong>
            </div>
            <div className="trello-detail-row">
              <span>Board</span>
              <strong>{currentTask.trello_board_name}</strong>
            </div>
            <div className="trello-detail-row">
              <span>Lista</span>
              <strong>{currentTask.trello_list_name}</strong>
            </div>
            <div className="trello-detail-row">
              <span>Estado interno</span>
              <strong>{currentTask.trello_state}</strong>
            </div>
            <div className="trello-detail-row">
              <span>Prioridad Trello</span>
              <strong>{currentTask.priority_label ?? "sin prioridad"}</strong>
            </div>
            {currentTask.checklist_total ? (
              <div className="trello-detail-row">
                <span>Checklist</span>
                <strong>
                  {currentTask.checklist_done ?? 0}/{currentTask.checklist_total}
                </strong>
              </div>
            ) : null}
            {currentTask.last_trello_activity_at ? (
              <div className="trello-detail-row">
                <span>Última actividad</span>
                <strong>{formatDateTime(currentTask.last_trello_activity_at)}</strong>
              </div>
            ) : null}
            {currentTask.source_url ? (
              <a className="trello-link" href={currentTask.source_url} target="_blank" rel="noreferrer">
                <ExternalLink size={15} />
                Abrir en Trello
              </a>
            ) : null}
            <div className="trello-actions">
              <button type="button" onClick={() => onTrelloMove(currentTask, "in_progress")}>
                Mover a EN PROCESO
              </button>
              <button type="button" onClick={() => onTrelloMove(currentTask, "review")}>
                Mover a EN REVISION
              </button>
              <button type="button" onClick={() => onTrelloMove(currentTask, "completed")}>
                Mover a TERMINADAS
              </button>
              <button type="button" onClick={() => onTrelloRename(currentTask, draft.title)}>
                Renombrar en Trello
              </button>
              <button type="button" onClick={() => onTrelloDue(currentTask, draft.due_at ? new Date(draft.due_at).toISOString() : null)}>
                Actualizar deadline en Trello
              </button>
              <button type="button" onClick={() => onLocalComplete(currentTask)}>
                Sólo marcar localmente
              </button>
            </div>
          </div>
        ) : null}
        {trelloLinkInfo.status !== "not_applicable" ? (
          <div className={trelloLinkInfo.status === "linkable" ? "trello-link-card" : "trello-link-card warning"}>
            <div>
              <p className="eyebrow">Trello</p>
              <h3>{trelloLinkInfo.status === "linkable" ? "Sin tarjeta asociada" : "Trello no está listo para este scope"}</h3>
              {trelloLinkInfo.status === "linkable" ? (
                <p>
                  Esta tarea pertenece a {currentTask.scope}, pero todavía no tiene tarjeta Trello. Board: {trelloLinkInfo.board.name} · Lista: {trelloLinkInfo.listName}
                </p>
              ) : (
                <p>Configurá la lista de Tareas para {trelloLinkInfo.board.alias} en Configuración → Trello.</p>
              )}
            </div>
            {trelloLinkInfo.status === "missing_mapping" ? (
              <button type="button" className="secondary-action subtle" onClick={onOpenTrelloSettings}>
                Ir a Configuración Trello
              </button>
            ) : null}
          </div>
        ) : null}
        <button className="save-button" type="submit" disabled={!dirty || saveStatus === "saving"}>
          {saveStatus === "saving" ? "Guardando..." : "Guardar"}
        </button>
      </form>
    </aside>
  );
}

function draftFromTask(task: Task) {
  return {
      title: task.title,
      scope: task.scope,
      priority_label: task.priority_label ?? "",
      due_at: task.due_at ? task.due_at.slice(0, 16) : "",
      notes: taskNotes(task),
      impact_score: task.impact_score?.toString() ?? "",
      urgency_score: task.urgency_score?.toString() ?? "",
      blocking_score: task.blocking_score?.toString() ?? "",
      effort_bucket: task.effort_bucket ?? "",
      estimated_minutes: task.estimated_minutes?.toString() ?? "",
      context_bucket: task.context_bucket ?? "",
    };
}

function patchFromDraft(draft: ReturnType<typeof draftFromTask>): Parameters<typeof patchTask>[1] {
  return {
    title: draft.title,
    scope: draft.scope,
    priority_label: (draft.priority_label || null) as Task["priority_label"],
    due_at: draft.due_at ? new Date(draft.due_at).toISOString() : null,
    notes: draft.notes,
    impact_score: draft.impact_score ? Number(draft.impact_score) : null,
    urgency_score: draft.urgency_score ? Number(draft.urgency_score) : null,
    blocking_score: draft.blocking_score ? Number(draft.blocking_score) : null,
    effort_bucket: (draft.effort_bucket || null) as Task["effort_bucket"],
    estimated_minutes: draft.estimated_minutes ? Number(draft.estimated_minutes) : null,
    context_bucket: (draft.context_bucket || null) as Task["context_bucket"],
  };
}

function sameDraft(left: ReturnType<typeof draftFromTask>, right: ReturnType<typeof draftFromTask>) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function mergeDraftWithServer(
  draft: ReturnType<typeof draftFromTask>,
  baseline: ReturnType<typeof draftFromTask>,
  server: ReturnType<typeof draftFromTask>,
) {
  return Object.fromEntries(
    Object.keys(server).map((field) => {
      const key = field as keyof typeof server;
      return [key, draft[key] !== baseline[key] ? draft[key] : server[key]];
    }),
  ) as ReturnType<typeof draftFromTask>;
}

function suggestionApplySummary(applied: number, remaining: number) {
  const appliedLabel = `${applied} ${applied === 1 ? "campo aplicado" : "campos aplicados"}`;
  const remainingLabel = `${remaining} ${remaining === 1 ? "campo pendiente" : "campos pendientes"}`;
  return `${appliedLabel} · ${remainingLabel}`;
}

function suggestionSkipReason(reason: string) {
  if (reason === "already_set") return "ya tenía ese valor";
  if (reason === "no_overwrite") return "cambió antes de aplicar";
  if (reason === "invalid_value") return "valor inválido";
  if (reason === "missing_suggestion") return "sugerencia ausente";
  return "campo no aplicable";
}

function taskNotes(task: Task) {
  const metadata = parseTaskMetadata(task.metadata_json);
  return typeof metadata.notes === "string" ? metadata.notes : "";
}

function taskStatusLabel(status: Task["status"]) {
  if (status === "completed") return "Completada";
  if (status === "deleted") return "Papelera";
  if (status === "archived") return "Archivada";
  return "Activa";
}

function saveStatusLabel(status: "saved" | "dirty" | "saving" | "error") {
  if (status === "saving") return "Guardando...";
  if (status === "dirty") return "Cambios sin guardar";
  if (status === "error") return "Error al guardar";
  return "Guardado";
}

function suggestionLabel(field: string) {
  return (
    {
      normalized_title: "Título",
      scope: "Scope",
      priority_label: "Prioridad",
      impact_score: "Impacto",
      urgency_score: "Urgencia",
      blocking_score: "Bloqueo",
      effort_bucket: "Esfuerzo",
      estimated_minutes: "Minutos",
      context_bucket: "Contexto",
      due_at: "Deadline",
      notes: "Notas",
      trello_card_recommendation: "Trello",
    }[field] ?? field
  );
}

const applicableSuggestionFields = new Set([
  "normalized_title",
  "scope",
  "priority_label",
  "impact_score",
  "urgency_score",
  "blocking_score",
  "effort_bucket",
  "estimated_minutes",
  "context_bucket",
  "due_at",
  "notes",
]);

function isApplicableSuggestion(field: string) {
  return applicableSuggestionFields.has(field);
}

function suggestionSourceLabel(response: TaskSuggestionResponse) {
  if (response.source === "openai") return "OpenAI";
  return "Fallback heurístico";
}

function currentSuggestionValue(task: Task, field: string) {
  if (field === "normalized_title") return task.title;
  if (field === "notes") {
    const metadata = parseTaskMetadata(task.metadata_json);
    return metadata.notes ? String(metadata.notes) : "vacío";
  }
  const value = (task as unknown as Record<string, unknown>)[field];
  return formatSuggestionValue(value);
}

function formatSuggestionValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "vacío";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function parseTaskMetadata(raw: string | null) {
  if (!raw) return {} as Record<string, unknown>;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

type BulkSuggestionPhase = "preparing" | "ready" | "generating" | "completed" | "partial_success" | "applying" | "done" | "error";
type BulkCandidate = IncompleteTaskCandidate;

function useElapsedSeconds(startedAt: number | null, active: boolean) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (startedAt === null) {
      setElapsedSeconds(0);
      return undefined;
    }
    const update = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    update();
    if (!active) return undefined;
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);

  return elapsedSeconds;
}

function AISuggestionProgress({
  state,
  title,
  description,
}: {
  state: AISuggestionProgressState;
  title: string;
  description: string;
}) {
  const active = state.phase === "preparing" || state.phase === "generating";
  const elapsedSeconds = useElapsedSeconds(state.startedAt, active);
  const statusText =
    state.phase === "preparing"
      ? "Preparando tareas"
      : state.phase === "generating"
        ? state.total > 1
          ? `Analizando ${state.total} tareas`
          : "Analizando la tarea"
        : state.phase === "partial_success"
          ? `${state.succeeded} ${state.succeeded === 1 ? "completada" : "completadas"}, ${state.failed} con error`
          : state.phase === "completed"
            ? state.total > 1
              ? `${state.succeeded} sugerencias generadas`
              : "Sugerencias generadas"
            : "No se pudieron generar las sugerencias";
  const progressValue = state.isDeterminate && state.total > 0 ? Math.round((state.completed / state.total) * 100) : null;

  return (
    <section className={`ai-progress-panel ${state.phase}`} aria-live="polite" aria-atomic="true" data-testid="ai-suggestion-progress">
      <div className="ai-progress-heading">
        {active ? <LoaderCircle className="ai-progress-spinner" size={22} aria-hidden="true" /> : null}
        <div>
          <h3>{title}</h3>
          <p id="ai-progress-description">{statusText}</p>
        </div>
      </div>
      <div
        className={`ai-progress-track ${progressValue === null ? (active ? "indeterminate" : "static") : "determinate"}`}
        role="progressbar"
        aria-label={title}
        aria-valuemin={progressValue === null ? undefined : 0}
        aria-valuemax={progressValue === null ? undefined : 100}
        aria-valuenow={progressValue ?? undefined}
        aria-valuetext={
          progressValue === null
            ? active
              ? "Generando sugerencias"
              : statusText
            : `${state.completed} de ${state.total} tareas completadas`
        }
        data-testid="ai-suggestion-progressbar"
      >
        <span style={progressValue === null ? undefined : { width: `${progressValue}%` }} />
      </div>
      <div className="ai-progress-meta">
        <span>{description}</span>
        {active ? <span>Esto puede tardar algunos minutos.</span> : null}
        {elapsedSeconds >= 2 ? <span className="ai-progress-elapsed">Transcurrido: {elapsedSeconds} s</span> : null}
      </div>
      {state.currentTaskTitle ? <strong className="ai-progress-task">Tarea: {state.currentTaskTitle}</strong> : null}
      {state.phase === "partial_success" ? (
        <div className="notice danger" role="alert">
          {state.failed} {state.failed === 1 ? "tarea tuvo" : "tareas tuvieron"} error. Podés revisar y aplicar las sugerencias disponibles.
        </div>
      ) : null}
      {state.phase === "error" ? (
        <div className="notice danger" role="alert">
          {state.error ?? "No se pudieron generar las sugerencias. Intentá nuevamente."}
        </div>
      ) : null}
    </section>
  );
}

function AISuggestionProgressModal({
  state,
  title,
  description,
  onClose,
  onRetry,
}: {
  state: AISuggestionProgressState;
  title: string;
  description: string;
  onClose: () => void;
  onRetry?: () => void;
}) {
  const active = state.phase === "preparing" || state.phase === "generating";
  const dialogRef = useRef<HTMLDivElement>(null);
  const safeClose = useCallback(() => {
    if (!active) onClose();
  }, [active, onClose]);
  useDialogFocusTrap(dialogRef, true, safeClose);
  return (
    <div className="modal-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="suggestion-modal ai-progress-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="individual-suggestion-progress-title"
        aria-describedby="ai-progress-description"
        aria-busy={active}
        data-testid="individual-suggestion-progress-modal"
      >
        <div className="modal-header">
          <div>
            <p className="eyebrow">Sugerencias con IA</p>
            <h2 id="individual-suggestion-progress-title">{title}</h2>
          </div>
          <button className="icon-button" type="button" onClick={safeClose} aria-label="Cerrar" disabled={active}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <AISuggestionProgress state={state} title="Generando sugerencias" description={description} />
        {active ? <small className="ai-progress-wait">Esperá a que termine el análisis.</small> : null}
        {state.phase === "error" ? (
          <div className="trello-modal-actions">
            {onRetry ? (
              <button type="button" onClick={onRetry}>
                Reintentar
              </button>
            ) : null}
            <button type="button" className="secondary-action subtle" onClick={onClose}>
              Cerrar
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function mergePreferredBulkCandidates(preferredTasks: Task[], fetchedCandidates: BulkCandidate[]) {
  const merged = new Map<string, BulkCandidate>();
  for (const candidate of fetchedCandidates) {
    merged.set(candidate.id, candidate);
  }
  const preferredIds = new Set(preferredTasks.filter((item) => item.status === "active").map((task) => task.id));
  return Array.from(merged.values())
    .sort((left, right) => Number(preferredIds.has(right.id)) - Number(preferredIds.has(left.id)))
    .slice(0, 25);
}

type ManualDetailDraft = Record<string, string>;

function manualDraftFromCandidate(candidate: IncompleteTaskCandidate): ManualDetailDraft {
  return Object.fromEntries(
    candidate.missing_fields.map((field) => {
      const value = candidate.current[field];
      return [field, value === null || value === undefined ? "" : String(value)];
    }),
  );
}

function manualPatchFromDraft(candidate: IncompleteTaskCandidate, draft: ManualDetailDraft) {
  const patch: Record<string, unknown> = {};
  for (const field of candidate.missing_fields) {
    const value = draft[field]?.trim();
    if (!value) continue;
    if (["impact_score", "urgency_score", "blocking_score", "estimated_minutes"].includes(field)) {
      patch[field] = Number(value);
    } else {
      patch[field] = value;
    }
  }
  return patch as Parameters<typeof completeTaskDetails>[3];
}

function useDialogFocusTrap(
  dialogRef: React.RefObject<HTMLElement>,
  active: boolean,
  onClose: () => void,
  initialFocusSelector?: string,
  focusKey?: string | null,
) {
  useEffect(() => {
    if (!active) return undefined;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusableSelector =
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';
    const firstFocusable =
      (initialFocusSelector ? dialog.querySelector<HTMLElement>(initialFocusSelector) : null)
      ?? dialog.querySelector<HTMLElement>(focusableSelector);
    firstFocusable?.focus();

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog!.querySelectorAll<HTMLElement>(focusableSelector));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    dialog.addEventListener("keydown", handleKeyDown);
    return () => {
      dialog.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [active, dialogRef, focusKey, initialFocusSelector, onClose]);
}

function ManualDetailField({
  field,
  value,
  onChange,
}: {
  field: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const label = suggestionLabel(field);
  if (field === "priority_label") {
    return (
      <label>
        {label}
        <select data-manual-field value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Seleccionar…</option>
          {priorities.filter((option) => option.value).map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
    );
  }
  if (field === "effort_bucket") {
    return (
      <label>
        {label}
        <select data-manual-field value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Seleccionar…</option>
          <option value="quick">Rápida</option>
          <option value="medium">Media</option>
          <option value="deep">Profunda</option>
        </select>
      </label>
    );
  }
  if (field === "context_bucket") {
    return (
      <label>
        {label}
        <select data-manual-field value={value} onChange={(event) => onChange(event.target.value)}>
          {contexts.map((context) => (
            <option key={context.value} value={context.value}>{context.label}</option>
          ))}
        </select>
      </label>
    );
  }
  if (field === "notes") {
    return (
      <label className="manual-detail-notes">
        {label}
        <textarea data-manual-field value={value} onChange={(event) => onChange(event.target.value)} rows={5} placeholder="Agregá contexto útil para retomar esta tarea." />
      </label>
    );
  }
  const scoreField = ["impact_score", "urgency_score", "blocking_score"].includes(field);
  return (
    <label>
      {label}
      <input
        data-manual-field
        type="number"
        min={1}
        max={scoreField ? 5 : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={scoreField ? "1–5" : "Minutos"}
      />
    </label>
  );
}

function ManualDetailQueueModal({
  initialDetails,
  tasks,
  openAIAvailable,
  onClose,
  onTaskUpdated,
}: {
  initialDetails: IncompleteTaskDetails;
  tasks: Task[];
  openAIAvailable: boolean;
  onClose: () => void;
  onTaskUpdated: (task: Task, remainingMissingFields: string[], isCandidate: boolean) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [candidates, setCandidates] = useState(initialDetails.tasks);
  const [doneIds, setDoneIds] = useState<Set<string>>(() => new Set());
  const [skippedIds, setSkippedIds] = useState<Set<string>>(() => new Set());
  const [draft, setDraft] = useState<ManualDetailDraft>({});
  const [saving, setSaving] = useState(false);
  const [savedCount, setSavedCount] = useState(0);
  const [sessionTotal, setSessionTotal] = useState(initialDetails.tasks.length);
  const [conflict, setConflict] = useState<string | null>(null);
  const [conflictingFields, setConflictingFields] = useState<string[]>([]);
  const [conflictAcknowledged, setConflictAcknowledged] = useState(false);
  const [conflictCanRetry, setConflictCanRetry] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [suggestionProgress, setSuggestionProgress] = useState<AISuggestionProgressState | null>(null);
  const [suggestionReview, setSuggestionReview] = useState<{ task: Task; response: TaskSuggestionResponse } | null>(null);
  const savingRef = useRef(false);
  savingRef.current = saving;
  const safeClose = useCallback(() => {
    if (!savingRef.current) onClose();
  }, [onClose]);
  const suggestionOverlayOpen = suggestionProgress !== null || suggestionReview !== null;
  const current = candidates.find((candidate) => !doneIds.has(candidate.id) && !skippedIds.has(candidate.id)) ?? null;
  useDialogFocusTrap(dialogRef, !suggestionOverlayOpen, safeClose, "[data-manual-field]", current?.id);
  const finished = current === null;
  const position = Math.min(doneIds.size + skippedIds.size + 1, sessionTotal);
  const patch = current ? manualPatchFromDraft(current, draft) : {};
  const hasValues = Object.keys(patch).length > 0;

  useEffect(() => {
    if (!current) return;
    setDraft(manualDraftFromCandidate(current));
    setConflict(null);
    setConflictingFields([]);
    setConflictAcknowledged(false);
    setConflictCanRetry(true);
    setError(null);
  }, [current?.id]);

  async function refreshCandidate(taskId: string, preserveDraftFields = false) {
    const refreshed = await fetchIncompleteTaskDetails(1, "", taskId);
    const nextCandidate = refreshed.tasks.find((candidate) => candidate.id === taskId) ?? null;
    setCandidates((existing) => {
      const previous = existing.find((candidate) => candidate.id === taskId);
      const replacement =
        nextCandidate && previous && preserveDraftFields
          ? {
              ...nextCandidate,
              missing_fields: previous.missing_fields,
              missing_count: previous.missing_fields.length,
            }
          : nextCandidate;
      if (!replacement && preserveDraftFields) return existing;
      if (!replacement || (!replacement.candidate && !preserveDraftFields)) {
        return existing.filter((candidate) => candidate.id !== taskId);
      }
      return existing.map((candidate) => candidate.id === taskId ? replacement : candidate);
    });
    return nextCandidate;
  }

  async function saveAndContinue(event: FormEvent) {
    event.preventDefault();
    if (!current || !hasValues) return;
    setSaving(true);
    setConflict(null);
    setError(null);
    try {
      const result = await completeTaskDetails(current.id, current.updated_at, current.version_token, patch);
      onTaskUpdated(result.task, result.remaining_missing_fields, result.is_candidate);
      await refreshCandidate(current.id);
      setDoneIds((ids) => new Set(ids).add(current.id));
      setSavedCount((count) => count + 1);
    } catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 409) {
        const refreshed = await refreshCandidate(current.id, true);
        const externallyCompleted = refreshed
          ? current.missing_fields.filter((field) => !refreshed.missing_fields.includes(field))
          : [];
        setConflictingFields(externallyCompleted);
        setConflictAcknowledged(false);
        setConflictCanRetry(Boolean(refreshed && refreshed.status === "active"));
        setConflict(
          refreshed
            ? "La tarea cambió mientras la editabas. Conservé lo que escribiste; revisá los valores del servidor y elegí explícitamente si querés usar tu borrador."
            : "La tarea dejó de estar activa. Conservé tu borrador para que puedas revisarlo, pero ya no se puede guardar desde esta cola.",
        );
      } else {
        setError(readError(nextError));
      }
    } finally {
      setSaving(false);
    }
  }

  function skipCurrent() {
    if (!current) return;
    setSkippedIds((ids) => new Set(ids).add(current.id));
  }

  function reviewSkipped() {
    const skipped = new Set(skippedIds);
    setCandidates((items) => items.filter((candidate) => skipped.has(candidate.id)));
    setDoneIds(new Set());
    setSkippedIds(new Set());
    setSessionTotal(skipped.size);
  }

  async function suggestCurrentTask() {
    if (!current || !openAIAvailable) return;
    const task = tasks.find((item) => item.id === current.id);
    if (!task) {
      setError("No pude cargar la versión actual de la tarea.");
      return;
    }
    setSuggestionProgress({
      phase: "generating",
      total: 1,
      completed: 0,
      succeeded: 0,
      failed: 0,
      currentTaskTitle: task.title,
      startedAt: Date.now(),
      error: null,
      isDeterminate: false,
    });
    try {
      const response = await suggestTaskDetails(task.id);
      setSuggestionProgress(null);
      if (!Object.keys(response.suggestions).length) {
        setError("Esta tarea ya tiene suficientes detalles para generar propuestas.");
        return;
      }
      setSuggestionReview({ task, response });
    } catch (nextError) {
      setSuggestionProgress((progress) =>
        progress
          ? { ...progress, phase: "error", completed: 1, failed: 1, error: readError(nextError), isDeterminate: true }
          : null,
      );
    }
  }

  async function applyCurrentSuggestions(fields: string[]) {
    if (!suggestionReview) return;
    setSaving(true);
    try {
      const suggestions = Object.fromEntries(
        Object.entries(suggestionReview.response.suggestions).map(([field, item]) => [field, item.value]),
      );
      const result = await applyTaskSuggestions(
        suggestionReview.task.id,
        suggestions,
        fields,
        false,
        suggestionReview.response.source,
      );
      onTaskUpdated(result.task, result.remaining_missing_fields, result.is_candidate);
      await refreshCandidate(suggestionReview.task.id);
      setDoneIds((ids) => new Set(ids).add(suggestionReview.task.id));
      setSavedCount((count) => count + 1);
      setSuggestionReview(null);
    } catch (nextError) {
      setError(readError(nextError));
      setSuggestionReview(null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="modal-backdrop" role="presentation">
        <div
          ref={dialogRef}
          className="suggestion-modal manual-detail-modal"
          role="dialog"
          aria-modal={!suggestionOverlayOpen}
          aria-hidden={suggestionOverlayOpen || undefined}
          aria-labelledby="manual-detail-title"
          aria-describedby="manual-detail-description"
          aria-busy={saving}
          data-testid="manual-detail-queue"
        >
        <div className="modal-header">
          <div>
            <p className="eyebrow">Completar detalles</p>
            <h2 id="manual-detail-title">{finished ? "Resumen de la sesión" : `Tarea ${position} de ${sessionTotal}`}</h2>
            <small id="manual-detail-description">Los cambios se guardan sólo al avanzar. No se modifica Trello.</small>
          </div>
          <button className="icon-button" type="button" onClick={safeClose} aria-label="Cerrar" disabled={saving}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {current ? (
          <form className="manual-detail-form" onSubmit={saveAndContinue}>
            <div className="manual-detail-task-summary">
              <strong>{current.title}</strong>
              <div className="detail-meta-chips">
                <span>{current.scope}</span>
                <span>{current.source_type === "trello" ? "Trello" : "Local"}</span>
                <span>{current.missing_fields.length} pendientes</span>
              </div>
            </div>
            {conflict ? (
              <div className="notice warning manual-detail-conflict" role="alert" tabIndex={-1}>
                <span>{conflict}</span>
                {conflictingFields.length ? (
                  <ul>
                    {conflictingFields.map((field) => (
                      <li key={field}>
                        {suggestionLabel(field)} en servidor: {formatSuggestionValue(current.current[field])}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {conflictCanRetry ? (
                  <button type="button" className="secondary-action" onClick={() => setConflictAcknowledged(true)}>
                    Usar mi borrador de todos modos
                  </button>
                ) : null}
              </div>
            ) : null}
            {error ? <div className="notice danger" role="alert">{error}</div> : null}
            <div className="manual-detail-fields">
              {current.missing_fields.map((field) => (
                <ManualDetailField
                  key={field}
                  field={field}
                  value={draft[field] ?? ""}
                  onChange={(value) => setDraft((values) => ({ ...values, [field]: value }))}
                />
              ))}
            </div>
            {openAIAvailable ? (
              <button
                type="button"
                className="secondary-action manual-detail-suggest"
                onClick={() => void suggestCurrentTask()}
                disabled={saving}
              >
                <Sparkles size={16} aria-hidden="true" />
                Sugerir esta tarea
              </button>
            ) : null}
            <div className="manual-detail-actions">
              <button type="button" className="secondary-action subtle" onClick={safeClose} disabled={saving}>Cerrar</button>
              <button type="button" className="secondary-action" onClick={skipCurrent} disabled={saving}>Omitir por ahora</button>
              <button
                type="submit"
                disabled={saving || !hasValues || Boolean(conflict && (!conflictAcknowledged || !conflictCanRetry))}
                data-testid="manual-details-save-next"
              >
                {saving ? "Guardando…" : "Guardar y siguiente"}
              </button>
            </div>
          </form>
        ) : (
          <div className="manual-detail-summary" aria-live="polite">
            <CheckCircle2 size={30} aria-hidden="true" />
            <h3>Recorrido terminado</h3>
            <p>{savedCount} tarea(s) guardada(s) y {skippedIds.size} omitida(s) por ahora.</p>
            <p>
              {candidates.filter((candidate) => doneIds.has(candidate.id) && candidate.candidate).length
                ? `${candidates.filter((candidate) => doneIds.has(candidate.id) && candidate.candidate).length} todavía incompleta(s).`
                : "No quedan tareas candidatas pendientes en este recorrido."}
            </p>
            <div className="manual-detail-actions">
              {skippedIds.size ? <button type="button" className="secondary-action" onClick={reviewSkipped}>Revisar omitidas</button> : null}
              <button type="button" className="secondary-action" onClick={onClose}>Volver a la lista</button>
              <button type="button" onClick={onClose}>Cerrar</button>
            </div>
          </div>
        )}
        </div>
      </div>
      {suggestionProgress ? (
        <AISuggestionProgressModal
          state={suggestionProgress}
          title="Sugerir esta tarea"
          description="Analizando los detalles pendientes sin aplicar cambios."
          onClose={() => setSuggestionProgress(null)}
          onRetry={suggestionProgress.phase === "error" ? () => void suggestCurrentTask() : undefined}
        />
      ) : null}
      {suggestionReview ? (
        <TaskSuggestionModal
          task={suggestionReview.task}
          response={suggestionReview.response}
          busy={saving}
          onApply={(fields) => void applyCurrentSuggestions(fields)}
          onCancel={() => setSuggestionReview(null)}
        />
      ) : null}
    </>
  );
}

function BulkTaskSuggestionModal({
  busy,
  preferredTasks,
  searchQuery,
  onClose,
  onManual,
  onShowList,
  onApplied,
  onGenerated,
}: {
  busy: boolean;
  preferredTasks: Task[];
  searchQuery: string;
  onClose: () => void;
  onManual: (details: IncompleteTaskDetails, openAIAvailable: boolean) => void;
  onShowList: (details: IncompleteTaskDetails) => void;
  onApplied: (tasks: Task[]) => void;
  onGenerated: (failed: number) => void;
}) {
  const [phase, setPhase] = useState<BulkSuggestionPhase>("preparing");
  const [candidates, setCandidates] = useState<BulkCandidate[]>([]);
  const [totalCandidates, setTotalCandidates] = useState(0);
  const [bulkResponse, setBulkResponse] = useState<BulkTaskSuggestionResponse | null>(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(() => new Set());
  const [selectedFields, setSelectedFields] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [applyErrors, setApplyErrors] = useState<Array<{ task_id: string; error: string }>>([]);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [openAIAvailable, setOpenAIAvailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPhase("preparing");
    setStartedAt(Date.now());
    void fetchLLMStatus()
      .then((status) => {
        if (!cancelled) setOpenAIAvailable(status.safe_to_use);
      })
      .catch(() => {
        if (!cancelled) setOpenAIAvailable(false);
      });
    fetchIncompleteTaskDetails(25, searchQuery)
      .then((response) => {
        if (cancelled) return;
        const preferred = searchQuery.trim() ? preferredTasks.filter((task) => task.title.toLocaleLowerCase().includes(searchQuery.trim().toLocaleLowerCase())) : preferredTasks;
        setCandidates(mergePreferredBulkCandidates(preferred, response.tasks));
        setTotalCandidates(response.total_candidates);
        setPhase("ready");
      })
      .catch((nextError) => {
        if (cancelled) return;
        setError(readError(nextError));
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [preferredTasks, searchQuery]);

  const reviewResults = bulkResponse?.results.filter((item) => item.ok && item.suggestion) ?? [];
  const failedResults = bulkResponse?.results.filter((item) => !item.ok) ?? [];

  function titleFor(taskId: string) {
    return candidates.find((candidate) => candidate.id === taskId)?.title ?? taskId;
  }

  function key(taskId: string, field: string) {
    return `${taskId}:${field}`;
  }

  async function generate() {
    setPhase("generating");
    setStartedAt(Date.now());
    setError(null);
    setApplyErrors([]);
    try {
      const taskIds = candidates.slice(0, 10).map((candidate) => candidate.id);
      const response = await suggestTaskDetailsBulk(taskIds, 10);
      setBulkResponse(response);
      const nextTaskIds = new Set<string>();
      const nextFields = new Set<string>();
      for (const result of response.results) {
        if (!result.ok || !result.suggestion) continue;
        const fields = safeDefaultFields(result.suggestion);
        if (!fields.length) continue;
        nextTaskIds.add(result.task_id);
        fields.forEach((field) => nextFields.add(key(result.task_id, field)));
      }
      setSelectedTaskIds(nextTaskIds);
      setSelectedFields(nextFields);
      const failed = response.results.filter((item) => !item.ok).length;
      const succeeded = response.results.length - failed;
      if (!succeeded) {
        setError("No se pudieron generar las sugerencias. Intentá nuevamente.");
        setPhase("error");
        return;
      }
      setPhase(failed ? "partial_success" : "completed");
      onGenerated(failed);
    } catch (nextError) {
      setError(readError(nextError));
      setPhase("error");
    }
  }

  function toggleTask(taskId: string) {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  function toggleField(taskId: string, field: string) {
    setSelectedFields((current) => {
      const next = new Set(current);
      const fieldKey = key(taskId, field);
      if (next.has(fieldKey)) next.delete(fieldKey);
      else next.add(fieldKey);
      return next;
    });
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      next.add(taskId);
      return next;
    });
  }

  function selectSafe() {
    const nextTaskIds = new Set<string>();
    const nextFields = new Set<string>();
    for (const result of reviewResults) {
      const fields = safeDefaultFields(result.suggestion!);
      if (!fields.length) continue;
      nextTaskIds.add(result.task_id);
      fields.forEach((field) => nextFields.add(key(result.task_id, field)));
    }
    setSelectedTaskIds(nextTaskIds);
    setSelectedFields(nextFields);
  }

  function selectAllTasks() {
    const nextTaskIds = new Set<string>();
    const nextFields = new Set<string>();
    for (const result of reviewResults) {
      const fields = applicableBulkFields(result.suggestion!);
      if (!fields.length) continue;
      nextTaskIds.add(result.task_id);
      fields.forEach((field) => nextFields.add(key(result.task_id, field)));
    }
    setSelectedTaskIds(nextTaskIds);
    setSelectedFields(nextFields);
  }

  async function applySelected() {
    if (!bulkResponse) return;
    const items: BulkTaskSuggestionApplyItem[] = [];
    for (const result of reviewResults) {
      if (!selectedTaskIds.has(result.task_id) || !result.suggestion) continue;
      const fields = applicableBulkFields(result.suggestion).filter((field) => selectedFields.has(key(result.task_id, field)));
      if (!fields.length) continue;
      items.push({
        task_id: result.task_id,
        fields,
        suggestions: Object.fromEntries(fields.map((field) => [field, result.suggestion!.suggestions[field].value])),
        source: result.suggestion.source,
      });
    }
    if (!items.length) return;
    setPhase("applying");
    try {
      const response = await applyTaskSuggestionsBulk(items);
      const skippedErrors = response.results
        .filter((result) => result.skipped_fields.length)
        .map((result) => ({
          task_id: result.task.id,
          error: result.skipped_fields
            .map((skipped) => `${suggestionLabel(skipped.field)}: ${suggestionSkipReason(skipped.reason)}`)
            .join(", "),
        }));
      const nextApplyErrors = [...response.errors, ...skippedErrors];
      setApplyErrors(nextApplyErrors);
      const changedTaskIds = new Set(
        response.results.filter((result) => result.applied_fields.length).map((result) => result.task.id),
      );
      const changedTasks = response.tasks.filter((task) => changedTaskIds.has(task.id));
      if (changedTasks.length) onApplied(changedTasks);
      const resultsByTaskId = new Map(response.results.map((result) => [result.task.id, result]));
      setCandidates((current) =>
        current
          .filter((candidate) => resultsByTaskId.get(candidate.id)?.is_candidate ?? true)
          .map((candidate) => {
            const result = resultsByTaskId.get(candidate.id);
            return result ? { ...candidate, missing_fields: result.remaining_missing_fields } : candidate;
          }),
      );
      setBulkResponse((current) => {
        if (!current) return current;
        return {
          ...current,
          results: current.results
            .filter((item) => resultsByTaskId.get(item.task_id)?.is_candidate ?? true)
            .map((item) => {
              const applied = resultsByTaskId.get(item.task_id);
              if (!applied || !item.suggestion) return item;
              const appliedFields = new Set(applied.applied_fields);
              return {
                ...item,
                suggestion: {
                  ...item.suggestion,
                  suggestions: Object.fromEntries(
                    Object.entries(item.suggestion.suggestions).filter(([field]) => !appliedFields.has(field)),
                  ),
                  missing_fields: applied.remaining_missing_fields,
                },
              };
            }),
        };
      });
      setSelectedTaskIds((current) => new Set(Array.from(current).filter((taskId) => !changedTaskIds.has(taskId))));
      setSelectedFields((current) => {
        const next = new Set(current);
        response.results.forEach((result) => result.applied_fields.forEach((field) => next.delete(key(result.task.id, field))));
        return next;
      });
      setPhase(nextApplyErrors.length ? "partial_success" : "done");
    } catch (nextError) {
      setError(readError(nextError));
      setPhase("error");
    }
  }

  const generationTotal = Math.min(candidates.length, 10);
  const succeededCount = reviewResults.length;
  const failedCount = failedResults.length;
  const progressState: AISuggestionProgressState = {
    phase:
      phase === "preparing"
        ? "preparing"
        : phase === "generating"
          ? "generating"
          : phase === "partial_success"
            ? "partial_success"
            : phase === "error"
              ? "error"
              : "completed",
    total: generationTotal,
    completed: phase === "generating" || phase === "preparing" ? 0 : succeededCount + failedCount,
    succeeded: succeededCount,
    failed: failedCount,
    currentTaskTitle: null,
    startedAt,
    error,
    isDeterminate: phase !== "preparing" && phase !== "generating",
  };
  const isReviewPhase = phase === "completed" || phase === "partial_success" || phase === "applying";
  const isProcessing = phase === "preparing" || phase === "generating";
  const details = { tasks: candidates, total_candidates: totalCandidates, limit: 25 };

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="suggestion-modal bulk-suggestion-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-suggestion-title"
        aria-describedby="bulk-suggestion-description"
        aria-busy={isProcessing}
        data-testid="bulk-suggestions-modal"
      >
        <div className="modal-header">
          <div>
            <p className="eyebrow">Sugerencias en lote</p>
            <h2 id="bulk-suggestion-title">Sugerir detalles</h2>
            <small id="bulk-suggestion-description">Revisá antes de aplicar. No se hará ningún cambio en Trello.</small>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Cerrar" disabled={isProcessing}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {phase === "preparing" ? (
          <AISuggestionProgress state={progressState} title="Preparando sugerencias" description="Buscando tareas incompletas." />
        ) : null}
        {phase === "error" ? (
          <>
            <AISuggestionProgress state={progressState} title="Generando sugerencias" description="La revisión humana sigue siendo necesaria antes de aplicar cambios." />
            <div className="trello-modal-actions">
              {candidates.length ? (
                <button type="button" onClick={generate}>
                  Reintentar
                </button>
              ) : null}
              <button type="button" className="secondary-action subtle" onClick={onClose}>
                Cerrar
              </button>
            </div>
          </>
        ) : null}
        {phase === "ready" ? (
          <>
            <div className="notice">
              Encontré {totalCandidates} tareas activas con al menos dos detalles pendientes.
            </div>
            <div className="detail-completion-routes">
              <button
                type="button"
                className="detail-completion-route primary"
                onClick={() => onManual(details, openAIAvailable)}
                disabled={busy || !candidates.length}
                data-testid="manual-details-route"
              >
                <span>Completar manualmente</span>
                <small>Recorré sólo los campos pendientes, una tarea a la vez.</small>
              </button>
              <button
                type="button"
                className="detail-completion-route"
                onClick={generate}
                disabled={busy || !candidates.length || !openAIAvailable}
                data-testid="bulk-suggestions-generate"
              >
                <span>Generar con OpenAI</span>
                <small>{openAIAvailable ? "Generá propuestas para revisar antes de aplicar." : "Configurá y validá OpenAI para habilitar esta opción."}</small>
              </button>
              <button
                type="button"
                className="detail-completion-route"
                onClick={() => onShowList(details)}
                disabled={!candidates.length}
                data-testid="show-incomplete-list-route"
              >
                <span>Mostrar en la lista</span>
                <small>Aplicá un filtro temporal para revisar estas tareas en TODO.</small>
              </button>
            </div>
            <button type="button" className="secondary-action subtle" onClick={onClose}>
              Cerrar
            </button>
          </>
        ) : null}
        {phase === "generating" ? (
          <AISuggestionProgress
            state={progressState}
            title="Generando sugerencias"
            description={`Analizando ${generationTotal} ${generationTotal === 1 ? "tarea" : "tareas"} sin aplicar cambios.`}
          />
        ) : null}
        {phase === "applying" ? <div className="empty-state">Aplicando seleccionadas…</div> : null}
        {phase === "done" ? <div className="notice">Sugerencias aplicadas. No se ejecutó ningún write Trello.</div> : null}
        {isReviewPhase ? (
          <>
            {phase === "completed" || phase === "partial_success" ? (
              <AISuggestionProgress
                state={progressState}
                title={
                  phase === "partial_success"
                    ? applyErrors.length
                      ? "Aplicación parcial"
                      : "Sugerencias generadas parcialmente"
                    : "Sugerencias listas"
                }
                description={applyErrors.length ? "Revisá los campos que no se pudieron aplicar." : "Revisalas antes de aplicar."}
              />
            ) : null}
            {bulkResponse?.warnings.length ? <div className="notice">{bulkResponse.warnings.join(" ")}</div> : null}
            <div className="suggestion-bulk-summary">
              <span>{reviewResults.length} tarea(s) con sugerencias</span>
              <span>Fallback: {bulkResponse?.source_summary.heuristic ?? 0}</span>
              <span>OpenAI: {bulkResponse?.source_summary.openai ?? 0}</span>
            </div>
            {applyErrors.length ? (
              <div className="notice danger">
                {applyErrors.length} tarea(s) no se pudieron aplicar: {applyErrors.map((item) => `${titleFor(item.task_id)}: ${item.error}`).join(" · ")}
              </div>
            ) : null}
            <div className="suggestion-list bulk-suggestion-list">
              {reviewResults.map((result) => (
                <BulkSuggestionRow
                  key={result.task_id}
                  title={titleFor(result.task_id)}
                  result={{ task_id: result.task_id, suggestion: result.suggestion }}
                  selected={selectedTaskIds.has(result.task_id)}
                  selectedFields={selectedFields}
                  fieldKey={key}
                  onToggleTask={() => toggleTask(result.task_id)}
                  onToggleField={(field) => toggleField(result.task_id, field)}
                />
              ))}
            </div>
            <div className="trello-modal-actions">
              <button type="button" className="secondary-action subtle" onClick={selectSafe} disabled={phase === "applying"}>
                Seleccionar seguras
              </button>
              <button type="button" className="secondary-action subtle" onClick={selectAllTasks} disabled={phase === "applying"}>
                Seleccionar todas
              </button>
              <button type="button" onClick={applySelected} disabled={phase === "applying" || !selectedTaskIds.size} data-testid="bulk-suggestions-apply-selected">
                Aplicar seleccionadas
              </button>
              <button type="button" className="secondary-action subtle" onClick={onClose} disabled={phase === "applying"}>
                Descartar todas
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

function BulkSuggestionRow({
  title,
  result,
  selected,
  selectedFields,
  fieldKey,
  onToggleTask,
  onToggleField,
}: {
  title: string;
  result: { task_id: string; suggestion: TaskSuggestionResponse | null };
  selected: boolean;
  selectedFields: Set<string>;
  fieldKey: (taskId: string, field: string) => string;
  onToggleTask: () => void;
  onToggleField: (field: string) => void;
}) {
  const suggestion = result.suggestion!;
  const fields = Object.keys(suggestion.suggestions);
  const applicableFields = applicableBulkFields(suggestion);
  return (
    <div className="bulk-suggestion-row" data-testid="bulk-suggestion-row">
      <label className="bulk-suggestion-title">
        <input type="checkbox" checked={selected} onChange={onToggleTask} />
        <span>
          <strong>{title}</strong>
          <small>{suggestionSourceLabel(suggestion)}</small>
        </span>
      </label>
      <div className="bulk-suggestion-fields">
        {fields.map((field) => {
          const item = suggestion.suggestions[field];
          const applicable = applicableFields.includes(field);
          return (
            <label className="bulk-suggestion-field" key={field}>
              <input
                type="checkbox"
                checked={selectedFields.has(fieldKey(result.task_id, field))}
                disabled={!applicable}
                onChange={() => onToggleField(field)}
              />
              <span>
                <strong>{suggestionLabel(field)}: {formatSuggestionValue(item.value)}</strong>
                <small>{item.reason}</small>
              </span>
              <span className="confidence-pill">{applicable ? `${Math.round(item.confidence * 100)}%` : "Info"}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

function applicableBulkFields(response: TaskSuggestionResponse) {
  return Object.keys(response.suggestions).filter((field) => isApplicableSuggestion(field) && response.suggestions[field]?.value !== null);
}

function safeDefaultFields(response: TaskSuggestionResponse) {
  return applicableBulkFields(response).filter((field) => {
    const item = response.suggestions[field];
    if (!item?.applies_to_empty_field || item.confidence < 0.5) return false;
    if (field === "normalized_title" || field === "scope") return false;
    if (field === "due_at" && item.confidence < 0.7) return false;
    return true;
  });
}

function TaskSuggestionModal({
  task,
  response,
  busy,
  onApply,
  onCancel,
}: {
  task: Task;
  response: TaskSuggestionResponse;
  busy: boolean;
  onApply: (fields: string[]) => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const safeCancel = useCallback(() => {
    if (!busy) onCancel();
  }, [busy, onCancel]);
  useDialogFocusTrap(dialogRef, true, safeCancel);
  const fields = Object.keys(response.suggestions);
  const applicableFields = fields.filter((field) => isApplicableSuggestion(field) && response.suggestions[field]?.value !== null);
  const [selected, setSelected] = useState(
    () => new Set(applicableFields.filter((field) => response.suggestions[field]?.value !== null && response.suggestions[field]?.applies_to_empty_field !== false)),
  );
  const selectedFields = applicableFields.filter((field) => selected.has(field));

  function toggle(field: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div ref={dialogRef} className="suggestion-modal" role="dialog" aria-modal="true" aria-labelledby="suggestion-modal-title" data-testid="task-suggestions-modal">
        <div className="modal-header">
          <div>
            <p className="eyebrow">{suggestionSourceLabel(response)}</p>
            <h2 id="suggestion-modal-title">Sugerencias para “{task.title}”</h2>
            {response.missing_fields.length ? <small>Campos incompletos: {response.missing_fields.map(suggestionLabel).join(", ")}</small> : null}
          </div>
          <button className="icon-button" type="button" onClick={safeCancel} aria-label="Cerrar">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        {response.warnings.length ? <div className="notice">{response.warnings.join(" ")}</div> : null}
        <div className="suggestion-list">
          {fields.map((field) => {
            const suggestion = response.suggestions[field];
            const applicable = isApplicableSuggestion(field);
            return (
              <label className="suggestion-row" key={field} data-testid={`task-suggestion-row-${field}`}>
                <input type="checkbox" checked={selected.has(field)} onChange={() => toggle(field)} disabled={!applicable} />
                <span>
                  <strong>{suggestionLabel(field)}</strong>
                  <small>Actual: {currentSuggestionValue(task, field)}</small>
                </span>
                <span>
                  <strong>Sugerido: {formatSuggestionValue(suggestion.value)}</strong>
                  <small>{suggestion.reason}</small>
                </span>
                <span className="confidence-pill">{applicable ? `${Math.round(suggestion.confidence * 100)}%` : "Info"}</span>
              </label>
            );
          })}
        </div>
        <div className="trello-modal-actions">
          <button type="button" onClick={() => onApply(applicableFields)} disabled={busy || !applicableFields.length} data-testid="task-suggestions-apply-all">
            Aplicar todas
          </button>
          <button type="button" onClick={() => onApply(selectedFields)} disabled={busy || !selectedFields.length} data-testid="task-suggestions-apply-selected">
            Aplicar seleccionadas
          </button>
          <button type="button" onClick={safeCancel} disabled={busy}>
            Descartar
          </button>
        </div>
      </div>
    </div>
  );
}

function TrashConfirmModal({
  action,
  busy,
  onConfirm,
  onCancel,
}: {
  action: TrashConfirmAction;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const count = action.tasks.length;
  const hasTrello = action.tasks.some(isTrelloLinkedTask);
  const singleTask = action.type === "single-delete" ? action.tasks[0] : null;
  const title =
    action.type === "empty"
      ? "Vaciar Papelera"
      : action.type === "bulk-delete"
        ? `Eliminar definitivamente ${count} tareas`
        : "Eliminar definitivamente";
  const confirmLabel = action.type === "empty" ? "Vaciar Papelera" : "Eliminar definitivamente";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="trash-confirm-title">
      <div className="trello-modal danger-modal">
        <h2 id="trash-confirm-title">{title}</h2>
        {singleTask ? (
          <>
            <p>Esta tarea se eliminará de forma permanente. No vas a poder restaurarla después.</p>
            <p>
              Tarea: <strong>{singleTask.title}</strong>
            </p>
          </>
        ) : (
          <p>
            Se eliminarán definitivamente {count} tarea{count === 1 ? "" : "s"}. Esta acción no se puede deshacer.
          </p>
        )}
        {hasTrello ? <p>Las tareas Trello sólo se eliminan de la copia local. No se borra ni archiva ninguna card remota.</p> : null}
        <div className="trello-modal-actions">
          <button className="danger-action" type="button" onClick={onConfirm} disabled={busy || count === 0}>
            {confirmLabel}
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

function TrelloChoiceModal({
  task,
  onMoveReview,
  onMoveCompleted,
  onLocalOnly,
  onCancel,
}: {
  task: Task;
  onMoveReview: () => void;
  onMoveCompleted: () => void;
  onLocalOnly: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="trello-modal">
        <h2>Esta tarea viene de Trello</h2>
        <p>{task.title}</p>
        <div className="trello-modal-actions">
          <button type="button" onClick={onMoveReview}>
            Mover a EN REVISION
          </button>
          <button type="button" onClick={onMoveCompleted}>
            Mover a TERMINADAS
          </button>
          <button type="button" onClick={onLocalOnly}>
            Sólo marcar localmente
          </button>
          <button type="button" onClick={onCancel}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

function TrelloRestoreModal({
  task,
  onRestoreLocal,
  onCancel,
}: {
  task: Task;
  onRestoreLocal: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="trello-restore-title">
      <div className="trello-modal">
        <h2 id="trello-restore-title">Restaurar sólo en TaskD</h2>
        <p>
          Esta tarea viene de Trello. Restaurarla localmente no moverá la card ni cambiará su lista en Trello.
        </p>
        <p>{task.title}</p>
        <div className="trello-modal-actions">
          <button type="button" onClick={onRestoreLocal}>
            Restaurar sólo localmente
          </button>
          <button type="button" onClick={onCancel}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

function BackupRestorePlanModal({ plan, onClose }: { plan: BackupRestorePlan; onClose: () => void }) {
  const commandText = plan.manual_commands.join("\n");
  function copyCommands() {
    void navigator.clipboard?.writeText(commandText);
  }
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="backup-restore-plan-title">
      <div className="trello-modal backup-plan-modal">
        <div className="sort-modal-header">
          <div>
            <p className="eyebrow">Recuperación</p>
            <h2 id="backup-restore-plan-title">Plan de recuperación</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Cerrar">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="backup-plan-summary">
          <ReadOnlySetting label="Backup" value={formatDateTime(plan.backup.created_at)} />
          <ReadOnlySetting label="Tamaño" value={formatBytes(plan.backup.size_bytes)} />
          <ReadOnlySetting label="Estado" value={plan.backup.valid ? "Válido" : plan.backup.validation.reason || "Sin validar"} />
          <ReadOnlySetting label="DB actual" value={plan.current_db ? `${formatBytes(plan.current_db.size_bytes)} · ${plan.current_db.path_redacted}` : "No disponible"} />
        </div>
        <div className="settings-warning-box">
          <strong>Restauración automática no habilitada</strong>
          <p>{plan.reason ?? "Usá el plan manual seguro para evitar reemplazar la DB mientras la app está escribiendo."}</p>
        </div>
        <ol className="backup-plan-actions">
          {plan.actions.map((action) => (
            <li key={action}>{action}</li>
          ))}
        </ol>
        <div className="backup-command-box">
          <strong>Comandos manuales</strong>
          <pre>{commandText}</pre>
        </div>
        <div className="trello-modal-actions">
          <button type="button" onClick={copyCommands}>
            Copiar comandos
          </button>
          <button type="button" className="secondary-action subtle" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
}

function SortProposalModal({
  proposal,
  busy,
  onApply,
  onCancel,
}: {
  proposal: SortProposal;
  busy: boolean;
  onApply: () => void;
  onCancel: () => void;
}) {
  const items = sortProposalItems(proposal);
  const changedItems = items.filter((item) => item.old_index !== item.new_index);
  const canApply = items.length >= 2;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="sort-proposal-title" data-testid="priority-proposal-modal">
      <div className="sort-modal">
        <div className="sort-modal-header">
          <div>
            <p className="eyebrow">Prioridad local</p>
            <h2 id="sort-proposal-title">Propuesta de Prioridad</h2>
          </div>
          <button className="icon-button" type="button" title="Cerrar" aria-label="Cerrar propuesta" onClick={onCancel}>
            <X size={18} />
          </button>
        </div>

        <p className="sort-summary">
          {proposal.summary || "Revisá el orden propuesto antes de aplicarlo. No toca el orden remoto de Trello."}
        </p>

        {items.length ? (
          <ol className="sort-proposal-list">
            {items.map((item) => (
              <li key={item.task_id}>
                <div className="sort-rank">{item.new_index}</div>
                <div className="sort-item-main">
                  <strong>{item.title}</strong>
                  <span>
                    {item.scope} · {priorityBandLabel(item.priority?.priority_band)} · score {Math.round(item.score)}
                    {item.old_index !== item.new_index ? ` · #${item.old_index} → #${item.new_index}` : " · se queda"}
                  </span>
                  <p>{item.priority?.summary || item.reason}</p>
                  {item.priority?.factors?.length ? (
                    <div className="priority-factor-strip" aria-label="Factores principales">
                      {item.priority.factors.slice(0, 3).map((factor) => (
                        <span className={factor.direction === "down" ? "priority-factor down" : "priority-factor"} key={`${item.task_id}-${factor.criterion}`}>
                          {factor.label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <div className="empty">No hay suficientes tareas activas para reordenar.</div>
        )}

        {changedItems.length ? (
          <div className="sort-changes">
            <strong>Cambios</strong>
            <span>{changedItems.length} tareas cambian de posición.</span>
          </div>
        ) : null}

        <div className="trello-modal-actions">
          <button type="button" onClick={onApply} disabled={busy || !canApply}>
            Aplicar orden
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

function taskScore(task: Task) {
  const priorityScore = task.priority_label === "high" ? 50 : task.priority_label === "medium_high" ? 30 : 0;
  return priorityScore + (task.impact_score ?? 0) * 3 + (task.urgency_score ?? 0) * 3 + (task.blocking_score ?? 0) * 2;
}

function sortProposalItems(proposal: SortProposal | null): SortProposalItem[] {
  if (!proposal) return [];
  if (Array.isArray(proposal.items)) return proposal.items;
  return proposal.tasks.map((task, index) => ({
    task_id: task.id,
    old_index: task.manual_order || index + 1,
    new_index: index + 1,
    title: task.title,
    scope: task.scope,
    score: taskScore(task),
    reason: reasonForTask(task),
  }));
}

function PriorityExplanationCard({ task, explanation }: { task: Task; explanation: PriorityExplanation | null }) {
  const fallback = explanation ?? fallbackPriorityExplanation(task);
  const upFactors = fallback.factors.filter((factor) => factor.direction !== "down");
  const downFactors = fallback.factors.filter((factor) => factor.direction === "down");
  return (
    <section className="priority-explanation-card" aria-label="Por qué esta prioridad" data-testid="priority-explanation">
      <div className="priority-explanation-header">
        <div>
          <span>Por qué esta prioridad</span>
          <strong>{priorityBandLabel(fallback.priority_band)}</strong>
        </div>
        <small>Score {Math.round(fallback.score)}</small>
      </div>
      <p>{fallback.summary}</p>
      {upFactors.length ? (
        <div className="priority-factor-group">
          <strong>Sube por</strong>
          {upFactors.slice(0, 4).map((factor) => (
            <span key={factor.criterion}>{factor.reason}</span>
          ))}
        </div>
      ) : null}
      {downFactors.length ? (
        <div className="priority-factor-group">
          <strong>Baja por</strong>
          {downFactors.slice(0, 3).map((factor) => (
            <span key={factor.criterion}>{factor.reason}</span>
          ))}
        </div>
      ) : null}
      {fallback.warnings.length ? (
        <div className="priority-factor-group muted">
          <strong>Faltan datos</strong>
          {fallback.warnings.slice(0, 3).map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function fallbackPriorityExplanation(task: Task): PriorityExplanation {
  const factors = [
    task.due_at
      ? { criterion: "due_date", label: "Fecha visible", contribution: 18, direction: "up" as const, reason: "Tiene una fecha visible." }
      : null,
    task.priority_label
      ? { criterion: "manual_priority", label: priorityLabel(task.priority_label), contribution: 14, direction: "up" as const, reason: `Prioridad ${priorityLabel(task.priority_label).toLowerCase()}.` }
      : null,
    task.impact_score
      ? { criterion: "impact", label: "Impacto", contribution: task.impact_score * 3, direction: "up" as const, reason: `Impacto ${task.impact_score}/5.` }
      : null,
    task.urgency_score
      ? { criterion: "urgency", label: "Urgencia", contribution: task.urgency_score * 3, direction: "up" as const, reason: `Urgencia ${task.urgency_score}/5.` }
      : null,
    task.blocking_score
      ? { criterion: "blocking", label: "Bloqueo", contribution: task.blocking_score * 2, direction: "up" as const, reason: `Bloqueo ${task.blocking_score}/5.` }
      : null,
    task.effort_bucket === "deep"
      ? { criterion: "effort", label: "Esfuerzo profundo", contribution: -4, direction: "down" as const, reason: "Requiere un bloque largo de trabajo." }
      : task.effort_bucket === "quick"
        ? { criterion: "effort", label: "Quick win", contribution: 8, direction: "up" as const, reason: "Parece corta y cerrable." }
        : null,
  ].filter(Boolean) as PriorityExplanation["factors"];
  const score = factors.reduce((total, factor) => total + factor.contribution, 0);
  const summary = factors.slice(0, 3).map((factor) => factor.label).join(", ") || "prioridad calculada localmente";
  const warnings = [
    task.impact_score == null ? "Sin impacto definido." : null,
    task.urgency_score == null ? "Sin urgencia definida." : null,
    task.effort_bucket == null && task.estimated_minutes == null ? "Sin estimación de esfuerzo." : null,
  ].filter(Boolean) as string[];
  return {
    task_id: task.id,
    score,
    priority_band: score >= 40 ? "high" : score >= 24 ? "medium_high" : score >= 10 ? "medium" : "low",
    factors,
    summary,
    warnings,
  };
}

function priorityBandLabel(value?: string) {
  return (
    {
      high: "Prioridad alta",
      medium_high: "Prioridad media alta",
      medium: "Prioridad media",
      low: "Prioridad baja",
    }[value || ""] ?? "Prioridad local"
  );
}

function contextLabel(value?: string | null) {
  return contexts.find((context) => context.value === value)?.label ?? String(value ?? "").replace("_", " ");
}

function priorityLabel(value: Task["priority_label"]) {
  return priorities.find((priority) => priority.value === value)?.label ?? "prioridad";
}

function reasonForTask(task: Task) {
  if (task.due_at) return "tiene deadline visible y conviene atenderla";
  if (task.priority_label === "high") return "prioridad alta";
  if (task.effort_bucket === "deep") return "requiere foco y puede tener impacto alto";
  if (task.effort_bucket === "quick") return "parece corta y cerrable";
  return "prioridad calculada localmente";
}

function formatDue(value: string) {
  return new Intl.DateTimeFormat("es", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("es", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBulkConfirmationSummary(result: ConfirmationBulkResponse, action: "confirm" | "cancel") {
  const done = action === "confirm" ? result.confirmed : result.cancelled;
  const noun = action === "confirm" ? "acción(es)" : "confirmación(es)";
  const doneLabel = action === "confirm" ? "confirmada(s)" : "cancelada(s)";
  if (result.failed === 0) return `${done} ${noun} ${doneLabel}.`;
  if (done === 0) return `No se pudo procesar ninguna confirmación. ${result.failed} fallaron.`;
  return `${done} ${noun} ${doneLabel}; ${result.failed} fallaron.`;
}

function confirmationDetail(confirmation: Confirmation) {
  if (confirmation.action_type !== "apply_task_suggestions") return "";
  try {
    const payload = JSON.parse(confirmation.payload_json) as { fields?: unknown };
    if (!Array.isArray(payload.fields) || payload.fields.length === 0) return "";
    return `Campos: ${payload.fields.map((field) => suggestionLabel(String(field))).join(", ")}`;
  } catch {
    return "";
  }
}

function bulkConfirmHint(confirmations: Confirmation[]) {
  const hasTrello = confirmations.some((confirmation) => confirmation.action_type.startsWith("trello_"));
  const hasSuggestions = confirmations.some((confirmation) => confirmation.action_type === "apply_task_suggestions");
  if (hasTrello && hasSuggestions) return "Esto puede aplicar sugerencias locales y cambios en Trello según cada acción.";
  if (hasTrello) return "Esto puede aplicar cambios en Trello según cada acción.";
  if (hasSuggestions) return "Esto aplicará los detalles sugeridos en las tareas seleccionadas.";
  return "Esto ejecutará cada acción pendiente.";
}

function formatTrelloActionFeedback(confirmation: Confirmation) {
  if (confirmation.status === "confirmed") return `Acción Trello ejecutada: ${confirmation.summary ?? confirmation.action_type}`;
  return `Confirmación creada: ${confirmation.summary ?? confirmation.action_type}`;
}

function formatBackgroundJobSummary(job: BackgroundJob) {
  if (job.failed === 0) return `${job.succeeded} acción(es) confirmada(s).`;
  if (job.succeeded === 0) return `No se pudo procesar ninguna confirmación. ${job.failed} fallaron.`;
  return `${job.succeeded} acción(es) confirmada(s); ${job.failed} fallaron.`;
}

function isTerminalJob(job: BackgroundJob) {
  return ["completed", "partial", "failed"].includes(job.status);
}


function readError(error: unknown) {
  const message = error instanceof Error ? error.message : "Error inesperado";
  try {
    const parsed = JSON.parse(message) as { detail?: unknown; message?: unknown };
    const detail = parsed.detail ?? parsed.message;
    if (typeof detail === "string") return toHumanError(detail);
  } catch {
    return toHumanError(message);
  }
  return toHumanError(message);
}

function toHumanError(message: string) {
  if (!message.trim()) return "Error inesperado";
  if (message.includes("No encontré") && message.includes("lista")) {
    return `${message} Abrí Configuración > Trello y usá Validar listas para actualizar los mapeos.`;
  }
  if (message === "Not Found") return "No encontré ese endpoint o recurso.";
  return message;
}

function scrollToSettingsSection(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  const toolbarBottom = document.querySelector(".settings-toolbar")?.getBoundingClientRect().bottom ?? 0;
  const offset = Math.max(toolbarBottom + 16, 96);
  window.scrollTo({
    top: window.scrollY + target.getBoundingClientRect().top - offset,
    behavior: "auto",
  });
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
