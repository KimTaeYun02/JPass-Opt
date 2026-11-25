import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from 'vite';
import { createTravelApi } from '../src/demo-api.ts';
import { harness } from './demo-harness.mjs';

test('Vite embeds the deployment base and the UI passes it to the real HTTP adapter', async () => {
  const output = await mkdtemp(join(tmpdir(), 'jpass-build-'));
  const previous = process.env.VITE_API_BASE;
  const base = 'https://backend.example.com/api';
  process.env.VITE_API_BASE = base;
  try {
    // Build the same env module imported by demo.ts, with no private dotenv files.
    await build({
      configFile: false, envDir: false, logLevel: 'silent',
      build: { outDir: output, lib: {
        entry: fileURLToPath(new URL('../src/demo-config.ts', import.meta.url)),
        formats: ['es'], fileName: () => 'config.mjs',
      } },
    });
    const { travelApiBase } = await import(pathToFileURL(join(output, 'config.mjs')).href);
    assert.equal(travelApiBase, base);
    const urls = [], passedBases = [];
    const client = createTravelApi(travelApiBase, async (url) => {
      urls.push(url);
      return new Response(JSON.stringify({ textQueries: null, results: [] }));
    });
    const app = harness({ mode: 'api', client, apiBase: travelApiBase, onApiBase: (value) => passedBases.push(value) });
    assert.deepEqual(passedBases, [base]);
    assert.deepEqual(urls, []);
    app.click('search');
    app.filter('Tokyo');
    app.searchKey('Enter');
    await app.settle();
    assert.deepEqual(urls, [`${base}/places/search`]);
  } finally {
    if (previous === undefined) delete process.env.VITE_API_BASE;
    else process.env.VITE_API_BASE = previous;
    await rm(output, { recursive: true, force: true });
  }
});
