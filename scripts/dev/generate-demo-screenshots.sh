#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${ALPHAWAVE_DEMO_SCREENSHOT_DIR:-${ROOT_DIR}/docs/assets/screenshots}"
PORT="${ALPHAWAVE_DEMO_PORT:-18711}"
BASE_URL="http://127.0.0.1:${PORT}"
TMP_DIR="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p "${OUT_DIR}"

echo "Building the frontend before capturing demo assets."
(cd "${ROOT_DIR}/frontend" && npm run build)

(
  cd "${ROOT_DIR}/backend"
  APP_HOST=127.0.0.1 \
  APP_PORT="${PORT}" \
  APP_DEV_ENDPOINTS_ENABLED=false \
  DATABASE_URL="sqlite:///${TMP_DIR}/demo.sqlite" \
  LOG_FILE="${TMP_DIR}/demo.log" \
  ALPHAWAVE_AUTH_ENABLED=false \
  ALPHAWAVE_SECRET_ENCRYPTION_KEY= \
  TELEGRAM_ENABLED=false \
  TELEGRAM_BOT_TOKEN= \
  TELEGRAM_ALLOWED_USER_ID= \
  TRELLO_ENABLED=false \
  TRELLO_WRITE_ENABLED=false \
  TRELLO_API_KEY= \
  TRELLO_TOKEN= \
  TRELLO_MEMBER_ID= \
  TRELLO_BOARD_ALPHA_ID= \
  TRELLO_BOARD_BETA_ID= \
  BACKUP_ENABLED=false \
  REMINDERS_ENABLED=false \
  DAILY_BRIEFING_ENABLED=false \
  CHECKINS_ENABLED=false \
  .venv/bin/python -m app.main
) &
SERVER_PID="$!"

for _ in {1..60}; do
  if curl -fsS "${BASE_URL}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! curl -fsS "${BASE_URL}/api/health" >/dev/null; then
  echo "Demo server did not start at ${BASE_URL}." >&2
  exit 1
fi

SCREENSHOT_SCRIPT="${TMP_DIR}/screenshots.cjs"
cat >"${SCREENSHOT_SCRIPT}" <<'NODE'
const { chromium } = require("@playwright/test");

const baseURL = process.env.ALPHAWAVE_BASE_URL;
const outDir = process.env.ALPHAWAVE_SCREENSHOT_OUT_DIR;
const channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL || "chrome";
const launchOptions = channel === "bundled" ? {} : { channel };

async function api(path, payload) {
  const response = await fetch(`${baseURL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function clickByText(page, text) {
  const target = page.getByText(text, { exact: true }).first();
  await target.click();
  await page.waitForTimeout(350);
}

async function captureDemoScreenshot(page, name) {
  const text = await page.locator("body").innerText();
  const legacyTaxonomy = [
    ["Quan", "tum Tr", "ade"],
    ["Vela", "ri"],
    ["Tra", "des Conn", "ector"],
    ["Zon", "Wizard"],
  ].map((parts) => new RegExp(parts.join(""), "i"));
  const forbidden = [
    ...legacyTaxonomy,
    /(?:api[_ -]?key|token|chat[_ -]?id|board[_ -]?id)\s*[:=]\s*\S+/i,
    /\/(?:home|Users)\/[^\s]+/i,
  ];
  const match = forbidden.find((pattern) => pattern.test(text));
  if (match) throw new Error(`Refusing to capture ${name}: rendered content matched ${match}`);
  if (!text.includes("[DEMO]")) throw new Error(`Refusing to capture ${name}: no demo task is visible`);
  await page.screenshot({ path: `${outDir}/${name}`, fullPage: true });
}

(async () => {
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });

  const tasks = [
    {
      title: "[DEMO] Preparar resumen de Project Alpha",
      scope: "ALPHA",
      priority_label: "high",
      impact_score: 5,
      urgency_score: 4,
      blocking_score: 2,
      effort_bucket: "deep",
      context_bucket: "deep_work",
      estimated_minutes: 90,
    },
    {
      title: "[DEMO] Revisar checklist de Project Beta",
      scope: "BETA",
      priority_label: "medium_high",
      impact_score: 4,
      urgency_score: 3,
      effort_bucket: "quick",
      context_bucket: "review",
      estimated_minutes: 25,
    },
    {
      title: "[DEMO] Organizar notas de Project Gamma",
      scope: "GAMMA",
      priority_label: "medium",
      impact_score: 3,
      urgency_score: 2,
      effort_bucket: "quick",
      context_bucket: "admin",
      estimated_minutes: 20,
    },
    {
      title: "[DEMO] Planificar seguimiento de Project Delta",
      scope: "DELTA",
      priority_label: "low",
      impact_score: 2,
      urgency_score: 2,
      blocking_score: 1,
      effort_bucket: "medium",
      context_bucket: "review",
      estimated_minutes: 45,
    },
  ];

  for (const task of tasks) {
    await api("/api/tasks", { auto_classify: false, ...task });
  }

  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.getByPlaceholder(/Buscar/i).fill("[DEMO]");
  await captureDemoScreenshot(page, "todo.png");

  await clickByText(page, "Hoy");
  await captureDemoScreenshot(page, "today.png");

  await clickByText(page, "Ahora");
  await captureDemoScreenshot(page, "now.png");

  await clickByText(page, "TODO");
  await page.getByText("[DEMO] Preparar resumen de Project Alpha").first().click();
  await captureDemoScreenshot(page, "task-detail.png");

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
NODE

(
  cd "${ROOT_DIR}/frontend"
  NODE_PATH="${ROOT_DIR}/frontend/node_modules" \
  ALPHAWAVE_BASE_URL="${BASE_URL}" \
  ALPHAWAVE_SCREENSHOT_OUT_DIR="${OUT_DIR}" \
  node "${SCREENSHOT_SCRIPT}"
)

echo "Demo screenshots written to ${OUT_DIR}"
