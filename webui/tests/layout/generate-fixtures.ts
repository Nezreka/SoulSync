import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

/** Re-render the React markup the probes measure, so it is never stale. */
export default function globalSetup() {
  // Call vitest's own entry directly. Spawning npx needs a shell on Windows
  // and EINVALs without one.
  execFileSync(
    process.execPath,
    [resolve(process.cwd(), 'node_modules/vitest/vitest.mjs'), 'run', 'src/test/layout-fixtures.test.tsx'],
    { stdio: 'inherit' },
  );
}
