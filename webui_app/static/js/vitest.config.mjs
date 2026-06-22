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
    // include MUST stay a RELATIVE glob (resolved against the config root,
    // webui_app/static/js). Do NOT switch back to an absolute resolve(repoRoot, …)
    // path: Vitest 4 applies this project's `environment` only to files whose
    // include match is relative-to-root. An absolute include pointing outside the
    // root still collects the files but runs them under the default `node`
    // environment, so every DOM test silently fails with `document is not defined`.
    include: ['../../../tests/js/**/*.test.{js,mjs}'],
    globals: false,
  },
});
