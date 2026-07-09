import { defineConfig, devices } from '@playwright/test'

// Tests E2E du SPA : on build puis on sert le bundle statique (comme en prod),
// et on mocke l'API (page.route) — pas besoin du backend Django pour ces tests.
const PORT = 4173

// Permet d'utiliser un Chromium déjà présent (ex. PLAYWRIGHT_CHROMIUM_PATH=
// /opt/pw-browsers/chromium) quand la révision packagée n'est pas téléchargée.
// En CI, on laisse Playwright utiliser le sien (`npx playwright install chromium`).
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: executablePath ? { executablePath } : {},
      },
    },
  ],
  webServer: {
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
