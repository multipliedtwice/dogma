import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// training-work/ holds large, constantly changing model and audio checkpoints
// (and their own index.html pages); scanning or watching it stalls dev start.
export default defineConfig({ base: './', plugins: [react()], clearScreen: false, optimizeDeps: { entries: ['index.html'] }, server: { host: '127.0.0.1', port: 1420, strictPort: true, watch: { ignored: ['**/training-work/**', '**/manual-labeling/**', '**/resources/music/**'] } }, build: { target: 'es2022' } });
