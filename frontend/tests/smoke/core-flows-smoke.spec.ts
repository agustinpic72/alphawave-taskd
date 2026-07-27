import { expect, test } from "@playwright/test";

import { assertNoHorizontalOverflow, cleanupSmokeTasks, createSmokeTask, installBrowserGuards, searchTasks, smokePrefix, taskCard } from "./helpers";

test.describe("core browser smoke", () => {
  test.beforeEach(async ({ page, request }) => {
    await cleanupSmokeTasks(request);
    await page.route("**/api/integrations/openai/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ safe_to_use: true }),
      });
    });
  });

  test.afterEach(async ({ request }) => {
    await cleanupSmokeTasks(request);
  });

  test("local Personal and project-scoped tasks complete without Trello modal", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const personalTitle = `${smokePrefix} Personal local`;
    const alphaTitle = `${smokePrefix} ALPHA local sin card`;
    const personal = await createSmokeTask(request, personalTitle, { scope: "Personal" });
    const alpha = await createSmokeTask(request, alphaTitle, { scope: "ALPHA" });

    await page.goto("/");
    await searchTasks(page, smokePrefix);

    await expect(taskCard(page, personalTitle)).toBeVisible();
    await expect(taskCard(page, alphaTitle)).toBeVisible();
    await expect(taskCard(page, alphaTitle)).toContainText("ALPHA");

    await taskCard(page, personalTitle).getByTestId("task-complete-checkbox").click();
    await expect(taskCard(page, personalTitle)).toHaveCount(0);

    await taskCard(page, alphaTitle).getByTestId("task-complete-checkbox").click();
    await expect(page.getByRole("dialog", { name: /Esta tarea viene de Trello/ })).toHaveCount(0);
    await expect(taskCard(page, alphaTitle)).toHaveCount(0);

    const completed = await request.get("/api/tasks?status=completed");
    expect(completed.ok()).toBeTruthy();
    const completedTasks = (await completed.json()).tasks as Array<{ id: string; title: string }>;
    expect(completedTasks.some((task) => task.id === personal.id)).toBeTruthy();
    expect(completedTasks.some((task) => task.id === alpha.id)).toBeTruthy();

    await assertNoBrowserErrors();
  });

  test("priority proposal and detail explanation render without nullish text", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const highTitle = `${smokePrefix} Prioridad alta`;
    const lowTitle = `${smokePrefix} Prioridad baja`;
    const today = new Date();
    today.setHours(18, 0, 0, 0);
    await createSmokeTask(request, highTitle, {
      scope: "Personal",
      priority_label: "high",
      impact_score: 5,
      urgency_score: 5,
      blocking_score: 4,
      due_at: today.toISOString(),
    });
    await createSmokeTask(request, lowTitle, {
      scope: "Personal",
      priority_label: "low",
      impact_score: 1,
      urgency_score: 1,
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);

    await page.getByTestId("priority-button").click();
    const proposal = page.getByTestId("priority-proposal-modal");
    await expect(proposal).toBeVisible();
    await expect(proposal).toContainText("Propuesta de Prioridad");
    await expect(proposal).not.toContainText(/\b(null|None|undefined)\b/);
    await proposal.getByRole("button", { name: "Cerrar propuesta" }).click();

    await taskCard(page, highTitle).click();
    const explanation = page.getByTestId("priority-explanation");
    await expect(explanation).toBeVisible();
    await expect(explanation).toContainText("Por qué esta prioridad");
    await expect(explanation).not.toContainText(/\b(null|None|undefined)\b/);

    await assertNoBrowserErrors();
  });

  test("task detail suggestions open and apply selected metadata locally", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} implementar endpoint de sugerencias`;
    const task = await createSmokeTask(request, title, { scope: "DELTA" });
    await page.route(`**/api/tasks/${task.id}/suggest-details`, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3200));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(smokeSuggestion(task.id)),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();

    await page.getByTestId("task-suggest-details-button").click();
    const progressModal = page.getByTestId("individual-suggestion-progress-modal");
    await expect(progressModal).toBeVisible();
    await expect(progressModal.locator(".ai-progress-spinner")).toBeVisible();
    await expect(progressModal.getByTestId("ai-suggestion-progressbar")).not.toHaveAttribute("aria-valuenow");
    await expect(progressModal).toContainText("Analizando la tarea");
    await expect(progressModal).toContainText("Transcurrido:");
    const modal = page.getByTestId("task-suggestions-modal");
    await expect(modal).toBeVisible();
    await expect(progressModal).toHaveCount(0);
    await expect(modal).toContainText("Sugerencias para");
    await expect(modal).toContainText(/Fallback heurístico|OpenAI/);
    await expect(page.getByTestId("task-suggestion-row-priority_label")).toBeVisible();

    await page.getByTestId("task-suggestions-apply-selected").click();
    await expect(modal).toHaveCount(0);

    const response = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
    expect(response.ok()).toBeTruthy();
    const payload = (await response.json()) as { tasks: Array<{ id: string; priority_label: string | null; source_id: string | null }> };
    const updated = payload.tasks.find((item) => item.id === task.id);
    expect(updated?.priority_label).toBeTruthy();
    expect(updated?.source_id).toBeNull();

    await assertNoBrowserErrors();
  });

  test("applied suggestions survive an unchanged timestamp and the next autosave", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} [AI APPLY REGRESSION] Documentar onboarding`;
    const task = await createSmokeTask(request, title, { scope: "Personal" }) as { id: string; updated_at: string };
    let suggestionRequest = 0;

    await page.route(`**/api/tasks/${task.id}/suggest-details`, async (route) => {
      suggestionRequest += 1;
      const gapsResponse = await request.get(`/api/tasks/${task.id}/detail-gaps`);
      expect(gapsResponse.ok()).toBeTruthy();
      const gaps = (await gapsResponse.json()) as { missing_fields: string[] };
      const available = {
        effort_bucket: suggestionItem("deep"),
        estimated_minutes: suggestionItem(90),
        context_bucket: suggestionItem("admin"),
      };
      const fields = suggestionRequest === 1
        ? ["effort_bucket", "estimated_minutes", "context_bucket"]
        : gaps.missing_fields.filter((field) => field in available);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          task_id: task.id,
          suggestions: Object.fromEntries(fields.map((field) => [field, available[field as keyof typeof available]])),
          source: "heuristic",
          llm_status: "disabled",
          missing_fields: gaps.missing_fields,
          created_at: "2026-07-18T00:00:00Z",
          warnings: ["Fixture determinística sin OpenAI."],
        }),
      });
    });
    await page.route(`**/api/tasks/${task.id}/apply-suggestions`, async (route) => {
      const response = await route.fetch();
      const payload = await response.json() as Record<string, unknown>;
      const authoritativeTask = (payload.task ?? payload) as Record<string, unknown>;
      authoritativeTask.updated_at = task.updated_at;
      await route.fulfill({
        response,
        contentType: "application/json",
        body: JSON.stringify(payload.task ? { ...payload, task: authoritativeTask } : authoritativeTask),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();
    await page.getByTestId("task-suggest-details-button").click();
    const modal = page.getByTestId("task-suggestions-modal");
    await expect(modal).toBeVisible();
    await page.getByTestId("task-suggestion-row-context_bucket").getByRole("checkbox").uncheck();
    await page.getByTestId("task-suggestions-apply-selected").click();
    await expect(modal).toHaveCount(0);

    await expect(page.getByTestId("task-metadata-effort")).toHaveValue("deep");
    await expect(page.getByTestId("task-metadata-minutes")).toHaveValue("90");
    await page.getByTestId("task-metadata-priority").selectOption("medium");
    await expect(page.getByText("Cambios sin guardar", { exact: true })).toBeVisible();
    await expect(page.getByText("Guardado", { exact: true })).toBeVisible();

    const persistedResponse = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
    const persistedPayload = await persistedResponse.json() as {
      tasks: Array<{
        id: string;
        effort_bucket: string | null;
        estimated_minutes: number | null;
        context_bucket: string | null;
        priority_label: string | null;
      }>;
    };
    const persisted = persistedPayload.tasks.find((item) => item.id === task.id);
    expect(persisted).toMatchObject({
      effort_bucket: "deep",
      estimated_minutes: 90,
      context_bucket: null,
      priority_label: "medium",
    });

    await page.getByTestId("task-suggest-details-button").click();
    const secondModal = page.getByTestId("task-suggestions-modal");
    await expect(secondModal).toBeVisible();
    await expect(secondModal.getByTestId("task-suggestion-row-effort_bucket")).toHaveCount(0);
    await expect(secondModal.getByTestId("task-suggestion-row-estimated_minutes")).toHaveCount(0);
    await expect(secondModal.getByTestId("task-suggestion-row-context_bucket")).toBeVisible();
    await assertNoBrowserErrors();
  });

  test("task detail editor saves notes and metadata, then completes a local project task", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} M18C editar detalle`;
    const task = await createSmokeTask(request, title, { scope: "ALPHA" });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();

    const detail = page.getByTestId("task-detail-panel");
    await expect(detail).toBeVisible();
    await expect(detail).toContainText("ALPHA");

    await page.getByTestId("task-notes-textarea").fill("Nota M18C línea 1\nNota M18C línea 2");
    await page.getByTestId("task-metadata-priority").selectOption("medium_high");
    await page.getByTestId("task-metadata-effort").selectOption("deep");
    await page.getByTestId("task-metadata-minutes").fill("90");
    await page.getByTestId("task-notes-save").click();

    await expect
      .poll(async () => {
        const response = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
        const payload = (await response.json()) as { tasks: Array<Record<string, unknown>> };
        const updated = payload.tasks.find((item) => item.id === task.id);
        const metadata = updated?.metadata_json ? JSON.parse(String(updated.metadata_json)) : {};
        return {
          notes: metadata.notes,
          priority: updated?.priority_label,
          effort: updated?.effort_bucket,
          minutes: updated?.estimated_minutes,
        };
      })
      .toEqual({
        notes: "Nota M18C línea 1\nNota M18C línea 2",
        priority: "medium_high",
        effort: "deep",
        minutes: 90,
      });

    await page.getByTestId("task-complete-button").click();
    await expect(page.getByRole("dialog", { name: /Esta tarea viene de Trello/ })).toHaveCount(0);
    await expect(taskCard(page, title)).toHaveCount(0);

    const completed = await request.get("/api/tasks?status=completed");
    const completedPayload = (await completed.json()) as { tasks: Array<{ id: string; source_id: string | null }> };
    const completedTask = completedPayload.tasks.find((item) => item.id === task.id);
    expect(completedTask?.source_id).toBeNull();

    await assertNoBrowserErrors();
  });

  test("task detail keeps autosave errors visible until retry", async ({ page, request }) => {
    const title = `${smokePrefix} autosave error visible`;
    const task = await createSmokeTask(request, title, { scope: "Personal" });
    await page.route(`**/api/tasks/${task.id}`, async (route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Fallo controlado." }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();
    await page.getByTestId("task-metadata-priority").selectOption("medium");

    const detail = page.getByTestId("task-detail-panel");
    await expect(detail.getByText("Error al guardar", { exact: true })).toBeVisible();
    await page.waitForTimeout(500);
    await expect(detail.getByText("Error al guardar", { exact: true })).toBeVisible();
  });

  test("bulk suggestions review renders generated queue without applying", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const firstTitle = `${smokePrefix} bulk sugerencias endpoint`;
    const secondTitle = `${smokePrefix} bulk sugerencias cafe`;
    const first = await createSmokeTask(request, firstTitle, { scope: "DELTA" });
    const second = await createSmokeTask(request, secondTitle, { scope: "Personal" });
    await page.route("**/api/tasks/suggest-details-bulk", async (route) => {
      const payload = route.request().postDataJSON() as { task_ids: string[] };
      const selectedIds = payload.task_ids.filter((id) => id === first.id || id === second.id);
      await new Promise((resolve) => setTimeout(resolve, 2200));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          mode: "sync",
          source_summary: { openai: 0, heuristic: selectedIds.length, error: 0 },
          results: selectedIds.map((id) => ({
            task_id: id,
            ok: true,
            suggestion: smokeSuggestion(id),
            error: null,
          })),
          warnings: [],
        }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();

    const modal = page.getByTestId("bulk-suggestions-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Sugerir detalles");
    await page.getByTestId("bulk-suggestions-generate").click();
    const progress = modal.getByTestId("ai-suggestion-progress");
    await expect(progress).toBeVisible();
    await expect(progress.locator(".ai-progress-spinner")).toBeVisible();
    await expect(progress).toContainText("Analizando 2 tareas");
    await expect(progress).not.toContainText("0/2");
    await expect(progress.getByTestId("ai-suggestion-progressbar")).not.toHaveAttribute("aria-valuenow");
    await expect(progress).toContainText("Transcurrido:");
    await expect(page.getByTestId("bulk-suggestion-row").filter({ hasText: firstTitle })).toBeVisible();
    await expect(page.getByTestId("bulk-suggestion-row").filter({ hasText: secondTitle })).toBeVisible();
    await expect(modal).toContainText("No se hará ningún cambio en Trello");
    await expect(page.locator(".toast").filter({ hasText: "Sugerencias listas" })).toHaveCount(1);

    await modal.getByRole("button", { name: "Descartar todas" }).click();
    await expect(modal).toHaveCount(0);

    const response = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(smokePrefix)}`);
    expect(response.ok()).toBeTruthy();
    const payload = (await response.json()) as { tasks: Array<{ id: string; priority_label: string | null; source_id: string | null }> };
    const firstUpdated = payload.tasks.find((item) => item.id === first.id);
    const secondUpdated = payload.tasks.find((item) => item.id === second.id);
    expect(firstUpdated?.priority_label).toBeNull();
    expect(firstUpdated?.source_id).toBeNull();
    expect(secondUpdated?.priority_label).toBeNull();

    await assertNoBrowserErrors();
  });

  test("detail completion selector keeps manual available when OpenAI is unavailable", async ({ page, request }) => {
    const title = `${smokePrefix} selector manual sin openai`;
    await createSmokeTask(request, title);
    await page.route("**/api/integrations/openai/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ safe_to_use: false }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    const modal = page.getByTestId("bulk-suggestions-modal");
    await expect(modal.getByTestId("manual-details-route")).toBeEnabled();
    await expect(modal.getByTestId("bulk-suggestions-generate")).toBeDisabled();
    await expect(modal.getByTestId("show-incomplete-list-route")).toBeEnabled();
    await expect(modal.getByRole("button", { name: "Cerrar", exact: true }).last()).toBeVisible();
  });

  test("manual detail queue saves, advances, and persists after reload", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} completar detalles manualmente`;
    const task = await createSmokeTask(request, title, { scope: "Personal" });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("manual-details-route").click();

    const queue = page.getByTestId("manual-detail-queue");
    await expect(queue).toBeVisible();
    await expect(queue).toContainText("Tarea 1 de 1");
    await expect(queue).toContainText(title);
    await queue.getByLabel("Prioridad").selectOption("medium_high");
    await queue.getByTestId("manual-details-save-next").click();
    await expect(queue).toContainText("Recorrido terminado");
    await queue.locator(".manual-detail-summary").getByRole("button", { name: "Cerrar" }).click();

    const response = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
    const payload = (await response.json()) as { tasks: Array<{ id: string; priority_label: string | null }> };
    expect(payload.tasks.find((item) => item.id === task.id)?.priority_label).toBe("medium_high");

    await page.reload();
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();
    await expect(page.getByTestId("task-metadata-priority")).toHaveValue("medium_high");
    await assertNoBrowserErrors();
  });

  test("incomplete detail list filter is temporary and exposes missing counts", async ({ page, request }) => {
    const title = `${smokePrefix} filtro detalles incompletos`;
    await createSmokeTask(request, title, { scope: "Personal" });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("show-incomplete-list-route").click();

    const filter = page.getByTestId("incomplete-details-filter");
    await expect(filter).toContainText("Detalles incompletos (1)");
    await expect(taskCard(page, title)).toContainText(/Faltan \d+ datos/);
    await filter.getByRole("button", { name: "Quitar filtro de detalles incompletos" }).click();
    await expect(filter).toHaveCount(0);
  });

  test("manual detail queue can skip and review skipped tasks in session order", async ({ page, request }) => {
    await createSmokeTask(request, `${smokePrefix} omitir detalle uno`);
    await createSmokeTask(request, `${smokePrefix} omitir detalle dos`);
    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("manual-details-route").click();
    const queue = page.getByTestId("manual-detail-queue");

    await expect(queue).toContainText("Tarea 1 de 2");
    await queue.getByRole("button", { name: "Omitir por ahora" }).click();
    await expect(queue).toContainText("Tarea 2 de 2");
    await queue.getByRole("button", { name: "Omitir por ahora" }).click();
    await expect(queue).toContainText("2 omitida(s)");
    await queue.getByRole("button", { name: "Revisar omitidas" }).click();
    await expect(queue).toContainText("Tarea 1 de 2");
  });

  test("wizard individual suggestion reviews before apply and updates the queue", async ({ page, request }) => {
    const title = `${smokePrefix} sugerencia individual en cola`;
    const task = await createSmokeTask(request, title);
    await page.route(`**/api/tasks/${task.id}/suggest-details`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(smokeSuggestion(task.id)),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("manual-details-route").click();
    const queue = page.getByTestId("manual-detail-queue");
    await queue.getByRole("button", { name: "Sugerir esta tarea" }).click();
    const review = page.getByTestId("task-suggestions-modal");
    await expect(review).toBeVisible();

    const before = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
    expect((await before.json()).tasks[0].priority_label).toBeNull();
    await review.getByTestId("task-suggestions-apply-selected").click();
    await expect(review).toHaveCount(0);
    await expect(queue).toContainText("Recorrido terminado");
    const after = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(title)}`);
    expect((await after.json()).tasks[0].priority_label).toBe("medium");
  });

  test("manual detail queue preserves a draft after a version conflict", async ({ page, request }) => {
    const title = `${smokePrefix} conflicto detalle manual`;
    const task = await createSmokeTask(request, title, { scope: "Personal" });
    let intercepted = false;
    await page.route(`**/api/tasks/${task.id}/complete-details`, async (route) => {
      if (!intercepted) {
        intercepted = true;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ detail: "La tarea cambió." }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("manual-details-route").click();
    const queue = page.getByTestId("manual-detail-queue");
    await queue.getByLabel("Notas").fill("Borrador que no debe perderse");
    await queue.getByTestId("manual-details-save-next").click();

    await expect(queue.getByRole("alert")).toContainText("Conservé lo que escribiste");
    await expect(queue.getByLabel("Notas")).toHaveValue("Borrador que no debe perderse");
    await expect(queue.getByTestId("manual-details-save-next")).toBeDisabled();
    await queue.getByRole("button", { name: "Usar mi borrador de todos modos" }).click();
    await expect(queue.getByTestId("manual-details-save-next")).toBeEnabled();
  });

  test("manual detail queue fits a 390 by 844 viewport", async ({ page, request }) => {
    const title = `${smokePrefix} detalle manual mobile`;
    await createSmokeTask(request, title, { scope: "Personal" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("manual-details-route").click();

    await expect(page.getByTestId("manual-detail-queue")).toBeVisible();
    await expect(page.locator("[data-manual-field]").first()).toBeFocused();
    await expect(page.getByTestId("manual-details-save-next")).toBeVisible();
    await assertNoHorizontalOverflow(page, "manual detail queue mobile");
  });

  test("bulk apply keeps skipped fields visible as a partial result", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} bulk stale suggestion`;
    const task = await createSmokeTask(request, title, { scope: "Personal" }) as Record<string, unknown> & { id: string };
    await page.route("**/api/tasks/suggest-details-bulk", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          mode: "sync",
          source_summary: { openai: 0, heuristic: 1, error: 0 },
          results: [{ task_id: task.id, ok: true, suggestion: smokeSuggestion(task.id), error: null }],
          warnings: [],
        }),
      });
    });
    await page.route("**/api/tasks/apply-suggestions-bulk", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          applied: 0,
          errors: [],
          tasks: [task],
          results: [{
            task,
            applied_fields: [],
            skipped_fields: [{ field: "priority_label", reason: "no_overwrite" }],
            remaining_missing_fields: ["effort_bucket", "estimated_minutes", "context_bucket", "notes"],
            is_candidate: true,
            priority_explanation: {},
          }],
        }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    await page.getByTestId("bulk-suggestions-generate").click();
    await expect(page.getByTestId("bulk-suggestion-row")).toBeVisible();
    await page.getByTestId("bulk-suggestions-apply-selected").click();

    const modal = page.getByTestId("bulk-suggestions-modal");
    await expect(modal).toContainText("Aplicación parcial");
    await expect(modal).toContainText("Prioridad: cambió antes de aplicar");
    await expect(page.getByTestId("bulk-suggestion-row")).toBeVisible();
    await expect(page.getByTestId("bulk-suggestions-apply-selected")).toBeEnabled();
    await assertNoBrowserErrors();
  });

  test("bulk suggestion progress reports partial success once without auto-apply", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const firstTitle = `${smokePrefix} progreso parcial disponible`;
    const secondTitle = `${smokePrefix} progreso parcial fallida`;
    const first = await createSmokeTask(request, firstTitle);
    const second = await createSmokeTask(request, secondTitle);
    await page.route("**/api/tasks/suggest-details-bulk", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          mode: "sync",
          source_summary: { openai: 1, heuristic: 0, error: 1 },
          results: [
            { task_id: first.id, ok: true, suggestion: smokeSuggestion(first.id), error: null },
            { task_id: second.id, ok: false, suggestion: null, error: "No se pudo analizar esta tarea." },
          ],
          warnings: [],
        }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    const modal = page.getByTestId("bulk-suggestions-modal");
    await page.getByTestId("bulk-suggestions-generate").click();

    await expect(modal.getByTestId("ai-suggestion-progress")).toContainText("1 completada, 1 con error");
    await expect(modal.getByTestId("ai-suggestion-progressbar")).toHaveAttribute("aria-valuenow", "100");
    await expect(modal.getByRole("alert")).toContainText("1 tarea tuvo error");
    await expect(page.locator(".toast").filter({ hasText: "Sugerencias generadas con 1 error" })).toHaveCount(1);

    const response = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(smokePrefix)}`);
    const payload = (await response.json()) as { tasks: Array<{ id: string; priority_label: string | null }> };
    expect(payload.tasks.find((item) => item.id === first.id)?.priority_label).toBeNull();
    expect(payload.tasks.find((item) => item.id === second.id)?.priority_label).toBeNull();
    await assertNoBrowserErrors();
  });

  test("bulk suggestion error stays accessible on reduced-motion mobile", async ({ page, request }) => {
    const title = `${smokePrefix} progreso error mobile`;
    await createSmokeTask(request, title);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/api/tasks/suggest-details-bulk", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "No se pudieron generar las sugerencias." }),
      });
    });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("bulk-suggest-details-button").click();
    const modal = page.getByTestId("bulk-suggestions-modal");
    await page.getByTestId("bulk-suggestions-generate").click();
    const spinner = modal.locator(".ai-progress-spinner");
    await expect(spinner).toBeVisible();
    await expect(spinner).toHaveCSS("animation-name", "none");
    await expect(modal.getByRole("alert")).toContainText("No se pudieron generar las sugerencias");
    await expect(modal).not.toContainText(/sk-|Return the required structured|response_format/i);
    await assertNoHorizontalOverflow(page, "bulk suggestion error mobile");
  });

  test("inbox triage moves one task out of Inbox and skips the next", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const firstTitle = `${smokePrefix} M18D procesar factura`;
    const secondTitle = `${smokePrefix} M18D omitir cafe`;
    const first = await createSmokeTask(request, firstTitle, { scope: "Inbox" });
    const second = await createSmokeTask(request, secondTitle, { scope: "Inbox" });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("inbox-process-button").click();

    const modal = page.getByTestId("inbox-triage-panel");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText(firstTitle);

    await page.getByTestId("inbox-triage-scope-select").selectOption("Personal");
    await page.getByTestId("inbox-triage-notes").fill("Procesada desde smoke M18D");
    await page.getByTestId("inbox-triage-save-next").click();

    await expect(modal).toContainText(secondTitle);
    await page.getByTestId("inbox-triage-skip").click();
    await expect(modal).toContainText("Inbox procesado");
    await page.getByTestId("inbox-triage-close-done").click();

    const active = await request.get(`/api/tasks?status=active&q=${encodeURIComponent(smokePrefix)}`);
    expect(active.ok()).toBeTruthy();
    const payload = (await active.json()) as { tasks: Array<{ id: string; scope: string; metadata_json: string | null }> };
    const moved = payload.tasks.find((task) => task.id === first.id);
    const skipped = payload.tasks.find((task) => task.id === second.id);
    expect(moved?.scope).toBe("Personal");
    expect(skipped?.scope).toBe("Inbox");
    expect(moved?.metadata_json ? JSON.parse(moved.metadata_json).notes : null).toBe("Procesada desde smoke M18D");

    await assertNoBrowserErrors();
  });

  test("today and now planning render grouped reasons and alternatives", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const now = new Date();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const today = new Date(now);
    today.setHours(18, 0, 0, 0);
    await createSmokeTask(request, `${smokePrefix} M18E vencida`, {
      scope: "Personal",
      due_at: yesterday.toISOString(),
      priority_label: "high",
      impact_score: 5,
      urgency_score: 5,
    });
    await createSmokeTask(request, `${smokePrefix} M18E hoy`, {
      scope: "Personal",
      due_at: today.toISOString(),
    });
    await createSmokeTask(request, `${smokePrefix} M18E quick`, {
      scope: "Personal",
      effort_bucket: "quick",
      estimated_minutes: 15,
      context_bucket: "admin",
    });
    await createSmokeTask(request, `${smokePrefix} M18E deep`, {
      scope: "DELTA",
      effort_bucket: "deep",
      estimated_minutes: 90,
      impact_score: 4,
    });

    await page.goto("/");
    await page.getByRole("button", { name: /Hoy/ }).click();
    const todayView = page.getByTestId("today-plan-view");
    await expect(todayView).toBeVisible();
    await expect(page.getByTestId("today-plan-group-overdue")).toBeVisible();
    await expect(page.getByTestId("today-plan-group-today")).toBeVisible();
    await expect(page.getByTestId("today-plan-group-quick_wins")).toBeVisible();
    await expect(todayView).not.toContainText(/\b(null|None|undefined|\[\])\b/);

    await page.getByRole("button", { name: /Ahora/ }).click();
    const nowView = page.getByTestId("now-plan-view");
    await expect(nowView).toBeVisible();
    await expect(page.getByTestId("now-next-action")).toContainText(`${smokePrefix} M18E vencida`);
    await expect(page.getByTestId("now-alternatives")).toBeVisible();
    await expect(nowView).not.toContainText(/\b(null|None|undefined|\[\])\b/);

    await assertNoBrowserErrors();
  });
});

function smokeSuggestion(taskId: string) {
  return {
    task_id: taskId,
    suggestions: {
      priority_label: {
        value: "medium",
        confidence: 0.8,
        reason: "Fixture local del browser smoke.",
        applies_to_empty_field: true,
      },
    },
    source: "heuristic",
    llm_status: "disabled",
    missing_fields: ["priority_label"],
    created_at: "2026-07-18T00:00:00Z",
    warnings: ["Fallback heurístico aislado para browser smoke."],
  };
}

function suggestionItem(value: unknown) {
  return {
    value,
    confidence: 0.8,
    reason: "Fixture local del browser smoke.",
    applies_to_empty_field: true,
  };
}
