import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// L'app est buildée en statique puis servie par Django/WhiteNoise (mono-conteneur).
// En dev, `npm run dev` sert sur :5173 et proxifie l'API vers Django :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
})
