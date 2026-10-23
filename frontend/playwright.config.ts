import { defineConfig, devices } from '@playwright/test';
import { randomBytes } from 'node:crypto';

process.env.PORTAL_E2E_PASSWORD ||= randomBytes(24).toString('base64url');

export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: '../backend/.venv/bin/python ../backend/tests/e2e_server.py', url: 'http://127.0.0.1:8000/api/health', reuseExistingServer: false, timeout: 30_000 },
    { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: false, timeout: 30_000 },
  ],
});
