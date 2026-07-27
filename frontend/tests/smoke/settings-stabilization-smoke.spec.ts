import { expect, test, type Page } from "@playwright/test";

import { assertNoHorizontalOverflow, installBrowserGuards } from "./helpers";

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1366, height: 768 },
  { name: "mobile", width: 390, height: 844 },
  { name: "narrow mobile", width: 320, height: 720 },
];

test.describe("settings stabilization", () => {
  test("weekend scopes come from the API and stale selections remain removable", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() !== "GET" || new URL(route.request().url()).pathname !== "/api/settings") {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      const payload = await response.json();
      payload.available_scopes = ["Inbox", "Personal", "Client Work"];
      payload.selected_weekend_scopes = ["Personal", "client work"];
      payload.settings.modes.weekend.active_scopes = payload.selected_weekend_scopes;
      await route.fulfill({ response, json: payload });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Modo fin de semana" }).click();

    const section = page.getByTestId("settings-section-weekend");
    await expect(section.getByText("Client Work", { exact: true })).toBeVisible();
    const staleCheckbox = section.getByRole("checkbox", { name: /client work/ });
    await expect(staleCheckbox).toBeVisible();
    await expect(section.getByText("No disponible", { exact: true })).toBeVisible();
    await expect(section.getByText("Engineering", { exact: true })).toHaveCount(0);
    await expect(section.getByText("Operations", { exact: true })).toHaveCount(0);

    await expect(staleCheckbox).toBeChecked();
    await staleCheckbox.click();
    await expect(staleCheckbox).toHaveCount(0);
    await expect(page.locator(".settings-toolbar").getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
    await assertNoBrowserErrors();
  });

  test("status badges do not wrap and General omits the storage details disclosure", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.goto("/");
    await openSettings(page);

    const probe = page.locator("body").evaluate(() => {
      const container = document.createElement("div");
      container.style.width = "55px";
      const badge = document.createElement("span");
      badge.className = "status-pill warning";
      badge.textContent = "Programado";
      container.appendChild(badge);
      document.body.appendChild(container);
      const style = getComputedStyle(badge);
      const result = { display: style.display, whiteSpace: style.whiteSpace, wordBreak: style.wordBreak };
      container.remove();
      return result;
    });
    await expect(probe).resolves.toEqual({ display: "inline-flex", whiteSpace: "nowrap", wordBreak: "normal" });

    for (const badge of await page.locator(".status-pill:visible").all()) {
      await expect(badge).toHaveCSS("white-space", "nowrap");
    }

    const generalLink = page.getByRole("link", { name: "General" });
    await generalLink.focus();
    await expect(generalLink).toBeFocused();
    await generalLink.press("Enter");
    await expect(page.getByTestId("settings-section-general")).toBeVisible();
    await expect(page.getByTestId("settings-section-general").locator("details")).toHaveCount(0);
    await assertNoBrowserErrors();
  });

  test("backups render five rows initially and expand by a chunk", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.route("**/api/backups", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ backups: Array.from({ length: 12 }, (_, index) => backupFixture(index)).reverse() }),
      });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Backups" }).click();

    const section = page.getByTestId("settings-section-backups");
    await expect(section.getByTestId("backup-row")).toHaveCount(5);
    await expect(section).toContainText("Mostrando 5 de 12");
    await expect(section.getByTestId("backup-row").first()).toContainText("backup-0.sqlite.gz");
    await section.getByRole("button", { name: "Mostrar más" }).click();
    await expect(section.getByTestId("backup-row")).toHaveCount(10);
    await expect(section).toContainText("Mostrando 10 de 12");
    await section.getByRole("button", { name: "Mostrar más" }).click();
    await expect(section.getByTestId("backup-row")).toHaveCount(12);
    await expect(section).toContainText("Mostrando 12 de 12");
    await section.getByRole("button", { name: "Mostrar menos" }).click();
    await expect(section.getByTestId("backup-row")).toHaveCount(5);
    await assertNoBrowserErrors();
  });

  test("deep scrolling keeps desktop navigation and save actions accessible", async ({ page }) => {
    const assertNoBrowserErrors = installBrowserGuards(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await openSettings(page);
    await page.getByRole("link", { name: "Modo fin de semana" }).click();
    await page.getByRole("switch", { name: /fin de semana/i }).click();
    await page.getByRole("link", { name: "Avanzado" }).click();

    const toolbar = page.locator(".settings-toolbar");
    const nav = page.locator(".settings-nav");
    await expect(toolbar).toBeInViewport();
    await expect(nav).toBeInViewport();
    await expect(toolbar.getByRole("button", { name: "Guardar cambios" })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
    const toolbarBox = await toolbar.boundingBox();
    const navBox = await nav.boundingBox();
    expect(toolbarBox?.y ?? -1).toBeGreaterThanOrEqual(0);
    expect(toolbarBox?.y ?? 99).toBeLessThanOrEqual(1);
    expect(navBox?.y ?? 0).toBeGreaterThanOrEqual((toolbarBox?.y ?? 0) + (toolbarBox?.height ?? 0) + 8);
    await assertNoBrowserErrors();
  });

  for (const viewport of viewports) {
    test(`Advanced has no horizontal overflow at ${viewport.name}`, async ({ page }) => {
      const assertNoBrowserErrors = installBrowserGuards(page);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/");
      await openSettings(page);
      await page.getByRole("link", { name: "Avanzado" }).click();
      await expect(page.getByTestId("settings-section-advanced")).toBeVisible();
      const toolbarBox = await page.locator(".settings-toolbar").boundingBox();
      expect(toolbarBox?.y ?? -1).toBeGreaterThanOrEqual(0);
      expect(toolbarBox?.y ?? 99).toBeLessThanOrEqual(1);
      await assertNoHorizontalOverflow(page, `settings advanced ${viewport.name}`);
      await assertNoBrowserErrors();
    });
  }
});

async function openSettings(page: Page) {
  await page.getByTestId("settings-button").click();
  await expect(page.getByTestId("settings-section-status")).toBeVisible();
}

function backupFixture(index: number) {
  return {
    id: `backup-${index}`,
    created_at: new Date(Date.UTC(2026, 6, 14 - index, 8, 0, 0)).toISOString(),
    path_redacted: `backup-${index}.sqlite.gz`,
    size_bytes: 1024 + index,
    source: index % 2 ? "automatic" : "manual",
    valid: index !== 6,
    validation: {
      checked_at: null,
      sqlite_integrity: index === 6 ? "failed" : "ok",
      foreign_key_violations: 0,
      sha256_matches: true,
    },
  };
}
