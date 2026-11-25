import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 远程开发配置：前端跑本地，API 走线上服务器，不需要在本机装后端/数据库。
//
// 用法：npm run dev:remote
//
// 原理：
//   1. .env.remote 把 VITE_API_BASE_URL 设成 http://localhost:5173（dev server 自己）
//      → 浏览器发出的请求与页面同源，不触发 CORS
//   2. 下面的 proxy 把 /api 转发到线上 nginx
//      → 转发发生在 Node 侧，服务器到服务器，同样不受 CORS 限制
//   3. 不做路径重写：线上 nginx 的 /api/ 是直接反代，代码里已经带 /api/v1 前缀
//      （根目录 vite.config.ts 里那个 /api → /api/v1 的 rewrite 会导致 /api/v1/v1，是错的）
//
// 注意：这是开发配置，不参与生产构建。生产仍用 vite.config.ts。

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://8.153.183.119:8080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://8.153.183.119:8080',
        changeOrigin: true,
      },
    },
  },
})
