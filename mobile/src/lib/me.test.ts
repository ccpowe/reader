import assert from 'node:assert/strict';
import { test } from 'node:test';

import { displayNameInput, engineLabel, localeLabel, passwordProblem } from './me.ts';

test('labels for the translation setting', () => {
  assert.equal(localeLabel('zh-CN'), '简体中文');
  assert.equal(localeLabel('zh-cn'), '简体中文');
  assert.equal(localeLabel('pt-BR'), 'pt-BR', 'an unlisted tag shows as is');
  const engines = [
    { id: 'deepseek', label: 'DeepSeek', available: true },
    { id: 'codex', label: 'Codex', available: false },
  ];
  assert.equal(engineLabel({ engine_id: 'deepseek', engines }), 'DeepSeek');
  assert.equal(engineLabel({ engine_id: 'codex', engines }), 'Codex（暂不可用）');
  assert.equal(engineLabel({ engine_id: '', engines: [] }), '未选择');
});

test('profile and password rules', () => {
  assert.equal(displayNameInput('  OK '), 'OK');
  assert.equal(displayNameInput('   '), null);
  assert.equal(displayNameInput('x'.repeat(130))?.length, 120);
  assert.equal(passwordProblem('', 'abcdefgh', 'abcdefgh')?.field, 'current');
  assert.equal(passwordProblem('old', 'short', 'short')?.field, 'next');
  assert.equal(passwordProblem('samesame', 'samesame', 'samesame')?.field, 'next');
  assert.equal(passwordProblem('old', 'abcdefgh', 'abcdefgx')?.field, 'confirm');
  assert.equal(passwordProblem('old', 'abcdefgh', 'abcdefgh'), null);
});
