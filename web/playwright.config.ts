/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
// Browser tests drive the real app in the Microsoft Edge that is installed on this machine, so no
// browser download is needed. Servers are started by the tester, not by this file:
//   API:  python scripts/run_local.py --reset --data <name> --port $E2E_API_PORT --demo
//   Web:  VITE_API_URL=http://localhost:$E2E_API_PORT node node_modules/vite/bin/vite.js --port $E2E_WEB_PORT
import { defineConfig } from '@playwright/test';

const web = process.env.E2E_WEB_PORT ?? '5173';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: process.env.E2E_REPORT ?? 'e2e-report.json' }]],
  outputDir: process.env.E2E_SCREENS ?? 'e2e-results',
  use: {
    baseURL: `http://localhost:${web}`,
    channel: 'msedge',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'off',
    viewport: { width: 1280, height: 800 },
  },
});
