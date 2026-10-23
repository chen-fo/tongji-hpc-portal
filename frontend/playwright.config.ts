import { defineConfig, devices } from '@playwright/test';
import { randomBytes } from 'node:crypto';

process.env.PORTAL_E2E_PASSWORD ||= randomBytes(24).toString('base64url');
const webPort = Number(process.env.PORTAL_E2E_WEB_PORT || 15173);
const apiPort = Number(process.env.PORTAL_E2E_API_PORT || 18000);
const webURL = 'http://127.0.0.1:' + webPort;
const apiURL = 'http://127.0.0.1:' + apiPort;

export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1,
  use: { baseURL: webURL, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: '../backend/.venv/bin/python ../backend/tests/e2e_server.py', url: apiURL + '/api/health', env: { PORTAL_E2E_API_PORT: String(apiPort), PORTAL_E2E_ORIGIN: webURL }, reuseExistingServer: false, timeout: 30_000 },
    { command: 'npm run dev -- --port ' + webPort, url: webURL, env: { PORTAL_DEV_API_TARGET: apiURL }, reuseExistingServer: false, timeout: 30_000 },
  ],
});
