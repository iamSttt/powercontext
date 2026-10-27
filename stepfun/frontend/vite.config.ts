import { defineConfig } from 'vite';

// Dev server proxies API and model files to the local Node backend,
// which talks to seekdb via seekdb-js and serves the ONNX models.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:3001',
      '/models': 'http://127.0.0.1:3001',
    },
  },
  optimizeDeps: {
    exclude: ['@huggingface/transformers'],
  },
});
