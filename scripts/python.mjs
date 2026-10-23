import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';

export const python = (env = process.env, platform = process.platform) =>
  env.RIMSKY_PYTHON || (platform === 'win32' ? 'python' : 'python3');

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const interpreter = python();
  const result = spawnSync(interpreter, process.argv.slice(2), {stdio: 'inherit'});
  if (result.error) {
    console.error(`Could not start RIMSKY_PYTHON=${interpreter}: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}
