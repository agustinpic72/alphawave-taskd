import { expect, test, type Locator, type Page } from "@playwright/test";

import { installBrowserGuards } from "./helpers";

test.describe("settings browser smoke", () => {
  test("system status renders runtime and read-only diagnostics", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const statusResponse = await request.get("/api/system/status");
    expect(statusResponse.ok()).toBeTruthy();
    const status = await statusResponse.json();

    await page.goto("/");
    await openSettings(page);

    const statusSection = page.getByTestId("settings-section-status");
    await expect(statusSection).toBeVisible();
    await expect(statusSection.locator(".system-observability-card").first()).toBeVisible();
    await expect(statusSection).not.toContainText("Cargando diagnóstico");
    await expect(page.getByTestId("system-status-copy")).toBeEnabled();
    await expect(page.getByTestId("system-status-runtime")).toContainText(/Runtime local/);
    await expect(statusSection).toContainText(/Actualizado/);
    await expect(statusSection).toContainText(/Backups/);
    await expect(statusSection).toContainText(/Modo fin de semana/);

    const llm = status.services?.llm ?? status.llm;
    if (llm?.readiness === "not_verified" || llm?.status === "unverified") {
      const iaCard = statusSection.locator(".system-observability-card").filter({
        has: page.locator("strong").filter({ hasText: /^IA$/ }),
      });
      await expect(iaCard).not.toContainText("Activo");
    }

    await assertNoBrowserErrors();
  });

  test("settings sections, dirty save bar, briefing validation and Trello/Backups render", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);

    await page.goto("/");
    await openSettings(page);

    const sections = [
      "status",
      "general",
      "weekend",
      "briefing",
      "reminders",
      "trello",
      "priority",
      "backups",
      "advanced",
    ];
    for (const section of sections) {
      await page.getByRole("link", { name: sectionLabel(section) }).click();
      await expect(page.getByTestId(`settings-section-${section}`)).toBeVisible();
    }

    await expect(page.getByTestId("settings-save-bar")).toHaveCount(0);
    await page.getByRole("link", { name: "Modo fin de semana" }).click();
    await page.getByRole("switch", { name: /fin de semana/i }).click();
    await expect(page.getByTestId("settings-save-bar")).toBeVisible();
    await page.getByTestId("settings-save-bar").getByRole("button", { name: "Descartar" }).click();
    await expect(page.getByTestId("settings-save-bar")).toHaveCount(0);

    await page.getByRole("link", { name: "Briefing" }).click();
    await setTimeField(page, page.getByTestId("briefing-time-input"), "13", "00");
    await setTimeField(page, page.getByTestId("briefing-cutoff-input"), "12", "00");
    await expect(page.getByTestId("settings-section-briefing")).toContainText(/hora límite/i);
    await page.getByTestId("settings-save-bar").getByRole("button", { name: "Descartar" }).click();

    await page.getByRole("link", { name: "Trello" }).click();
    const trelloSection = page.getByTestId("settings-section-trello");
    await expect(trelloSection).toContainText(/No hay boards conectados|clasificaciones activas/);
    if (await page.getByTestId("trello-board-card").count()) {
      await expect(trelloSection).toContainText("Entiendo el riesgo");
    }

    await page.getByRole("link", { name: "Backups" }).click();
    await expect(page.getByRole("button", { name: /Crear backup ahora/ })).toBeVisible();
    await expect(page.getByTestId("settings-section-backups")).toContainText(/Backups disponibles|Todavía no hay backups/);

    await assertNoBrowserErrors();
  });

  test("trello credential hints are masked defensively", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.route("**/api/integrations/trello/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          provider: "trello",
          configured: true,
          credentials_source: "user_encrypted",
          status: "configured",
          api_key_hint: "trello-secret-key-1234567890",
          token_hint: "trello-token-secret-1234567890",
          secret_store_available: true,
        }),
      });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Avanzado" }).click();

    await expect(page.getByLabel("API key", { exact: true })).toHaveAttribute("placeholder", "Guardada");
    await expect(page.getByLabel("Token")).toHaveAttribute("placeholder", "Guardada");
    await expect(page.locator("body")).not.toContainText("trello-secret-key-1234567890");
    await expect(page.locator("body")).not.toContainText("trello-token-secret-1234567890");

    await assertNoBrowserErrors();
  });

  test("OpenAI credentials stay write-only and controls follow validation", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    let status = openAIStatus();
    let catalog = openAIModelCatalog();

    await page.route("**/api/integrations/openai/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/models/refresh") && request.method() === "POST") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(catalog) });
      }
      if (path.endsWith("/models") && request.method() === "GET") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(catalog) });
      }
      if (path.endsWith("/credentials") && request.method() === "POST") {
        const payload = request.postDataJSON() as { api_key: string };
        expect(payload.api_key).toBe("sk-test-browser-secret-value");
        status = openAIStatus({
          api_key_configured: true,
          api_key_hint: "sk-...lue",
          status: "not_validated",
          reason: "La API key está guardada pero todavía no fue validada.",
        });
        catalog = openAIModelCatalog({ status: "ready" });
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
      }
      if (path.endsWith("/validate") && request.method() === "POST") {
        status = openAIStatus({
          api_key_configured: true,
          api_key_hint: "sk-...lue",
          status: "disabled",
          validation_status: "ready",
          validated_model: "gpt-5.6-sol",
          last_validated_at: "2026-07-18T10:00:00+00:00",
          reason: "OpenAI está deshabilitada para este usuario.",
        });
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status, checked: true }),
        });
      }
      if (path.endsWith("/credentials") && request.method() === "DELETE") {
        status = openAIStatus();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Avanzado" }).click();

    const card = page.getByTestId("openai-settings-card");
    const input = page.getByTestId("openai-api-key-input");
    await expect(card).toContainText("Los datos seleccionados de la tarea se envían a OpenAI");
    await expect(input).toHaveValue("");
    await input.fill("sk-test-browser-secret-value");
    await page.getByTestId("openai-save-key").click();
    await expect(input).toHaveValue("");
    await expect(card).toContainText("sk-...lue");
    await expect(page.locator("body")).not.toContainText("sk-test-browser-secret-value");

    const toggle = card.getByRole("switch", { name: "Usar OpenAI" });
    await expect(toggle).toBeDisabled();
    await page.getByTestId("openai-validate").click();
    await expect(toggle).toBeEnabled();

    await card.getByRole("button", { name: "Revocar API key" }).click();
    await expect(page.getByTestId("openai-revoke-confirm")).toBeVisible();
    await page.getByTestId("openai-revoke-confirm").click();
    await expect(card).toContainText("No hay una API key guardada.");

    await assertNoBrowserErrors();
  });

  test("OpenAI model selector is searchable and changing model requires revalidation", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    let status = openAIStatus({
      api_key_configured: true,
      status: "disabled",
      validation_status: "ready",
      validated_model: "gpt-5.6-sol",
    });
    let catalog = openAIModelCatalog({ status: "ready" });
    let catalogFetches = 0;

    await page.route("**/api/integrations/openai/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/models/refresh")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(catalog) });
      }
      if (path.endsWith("/models")) {
        catalogFetches += 1;
        if (catalogFetches === 1) await new Promise((resolve) => setTimeout(resolve, 1200));
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(catalog) });
      }
      if (path.endsWith("/settings") && request.method() === "PATCH") {
        expect(request.postDataJSON()).toEqual({ model: "gpt-5.6-luna" });
        status = openAIStatus({
          api_key_configured: true,
          model: "gpt-5.6-luna",
          status: "not_validated",
          validation_status: "not_validated",
          validated_model: null,
          reason: "El modelo seleccionado todavía no fue validado.",
        });
        catalog = openAIModelCatalog({ status: "ready", current_model: "gpt-5.6-luna" });
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Avanzado" }).click();
    await expect(page.getByTestId("openai-settings-card")).toContainText("Cargando modelos disponibles");
    await expect(page.getByTestId("openai-model-selector")).toBeEnabled();
    await page.getByTestId("openai-refresh-models").click();
    await expect(page.getByTestId("openai-refresh-models")).toContainText("Actualizar modelos");
    await page.getByTestId("openai-model-selector").click();
    await page.getByPlaceholder("Buscar modelo...").fill("luna");
    await expect(page.getByRole("option", { name: /GPT-5.6 Luna/ })).toContainText("Recomendado");
    await page.getByPlaceholder("Buscar modelo...").press("Enter");
    await expect(page.getByTestId("openai-model-selector")).toContainText("GPT-5.6 Luna");
    await expect(page.getByTestId("openai-model-selector")).toBeFocused();
    await page.getByTestId("openai-save-model").click();
    await expect(page.getByTestId("openai-settings-card")).toContainText(/todavía no fue validad/i);
    await assertNoBrowserErrors();
  });

  test("OpenAI unavailable current model stays selected on stale catalog without mobile overflow", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.setViewportSize({ width: 390, height: 844 });
    const status = openAIStatus({
      api_key_configured: true,
      status: "not_validated",
      model_available: false,
      reason: "El modelo configurado ya no está disponible para esta API key.",
    });
    const catalog = openAIModelCatalog({
      status: "ready",
      stale: true,
      cached: true,
      models: [
        {
          id: "gpt-5.6-sol",
          display_name: "GPT-5.6 Sol",
          category: "No disponible",
          is_current: true,
          is_validated: false,
          compatibility: "unavailable",
        },
        {
          id: "gpt-5.6-luna",
          display_name: "GPT-5.6 Luna",
          category: "Más económico",
          is_current: false,
          is_validated: false,
          compatibility: "requires_validation",
        },
      ],
    });
    await page.route("**/api/integrations/openai/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const body = path.endsWith("/models") ? catalog : status;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Avanzado" }).click();
    const card = page.getByTestId("openai-settings-card");
    await expect(page.getByTestId("openai-model-selector")).toContainText("GPT-5.6 Sol");
    await expect(card).toContainText("ya no está disponible");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await assertNoBrowserErrors();
  });

  test("OpenAI refresh degrades visible safe state even if the follow-up status request fails", async ({ page }) => {
    const status = openAIStatus({
      enabled: true,
      user_enabled: true,
      api_key_configured: true,
      status: "ready",
      validation_status: "ready",
      validated_model: "gpt-5.6-sol",
      safe_to_use: true,
    });
    const available = openAIModelCatalog({ status: "ready" });
    const unavailable = openAIModelCatalog({
      status: "ready",
      models: [
        {
          id: "gpt-5.6-sol",
          display_name: "GPT-5.6 Sol",
          category: "No disponible",
          is_current: true,
          is_validated: false,
          compatibility: "unavailable",
        },
      ],
    });
    let refreshSeen = false;
    await page.route("**/api/integrations/openai/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/models/refresh")) {
        refreshSeen = true;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(unavailable) });
      }
      if (path.endsWith("/models")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(available) });
      }
      if (refreshSeen && path.endsWith("/status")) {
        return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporary" }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Avanzado" }).click();
    await page.getByTestId("openai-refresh-models").click();
    const safeSetting = page.locator(".readonly-setting").filter({ hasText: "Uso seguro" });
    await expect(safeSetting).toContainText("No");
    await expect(page.getByTestId("openai-settings-card")).toContainText("ya no está disponible");
  });
});

