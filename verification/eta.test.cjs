const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function load(file) {
  const filename = path.resolve(__dirname, '..', file);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    fileName: filename,
  }).outputText;
  const module = {exports: {}};
  new Function('require', 'module', 'exports', source)(require, module, module.exports);
  return module.exports;
}
const {addSample, remainingSeconds, formatRemaining} = load('src/lib/eta.ts');

test('remaining time extrapolates the observed rate only after a minute of samples', () => {
  let samples = addSample([], 10, 0);
  samples = addSample(samples, 20, 30_000);
  assert.equal(remainingSeconds(samples, 100), null);
  samples = addSample(samples, 40, 120_000); // 30 units in 120 s
  assert.equal(Math.round(remainingSeconds(samples, 100)), 240);
});

test('a stall lowers the rate instead of freezing the estimate', () => {
  let samples = addSample([], 0, 0);
  samples = addSample(samples, 60, 60_000);
  const before = remainingSeconds(samples, 120);
  samples = addSample(samples, 60, 240_000);
  assert.ok(remainingSeconds(samples, 120) > before);
});

test('old samples leave the window and labels are localized', () => {
  const samples = addSample([{at: 0, done: 0}], 5, 20 * 60 * 1000);
  assert.equal(samples.length, 1);
  assert.equal(formatRemaining('en', 30), 'under a minute');
  assert.equal(formatRemaining('ru', 3900), '≈1 ч 5 мин');
});
