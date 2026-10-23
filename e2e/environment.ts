import {fileURLToPath} from 'node:url';

export const root = fileURLToPath(new URL('../', import.meta.url));
export const python = process.env.E2E_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

export function fixtureEnvironment(): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {...process.env, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1'};
  for (const name of Object.keys(environment)) {
    if (/^(?:RIMSKY_RESOURCE_DIR|OPENAI_API_KEY|CODEX_API_KEY|AZURE_OPENAI_API_KEY|PYTHONPATH|PYTHONHOME)$/.test(name)) delete environment[name];
  }
  return environment;
}