async function openSettings(page: Page) {
  await page.getByTestId("settings-button").click();
  await expect(page.getByTestId("settings-section-status")).toBeVisible();
}

function sectionLabel(section: string) {
  return (
    {
      status: "Estado",
      general: "General",
      weekend: "Modo fin de semana",
      briefing: "Briefing",
      reminders: "Recordatorios",
      trello: "Trello",
      priority: "Prioridad",
      backups: "Backups",
      advanced: "Avanzado",
    } as Record<string, string>
  )[section];
}

async function setTimeField(page: Page, field: Locator, hour: string, minute: string) {
  const segments = field.locator('[role="spinbutton"]');
  await segments.nth(0).click();
  await selectSegment(page);
  await page.keyboard.type(hour);
  await segments.nth(1).click();
  await selectSegment(page);
  await page.keyboard.type(minute);
}

async function selectSegment(page: Page) {
  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
}

function openAIStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false,
    user_enabled: false,
    provider: "openai",
    status: "requires_api_key",
    api_key_configured: false,
    api_key_hint: null,
    model: "gpt-5.6-sol",
    model_configured: true,
    model_supported: true,
    model_available: true,
    validated_model: null,
    validation_status: "not_validated",
    secret_store_available: true,
    last_validated_at: null,
    last_success_at: null,
    last_error_at: null,
    last_error: null,
    safe_to_use: false,
    fallback_available: true,
    reason: "Ingresá y guardá una API key de OpenAI.",
    ...overrides,
  };
}

function openAIModelCatalog(overrides: Record<string, unknown> = {}) {
  return {
    models: [
      {
        id: "gpt-5.6-luna",
        display_name: "GPT-5.6 Luna",
        category: "Más económico",
        recommendation: "Recomendado para tareas · Optimizado para costo/volumen",
        is_current: false,
        is_validated: false,
        compatibility: "requires_validation",
      },
      {
        id: "gpt-5.6-sol",
        display_name: "GPT-5.6 Sol",
        category: "Máxima calidad",
        recommendation: "Máxima calidad · Mayor costo esperado",
        is_current: true,
        is_validated: true,
        compatibility: "supported",
      },
    ],
    current_model: "gpt-5.6-sol",
    recommended_model: "gpt-5.6-luna",
    fetched_at: "2026-07-18T10:00:00Z",
    cached: false,
    stale: false,
    policy_version: "2026-07-18.1",
    status: "requires_api_key",
    error: null,
    ...overrides,
  };
}
