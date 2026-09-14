import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './scripts/browser',
  testMatch: '**/*.spec.mjs',
  timeout: 30_000,
  fullyParallel: false,
  // The development workstation shares memory with the IDE and device tooling.
  // Keep local runs bounded too; see ../AGENTS.md for cross-task serialization.
  workers: 1,
  use: {
    browserName: 'chromium',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    viewport: { width: 390, height: 844 },
  },
});
