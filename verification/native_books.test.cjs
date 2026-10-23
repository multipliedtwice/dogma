const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function controller() {
  const file = path.join(__dirname, '../src/lib/nativeBooks.ts');
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  }).outputText;
  const timers = new Map();
  let sequence = 0;
  const output = {exports: {}};
  new Function('module', 'exports', 'setTimeout', 'clearTimeout', source)(output, output.exports,
    (callback, delay) => { const id = ++sequence; timers.set(id, {callback, delay}); return id; },
    id => timers.delete(id));
  return {...output.exports, timers, tick: async () => {
    const entry = timers.entries().next().value;
    assert.ok(entry, 'Expected a scheduled queue drain');
    timers.delete(entry[0]);
    entry[1].callback();
    await new Promise(resolve => setImmediate(resolve));
  }};
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('native book queue listens before draining and keeps events received during opening', async () => {
  const runtime = controller();
  let notify;
  let finishOpening;
  let registrations = 0;
  let unregistrations = 0;
  const pending = ['startup.epub'];
  const opened = [];
  const stop = runtime.bindNativeBooks({
    listen: async callback => { registrations++; notify = callback; return () => unregistrations++; },
    process: async () => {
      assert.equal(registrations, 1);
      const batch = pending.splice(0);
      opened.push(batch);
      if (batch.includes('startup.epub')) await new Promise(resolve => { finishOpening = resolve; });
      return true;
    },
    error: reason => { throw reason; },
  });
  assert.deepEqual(opened, []);
  await settle();
  await runtime.tick();
  pending.push('second.fb2');
  notify();
  notify();
  assert.equal(runtime.timers.size, 0);
  finishOpening();
  await settle();
  await runtime.tick();
  assert.deepEqual(opened, [['startup.epub'], ['second.fb2']]);
  assert.equal(runtime.timers.size, 0);
  stop();
  assert.equal(unregistrations, 1);
});

test('busy native actions retain queued books and retry without overlapping work', async () => {
  const runtime = controller();
  let blocked = true;
  const pending = ['queued.epub'];
  const opened = [];
  const stop = runtime.bindNativeBooks({
    listen: async () => () => {},
    process: async () => {
      if (blocked) return false;
      opened.push(...pending.splice(0));
      return true;
    },
    error: reason => { throw reason; },
  });
  await settle();
  await runtime.tick();
  assert.deepEqual(pending, ['queued.epub']);
  assert.equal([...runtime.timers.values()][0].delay, 250);
  blocked = false;
  await runtime.tick();
  assert.deepEqual(opened, ['queued.epub']);
  stop();
});

test('disposing before listener registration cannot consume startup books', async () => {
  const runtime = controller();
  let registered;
  let unregistered = 0;
  let drains = 0;
  const stop = runtime.bindNativeBooks({
    listen: () => new Promise(resolve => { registered = resolve; }),
    process: async () => { drains++; return true; },
    error: reason => { throw reason; },
  });
  stop();
  registered(() => unregistered++);
  await settle();
  assert.equal(unregistered, 1);
  assert.equal(drains, 0);
  assert.equal(runtime.timers.size, 0);
});

test('failed native listener registration reports failure without draining', async () => {
  const runtime = controller();
  const errors = [];
  const stop = runtime.bindNativeBooks({
    listen: async () => { throw new Error('listener failed'); },
    process: async () => { assert.fail('Must register before draining'); },
    error: reason => errors.push(reason.message),
  });
  await settle();
  assert.deepEqual(errors, ['listener failed']);
  assert.equal(runtime.timers.size, 0);
  stop();
});
