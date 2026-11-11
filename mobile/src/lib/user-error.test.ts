import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError, type ApiProblem } from './api/errors.ts';
import { discoveryMessage, errorMessage, problemMessage } from './user-error.ts';

test('known codes get their own sentence', () => {
  assert.equal(problemMessage({ kind: 'http', status: 401, code: 'invalid_credentials', message: 'x' }), '邮箱或密码不对。');
  assert.equal(problemMessage({ kind: 'http', status: 409, code: 'email_taken', message: 'x' }), '这个邮箱已经注册过，可以直接登录。');
});

test('rate limits say how long to wait', () => {
  assert.equal(problemMessage({ kind: 'http', status: 429, code: 'too_many_attempts', message: '', retryAfter: 42 }), '操作太频繁，约 42 秒后再试。');
  assert.equal(discoveryMessage({ kind: 'rate-limited', retryAfter: 150 }), '尝试次数过多，约 3 分钟后再试。');
  assert.equal(discoveryMessage({ kind: 'rate-limited' }), '尝试次数过多，请稍后再试。');
});

test('field errors name the field', () => {
  const problem: ApiProblem = { kind: 'http', status: 422, code: 'invalid_request', message: '', fields: [{ field: 'body.password', message: 'too short' }] };
  assert.equal(problemMessage(problem), '密码需要 8 到 128 个字符。');
});

test('unknown errors fall back without showing codes', () => {
  assert.equal(problemMessage({ kind: 'http', status: 400, code: 'weird_thing', message: 'internal' }), '操作没有成功，请稍后再试。');
  assert.equal(problemMessage({ kind: 'http', status: 500, code: 'boom', message: '' }), '服务器暂时出错了，请稍后再试。');
  assert.equal(errorMessage(new Error('x')), '出了点问题，请稍后再试。');
  assert.equal(errorMessage(new ApiError({ kind: 'network' })), '连不上服务器。检查网络后再试。');
});

test('a timeout on a change says the result is unknown', () => {
  assert.match(problemMessage({ kind: 'timeout' }), /结果未知/);
});

test('discovery and address problems', () => {
  assert.equal(discoveryMessage({ kind: 'address', problem: 'protocol' }), '地址要以 http:// 或 https:// 开头。');
  assert.equal(discoveryMessage({ kind: 'redirected', to: 'https://b.example' }), '这个地址会跳转到 https://b.example。请直接填写跳转后的地址。');
});
