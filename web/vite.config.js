import { resolve } from 'node:path';
import { defineConfig } from 'vite';

export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
    // Three pages: the creation app, sign-in, and the Admin portal.
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        login: resolve(__dirname, 'login.html'),
        admin: resolve(__dirname, 'admin.html'),
      },
    },
  },
});
