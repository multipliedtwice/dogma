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
const {stressWords, withStress, formsPreview, markWord, ACUTE} = load('src/lib/nameStress.ts');

test('each multi-vowel name word offers one option per vowel, with the guess selected', () => {
  const words = stressWords('Эрдамо' + ACUTE + 'н Бела' + ACUTE + 'р');
  assert.deepEqual(words.map(w => w.plain), ['Эрдамон', 'Белар']);
  assert.deepEqual(words[1].options.map(i => markWord('Белар', i)), ['Бе' + ACUTE + 'лар', 'Бела' + ACUTE + 'р']);
  assert.equal(markWord('Белар', words[1].chosen), 'Бела' + ACUTE + 'р');
  assert.equal(stressWords('Вел (Велнари' + ACUTE + 'н)').length, 1);  // single-vowel Вел has no choice
});

test('changing one word keeps every other character, alias and mark', () => {
  const name = 'Сакуа' + ACUTE + 'л Хан (Ку' + ACUTE + 'ра-Пи' + ACUTE + 'ра)';
  const words = stressWords(name);
  const kura = words.find(w => w.plain === 'Кура');
  const changed = withStress(name, kura.index, 3);
  assert.equal(changed, 'Сакуа' + ACUTE + 'л Хан (Кура' + ACUTE + '-Пи' + ACUTE + 'ра)');
  assert.equal(changed.split(ACUTE).join(''), name.split(ACUTE).join(''));
});

test('form preview shows the consequence across cases', () => {
  assert.deepEqual(formsPreview('Белар', 3), ['Бела' + ACUTE + 'р', 'Бела' + ACUTE + 'ра', 'Бела' + ACUTE + 'ру', 'Бела' + ACUTE + 'ром']);
  assert.deepEqual(formsPreview('Стигга', 2), ['Сти' + ACUTE + 'гга', 'Сти' + ACUTE + 'ггы', 'Сти' + ACUTE + 'гге', 'Сти' + ACUTE + 'ггу']);
});
