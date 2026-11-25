import { defineConfig, loadEnv } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
  server: { proxy: { '/api': { target: env.JPASS_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, timeout: 600000, proxyTimeout: 600000 } } },
  build: {
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        demo: fileURLToPath(new URL('./demo.html', import.meta.url)),
      },
    },
  },
};
});
