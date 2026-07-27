import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.ALPHAWAVE_BASE_URL ?? "http://127.0.0.1:8711";
const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL ?? "chrome";
const browserChannelOptions = browserChannel === "bundled" ? {} : { channel: browserChannel };

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chrome",
      use: {
        ...devices["Desktop Chrome"],
        ...browserChannelOptions,
      },
    },
  ],
});
