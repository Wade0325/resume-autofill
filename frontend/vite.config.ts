import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5177,
    strictPort: true,   // 埠被佔用時直接失敗，不要偷偷跳到下一個讓 dev.ps1 找不到
    // 開發時前端在 5177、後端在 8090，用 proxy 讓 /api 同源，省掉 CORS。
    // changeOrigin 要明寫 false：字串簡寫在 Vite 8 會自動打開它，Host 被改成 127.0.0.1:8090、
    // Origin 卻還是 localhost:5177，後端的同源檢查（main.same_origin_writes）就把存檔、上傳全擋掉
    proxy: { '/api': { target: 'http://127.0.0.1:8090', changeOrigin: false } },
  },
})
