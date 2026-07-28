import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 開發時前端在 5173、後端在 8090，用 proxy 讓 /api 同源，省掉 CORS
    proxy: { '/api': 'http://127.0.0.1:8090' },
  },
})
