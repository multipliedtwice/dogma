import {execFile} from 'node:child_process';
import {mkdtemp, rm, access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {promisify} from 'node:util';
import {chromium} from '@playwright/test';
import {guard} from './guard.mjs';
import {root, python, fixtureEnvironment} from './environment';

const execute = promisify(execFile);
const require = createRequire(import.meta.url);

export default async function globalSetup() {
  await guard();
  const metadata: {version: string} = require('@playwright/test/package.json');
  if (metadata.version !== '1.58.2') throw new Error('Install the exact @playwright/test 1.58.2 dependency before E2E.');
  await access(chromium.executablePath()).catch(() => {throw new Error('Pinned Chromium is missing. Run npm run e2e:install first.');});
  try {
    await execute(python, ['-c', 'import lxml, PIL, numpy, soundfile'], {cwd: root, env: fixtureEnvironment(), timeout: 15_000});
  } catch (error) {
    throw new Error('E2E Python dependencies missing. Set E2E_PYTHON to the intended interpreter; install runtime/requirements.txt.', {cause: error});
  }
  for (const args of [['node_modules/typescript/bin/tsc', '--noEmit', '-p', 'e2e/tsconfig.json'], ['node_modules/typescript/bin/tsc', '--noEmit'], ['node_modules/vite/bin/vite.js', 'build']]) {
    try {
      const result = await execute(process.execPath, args, {cwd: root, timeout: 180_000, maxBuffer: 8 * 1024 ** 2});
      if (result.stdout) process.stdout.write(result.stdout);
      if (result.stderr) process.stderr.write(result.stderr);
    } catch (error) {
      throw new Error(`E2E build/typecheck failed: node ${args.join(' ')}`, {cause: error});
    }
  }
  const directory = await mkdtemp(join(tmpdir(), 'rimskify-e2e-inputs-'));
  try {
    await execute(python, [join(root, 'e2e/runtime/make_fixtures.py'), '--output', directory], {cwd: root, env: fixtureEnvironment(), timeout: 15_000});
    process.env.RIMSKIFY_E2E_INPUTS = directory;
  } catch (error) {
    await rm(directory, {recursive: true, force: true});
    throw error;
  }
  return async () => {
    delete process.env.RIMSKIFY_E2E_INPUTS;
    await rm(directory, {recursive: true, force: true});
  };
}
