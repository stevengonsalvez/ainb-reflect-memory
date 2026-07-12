// Launch `reflect serve` against a FRESH copy of the fixture KB so the e2e
// suite can mutate freely (archive, confidence, compress) without ever
// touching the committed fixture or the real ~/.learnings.
import { cpSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');
const fixture = join(here, 'fixture-kb');
const port = process.env.PORT || '8961';

const kb = mkdtempSync(join(tmpdir(), 'reflect-e2e-kb-'));
cpSync(fixture, kb, { recursive: true });
console.log(`[serve-fixture] serving copy ${kb} on :${port}`);

const child = spawn('uv', ['run', 'reflect', 'serve', '--port', port, '--repo', kb], {
  cwd: repoRoot,
  stdio: 'inherit',
});
for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => child.kill(sig));
child.on('exit', (code) => process.exit(code ?? 0));
