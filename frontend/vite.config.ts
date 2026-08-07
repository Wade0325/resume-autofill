import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,   // 埠被佔用時直接失敗，不要偷偷跳到 5174 讓 dev.ps1 找不到
    // 開發時前端在 5173、後端在 8090，用 proxy 讓 /api 同源，省掉 CORS
    proxy: { '/api': 'http://127.0.0.1:8090' },
  },
})
