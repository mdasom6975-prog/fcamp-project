import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api 요청은 FastAPI(기본 8000, API_PORT 로 변경 가능)로 전달한다.
const apiPort = process.env.API_PORT || '8000'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': `http://127.0.0.1:${apiPort}` } },
})
