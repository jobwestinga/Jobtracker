import { cp, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** A fresh real ES-module graph for browser modules, including their imports.
 * The temporary package enables Node18's ESM mode without changing production.
 */
export async function importWebModule(path) {
  const directory = await mkdtemp(join(tmpdir(), 'jobtracker-web-test-'));
  try {
    await cp(new URL('../../../web/', import.meta.url), directory, { recursive: true });
    await writeFile(join(directory, 'package.json'), '{"type":"module"}\n');
    return await import(pathToFileURL(join(directory, path)).href);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}
