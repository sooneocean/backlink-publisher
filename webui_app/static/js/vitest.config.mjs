import { defineConfig } from 'vitest/config';
import { resolve } from 'path';

const here = resolve(import.meta.dirname || '.');
const repoRoot = resolve(here, '..', '..', '..');

export default defineConfig({
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
  test: {
    environment: 'happy-dom',
    include: [resolve(repoRoot, 'tests/js/**/*.test.{js,mjs}')],
    globals: false,
  },
});
