import test from 'node:test';
import assert from 'node:assert/strict';
import {ancestors, assessResources, competingProcesses, parseProcesses} from './guard.mjs';

const healthy = {cpuBusy: .2, load: 1, cores: 4, availableMemory: 4 * 1024 ** 3, totalMemory: 8 * 1024 ** 3, memoryPressure: false, disks: [{free: 10 * 1024 ** 3}]};

test('ps parsing keeps paths and spaces', () => {
  assert.deepEqual(parseProcesses(' 31 7 2.5 node /my project/node_modules/playwright/cli.js test\n'), [{pid: 31, ppid: 7, command: 'node /my project/node_modules/playwright/cli.js test'}]);
});
test('ancestry stops on cycles and ignores self and all ancestors', () => {
  const rows = [{pid: 20, ppid: 10, command: 'node e2e/guard.mjs'}, {pid: 10, ppid: 1, command: 'npm run e2e'}, {pid: 1, ppid: 10, command: 'sh -c playwright test'}];
  assert.deepEqual([...ancestors(rows, 20)], [20, 10, 1]);
  assert.deepEqual(competingProcesses(rows, 20), []);
});
test('siblings and descendants still compete', () => {
  const rows = [
    {pid: 20, ppid: 10, command: 'node e2e/guard.mjs'},
    {pid: 10, ppid: 1, command: 'npm run e2e'},
    {pid: 30, ppid: 10, command: 'node /work/node_modules/playwright/cli.js test'},
    {pid: 31, ppid: 20, command: 'python3 /work/e2e/runtime/serve_fixture.py'},
    {pid: 32, ppid: 1, command: 'node run-e2e.mjs'},
    {pid: 33, ppid: 1, command: '/Applications/Cypress.app/Contents/MacOS/Cypress'},
    {pid: 34, ppid: 1, command: 'npm run e2e:headed'},
    {pid: 35, ppid: 1, command: 'node /work/node_modules/vite/bin/vite.js build'},
  ];
  assert.deepEqual(competingProcesses(rows, 20).map(row => row.pid), [30, 31, 32, 33, 34, 35]);
});
test('ordinary app processes do not compete', () => {
  assert.deepEqual(competingProcesses([{pid: 1, ppid: 0, command: 'launchd'}, {pid: 2, ppid: 1, command: 'node server.js'}, {pid: 3, ppid: 1, command: 'python runtime/server.py --port 0'}], 4), []);
});
test('safe resource sample passes', () => assert.deepEqual(assessResources(healthy), []));
test('each unsafe resource refuses with an action', () => {
  for (const change of [{cpuBusy: .95}, {load: 10}, {availableMemory: 100}, {memoryPressure: true}, {disks: [{free: 100}]}]) {
    assert.ok(assessResources({...healthy, ...change}).length > 0, JSON.stringify(change));
  }
});
test('low memory fraction refuses even above absolute minimum', () => {
  assert.ok(assessResources({...healthy, availableMemory: 2 * 1024 ** 3, totalMemory: 64 * 1024 ** 3}).some(message => message.includes('memory')));
});

test('quoted paths and scoped Playwright package still compete', () => {
  const rows = [
    {pid: 20, ppid: 1, command: 'node e2e/guard.mjs'},
    {pid: 21, ppid: 1, command: 'node "/work/node_modules/@playwright/test/cli.js" test'},
    {pid: 22, ppid: 1, command: 'python "/work with spaces/e2e/runtime/serve_fixture.py"'},
    {pid: 23, ppid: 1, command: 'python verification/browser_playback.py'},
  ];
  assert.deepEqual(competingProcesses(rows, 20).map(row => row.pid), [21, 22, 23]);
});
