import { mkdir } from 'node:fs/promises';

// Keep generated reports inside the frontend, independent of the caller's cwd.
export async function reportPath(name) {
  const directory = new URL('../.local-run/reports/', import.meta.url);
  await mkdir(directory, { recursive: true });
  return new URL(name, directory);
}
