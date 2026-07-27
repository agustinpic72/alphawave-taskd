import { expect, test } from "@playwright/test";

import {
  assertNoHorizontalOverflow,
  cleanupSmokeTasks,
  createSmokeTask,
  installBrowserGuards,
  searchTasks,
  smokePrefix,
  taskCard,
} from "./helpers";

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1366, height: 768 },
  { name: "mobile", width: 390, height: 844 },
];

test.describe("responsive browser smoke", () => {
  test.beforeEach(async ({ request }) => {
    await cleanupSmokeTasks(request);
  });

  test.afterEach(async ({ request }) => {
    await cleanupSmokeTasks(request);
  });

  for (const viewport of viewports) {
    test(`app shell has no horizontal overflow at ${viewport.name}`, async ({ page, request }) => {
      const assertNoBrowserErrors = installBrowserGuards(page);
      const today = new Date();
      today.setHours(18, 0, 0, 0);
      await createSmokeTask(request, `${smokePrefix} responsive ${viewport.name}`, {
        scope: "Personal",
        due_at: today.toISOString(),
      });
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      await page.goto("/");
      await expect(page.getByRole("heading", { name: "TODO" })).toBeVisible();
      await assertNoHorizontalOverflow(page, `shell ${viewport.name}`);

      await page.getByRole("button", { name: /Hoy/ }).click();
      await expect(page.getByTestId("today-plan-view")).toBeVisible();
      await assertNoHorizontalOverflow(page, `today ${viewport.name}`);

      await page.getByRole("button", { name: /Ahora/ }).click();
      await expect(page.getByTestId("now-plan-view")).toBeVisible();
      await assertNoHorizontalOverflow(page, `now ${viewport.name}`);

      await assertNoBrowserErrors();
    });
  }

  test("mobile task detail opens as usable sheet without overflow", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} M18F mobile detail`;
    await createSmokeTask(request, title, { scope: "Personal" });
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await taskCard(page, title).click();

    const detail = page.getByTestId("task-detail-panel");
    await expect(detail).toBeVisible();
    await expect(page.getByTestId("task-notes-textarea")).toBeVisible();
    await expect(page.getByTestId("task-suggest-details-button")).toBeVisible();
    await assertNoHorizontalOverflow(page, "mobile detail");

    await detail.getByRole("button", { name: "Cerrar detalle" }).click();
    await expect(detail).toHaveCount(0);
    await assertNoBrowserErrors();
  });

  test("mobile settings, backups and Trello sections remain readable", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/");
    await page.getByTestId("settings-button").click();
    await expect(page.getByTestId("settings-section-status")).toBeVisible();
    await assertNoHorizontalOverflow(page, "mobile settings status");

    await page.getByRole("link", { name: "Trello" }).click();
    await expect(page.getByTestId("settings-section-trello")).toBeVisible();
    await expect(page.getByTestId("settings-section-trello")).toContainText(/No hay boards conectados|clasificaciones activas/);
    await assertNoHorizontalOverflow(page, "mobile settings trello");

    await page.getByRole("link", { name: "Backups" }).click();
    await expect(page.getByTestId("settings-section-backups")).toBeVisible();
    await expect(page.getByRole("button", { name: /Crear backup ahora/ })).toBeVisible();
    await assertNoHorizontalOverflow(page, "mobile settings backups");

    await assertNoBrowserErrors();
  });

  test("mobile inbox processing modal is reachable without overflow", async ({ page, request }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    const title = `${smokePrefix} M18F inbox mobile`;
    await createSmokeTask(request, title, { scope: "Inbox" });
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/");
    await searchTasks(page, smokePrefix);
    await page.getByTestId("inbox-process-button").click();

    const modal = page.getByTestId("inbox-triage-panel");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText(title);
    await expect(page.getByTestId("inbox-triage-save-next")).toBeVisible();
    await assertNoHorizontalOverflow(page, "mobile inbox triage");

    await modal.getByRole("button", { name: "Cerrar" }).click();
    await assertNoBrowserErrors();
  });
});
