import assert from 'node:assert/strict';
import test from 'node:test';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import viteConfig from '../vite.config.ts';

const webRoot = fileURLToPath(new URL('../', import.meta.url));
const powershell = process.platform === 'win32' ? 'powershell.exe' : 'pwsh';
const hasPowerShell = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-Command', 'exit 0']).status === 0;
const psLiteral = value => `'${value.replaceAll("'", "''")}'`;

async function withCopy(run) {
  const directory = await mkdtemp(path.join(tmpdir(), 'jpass packaging '));
  try {
    const renamedWeb = path.join(directory, 'renamed frontend');
    await mkdir(path.join(renamedWeb, 'scripts'), { recursive: true });
    await cp(path.join(webRoot, 'scripts/start-demo.ps1'), path.join(renamedWeb, 'scripts/start-demo.ps1'));
    await run({ directory, renamedWeb, launcher: path.join(renamedWeb, 'scripts/start-demo.ps1') });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

function runPS(args, directory, extraEnv = {}) {
  return spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', ...args], {
    cwd: directory, env: { ...process.env, ...extraEnv }, encoding: 'utf8', timeout: 30000,
  });
}

test('launcher resolves a renamed copy from unrelated cwd without backend or dependencies', { skip: !hasPowerShell }, async () => {
  await withCopy(async ({ directory, renamedWeb, launcher }) => {
    const result = runPS(['-File', launcher, '-ValidateOnly', '-Port', '5417', '-ApiTarget', 'http://127.0.0.1:9001'], directory);
    assert.equal(result.status, 0, result.stderr);
    const plan = JSON.parse(result.stdout);
    assert.equal(plan.WebRoot, renamedWeb);
    assert.equal(plan.ViteBin, path.join(renamedWeb, 'node_modules/vite/bin/vite.js'));
    assert.equal(plan.DependenciesInstalled, false);
    assert.equal(plan.Port, 5417);
    assert.equal(plan.ApiTargetOverride, 'http://127.0.0.1:9001');
  });
});

test('launcher actually passes cwd, strict port and API target to Node and restores caller state', { skip: !hasPowerShell }, async () => {
  await withCopy(async ({ directory, renamedWeb, launcher }) => {
    const vite = path.join(renamedWeb, 'node_modules/vite/bin/vite.js');
    await mkdir(path.dirname(vite), { recursive: true });
    await writeFile(vite, 'console.log(JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2),target:process.env.JPASS_API_TARGET}));');
    const command = `. ${psLiteral(launcher)} -Port 5417 -ApiTarget 'http://127.0.0.1:9001'; @{cwd=(Get-Location).Path;target=$env:JPASS_API_TARGET} | ConvertTo-Json -Compress`;
    const result = runPS(['-Command', command], directory, { JPASS_API_TARGET: 'http://127.0.0.1:8111' });
    assert.equal(result.status, 0, result.stderr);
    const [child, caller] = result.stdout.trim().split(/\r?\n/).map(line => JSON.parse(line));
    assert.equal(child.cwd, renamedWeb);
    assert.equal(child.target, 'http://127.0.0.1:9001');
    assert.deepEqual(child.args, ['--host', '127.0.0.1', '--port', '5417', '--strictPort']);
    assert.deepEqual(caller, { cwd: directory, target: 'http://127.0.0.1:8111' });
  });
});

test('launcher surfaces missing dependencies and Vite failure without restarting other servers', { skip: !hasPowerShell }, async () => {
  await withCopy(async ({ directory, renamedWeb, launcher }) => {
    const missing = runPS(['-File', launcher], directory);
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr, /npm ci/);
    const vite = path.join(renamedWeb, 'node_modules/vite/bin/vite.js');
    await mkdir(path.dirname(vite), { recursive: true });
    await writeFile(vite, 'process.exitCode=7;');
    const failed = runPS(['-File', launcher], directory);
    assert.notEqual(failed.status, 0);
    assert.match(failed.stderr, /code 7/);
  });
});

test('launcher rejects a backend path or malformed origin before starting anything', { skip: !hasPowerShell }, async () => {
  await withCopy(async ({ directory, launcher }) => {
    for (const target of ['127.0.0.1:8000', 'file:///tmp', 'http://host/api', 'http://user:secret@host']) {
      const result = runPS(['-File', launcher, '-ValidateOnly', '-ApiTarget', target], directory);
      assert.notEqual(result.status, 0, target);
      assert.match(result.stderr, /HTTP\(S\) origin/);
    }
  });
});

test('Vite keeps full URL targets, reads dotenv and includes both HTML build entries', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'jpass vite '));
  const originalTarget = process.env.JPASS_API_TARGET;
  const originalCwd = process.cwd();
  try {
    process.chdir(directory);
    delete process.env.JPASS_API_TARGET;
    const config = viteConfig({ mode: 'development' });
    assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:8000');
    await writeFile(path.join(directory, '.env.local'), 'JPASS_API_TARGET=http://127.0.0.1:9001\n');
    assert.equal(viteConfig({ mode: 'development' }).server.proxy['/api'].target, 'http://127.0.0.1:9001');
    process.env.JPASS_API_TARGET = 'https://api.example.test';
    assert.equal(viteConfig({ mode: 'development' }).server.proxy['/api'].target, 'https://api.example.test');
    assert.equal(config.build.rolldownOptions.input.demo, path.join(webRoot, 'demo.html'));
    assert.equal(config.build.rolldownOptions.input.main, path.join(webRoot, 'index.html'));
  } finally {
    process.chdir(originalCwd);
    if (originalTarget === undefined) delete process.env.JPASS_API_TARGET;
    else process.env.JPASS_API_TARGET = originalTarget;
    await rm(directory, { recursive: true, force: true });
  }
});

test('offline CLIs write reports only under the copied frontend from unrelated cwd', async () => {
  await withCopy(async ({ directory, renamedWeb }) => {
    for (const name of ['src', 'scripts', 'fixtures']) await cp(path.join(webRoot, name), path.join(renamedWeb, name), { recursive: true });
    for (const name of ['demo-browser-route.mjs', 'replay-lazy-routing.mjs', 'verify-route.mjs']) {
      const result = spawnSync(process.execPath, [path.join(renamedWeb, 'scripts', name)], { cwd: directory, encoding: 'utf8', timeout: 30000 });
      assert.equal(result.status, 0, result.stderr);
    }
    const output = path.join(renamedWeb, '.local-run/reports');
    for (const name of ['ekispert-browser-route-result.json', 'lazy-routing-replay.json', 'route-verification-result.json']) {
      const report = JSON.parse(await readFile(path.join(output, name), 'utf8'));
      assert.ok(report && typeof report === 'object');
    }
    for (const name of ['ekispert-browser-route-result.md', 'route-verification-result.md']) assert.match(await readFile(path.join(output, name), 'utf8'), /^# /);
    await assert.rejects(readFile(path.join(directory, 'sample/route-verification-result.json')), { code: 'ENOENT' });
  });
});
