const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawnSync} = require('node:child_process');

const script = path.join(__dirname, '..', 'scripts', 'python.mjs');

test('npm scripts use RIMSKY_PYTHON before the platform default', async () => {
  const {python} = await import(pathToFileURL(script).href);
  assert.equal(python({RIMSKY_PYTHON: '/venv/bin/python'}, 'darwin'), '/venv/bin/python');
  assert.equal(python({}, 'darwin'), 'python3');
  assert.equal(python({}, 'linux'), 'python3');
  assert.equal(python({}, 'win32'), 'python');
});

test('the resolver passes arguments and the exit status through', () => {
  const result = spawnSync(process.execPath, [script, '-e', 'process.exit(7)'], {env: {...process.env, RIMSKY_PYTHON: process.execPath}});
  assert.equal(result.status, 7);
  const missing = spawnSync(process.execPath, [script, '--version'], {env: {...process.env, RIMSKY_PYTHON: path.join(__dirname, 'no-such-python')}, encoding: 'utf8'});
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /Could not start RIMSKY_PYTHON=/);
});
