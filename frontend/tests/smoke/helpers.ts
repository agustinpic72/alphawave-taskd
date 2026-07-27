import { expect, type APIRequestContext, type Page, type Response } from "@playwright/test";

export const smokePrefix = "[M17 SMOKE]";

type Task = {
  id: string;
  title: string;
  status: string;
};

export function installBrowserGuards(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const serverErrors: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });
  page.on("response", (response: Response) => {
    const url = response.url();
    if (url.includes("/api/") && response.status() >= 500) {
      serverErrors.push(`${response.status()} ${url}`);
    }
  });

  return async function assertNoBrowserErrors() {
    expect(consoleErrors, "console.error output").toEqual([]);
    expect(pageErrors, "uncaught page errors").toEqual([]);
    expect(serverErrors, "critical API 5xx responses").toEqual([]);
  };
}

export async function createSmokeTask(
  request: APIRequestContext,
  title: string,
  overrides: Record<string, unknown> = {},
) {
  const response = await request.post("/api/tasks", {
    data: {
      title,
      scope: "Personal",
      auto_classify: false,
      ...overrides,
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as Task;
}

export async function cleanupSmokeTasks(request: APIRequestContext) {
  for (const status of ["active", "completed", "deleted"]) {
    const response = await request.get(`/api/tasks?status=${status}`);
    if (!response.ok()) continue;
    const payload = (await response.json()) as { tasks: Task[] };
    for (const task of payload.tasks.filter((item) => item.title.includes(smokePrefix))) {
      await deleteTaskPermanently(request, task);
    }
  }
}

async function deleteTaskPermanently(request: APIRequestContext, task: Task) {
  if (task.status !== "deleted") {
    await request.delete(`/api/tasks/${task.id}`).catch(() => null);
  }
  await request.delete(`/api/tasks/${task.id}/permanent`).catch(() => null);
}

export async function searchTasks(page: Page, query: string) {
  await page.getByRole("textbox", { name: /buscar tareas/i }).fill(query);
}

export function taskCard(page: Page, title: string) {
  return page.locator('[data-testid="task-card"]').filter({ hasText: title }).first();
}

export async function assertNoHorizontalOverflow(page: Page, label = "page") {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    const viewportWidth = window.innerWidth;
    const maxScrollWidth = Math.max(root.scrollWidth, body.scrollWidth);
    const wideElements = Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        if (style.position === "fixed") return false;
        if (hasHorizontalScrollAncestor(element)) return false;
        const rect = element.getBoundingClientRect();
        return rect.width > viewportWidth + 2 || rect.right > viewportWidth + 2;
      })
      .slice(0, 5)
      .map((element) => ({
        tag: element.tagName.toLowerCase(),
        className: String(element.className),
        testId: element.getAttribute("data-testid"),
        width: Math.round(element.getBoundingClientRect().width),
        right: Math.round(element.getBoundingClientRect().right),
      }));
    return {
      viewportWidth,
      maxScrollWidth,
      wideElements,
    };

    function hasHorizontalScrollAncestor(element: HTMLElement) {
      let current: HTMLElement | null = element.parentElement;
      while (current && current !== document.body) {
        const style = window.getComputedStyle(current);
        if ((style.overflowX === "auto" || style.overflowX === "scroll") && current.scrollWidth > current.clientWidth) {
          return true;
        }
        current = current.parentElement;
      }
      return false;
    }
  });
  expect(overflow.maxScrollWidth, `${label} document overflow: ${JSON.stringify(overflow)}`).toBeLessThanOrEqual(overflow.viewportWidth + 2);
  expect(overflow.wideElements, `${label} overflowing elements`).toEqual([]);
}
