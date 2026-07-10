import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Version de l'app, injectée à la compilation dans la constante globale
// __APP_VERSION__ (affichée dans l'UI). En release, la CI passe la version
// calculée (tag semver) via APP_VERSION ; en local, on retombe sur package.json.
const pkg = JSON.parse(
  readFileSync(fileURLToPath(new URL('./package.json', import.meta.url)), 'utf-8'),
)
const appVersion = process.env.APP_VERSION || pkg.version

// L'app est buildée en statique puis servie par Django/WhiteNoise (mono-conteneur).
// En dev, `npm run dev` sert sur :5173 et proxifie l'API vers Django :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(appVersion) },
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
})
