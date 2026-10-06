/**
 * 模型设置表单的纯逻辑（`site/src/lib/settings.js`）。
 *
 * 这里盯的是**保存时写了什么**，不是控件长什么样。最要紧的一条：
 * `PUT /api/settings` 是整体替换，而那几个字段在界面上显示的是**生效值**
 * （可能来自 `.env.local` 或预设默认）——把没动过的字段也写回去，等于把它们
 * **冻进文件**、从此架空环境变量那一层。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {
  ROLE_ROWS,
  buildPayload,
  describeResult,
  formFromView,
  knownProviders,
  newProvider,
  sourceLabel,
  validateForm,
} from '../src/lib/settings.js';

const VIEW = {
  file: '/home/me/.local/share/ai-note/settings.json',
  file_ok: true,
  applies: '立刻生效',
  roles: {
    extract: {provider: {value: 'dashscope', source: 'default'},
              model: {value: 'qwen-vl-max', source: 'default'},
              key: {set: true, hint: '…ef01', source: 'env'}},
    segmenter: {provider: {value: 'deepseek', source: 'env'},
                model: {value: 'deepseek-v4', source: 'env'}},
    judge: {provider: {value: 'openai', source: 'settings'},
            model: {value: 'gpt-4o', source: 'settings'}},
    brief: {provider: {value: 'dashscope', source: 'default'},
            model: {value: 'qwen-max', source: 'default'}},
  },
  providers: {
    dashscope: {preset: true, base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                model: 'qwen-vl-max', key_env: 'DASHSCOPE_API_KEY',
                key: {set: true, hint: '…ef01', source: 'env'}},
    deepseek: {preset: true, base_url: 'https://api.deepseek.com', model: 'deepseek-v4',
               key_env: 'DEEPSEEK_API_KEY', key: {set: false, hint: null, source: 'default'}},
    openai: {preset: false, base_url: 'https://api.openai.com/v1', model: 'gpt-4o',
             key_env: null, key: {set: true, hint: '…1234', source: 'settings'}},
  },
};

test('四个角色都在，且每一项都带着它来自哪一层', () => {
  const form = formFromView(VIEW);
  assert.deepEqual(Object.keys(form.roles).sort(),
                   ['brief', 'extract', 'judge', 'segmenter']);
  assert.equal(form.roles.segmenter.sources.provider, 'env');
  assert.equal(form.roles.judge.sources.provider, 'settings');
  assert.equal(sourceLabel('settings'), '来自设置文件');
  assert.equal(sourceLabel('env'), '来自 .env.local');
  assert.equal(sourceLabel('default'), '来自预设默认');
  assert.equal(ROLE_ROWS.length, 4);
});

test('没动过的字段一个字都不写进文件（否则会把生效值冻进文件、架空环境变量）', () => {
  const form = formFromView(VIEW);
  const payload = buildPayload(form);
  // extract/segmenter/brief 的 provider 分别来自 default/env/default —— 都没动过
  assert.equal(payload.roles.extract, undefined);
  assert.equal(payload.roles.segmenter, undefined);
  assert.equal(payload.roles.brief, undefined);
  // judge 的 provider/model 本来就来自设置文件 → 保留
  assert.deepEqual(payload.roles.judge, {provider: 'openai', model: 'gpt-4o'});
});

test('动过的字段才写；预设 provider 不写进文件', () => {
  const form = formFromView(VIEW);
  form.roles.extract.model = 'qwen-vl-plus';
  form.roles.extract.touched.model = true;
  const payload = buildPayload(form);

  assert.deepEqual(payload.roles.extract, {model: 'qwen-vl-plus'});
  assert.equal(payload.roles.extract.provider, undefined, '没动过的 provider 不许跟着写');
  // 三家预设都不许出现在文件里（写进去就等于把预设抄成自定义）
  assert.deepEqual(Object.keys(payload.providers), ['openai']);
});

test('密钥：不填＝省略（不改动）、填了＝写、清空＝显式 null', () => {
  const untouched = buildPayload(formFromView(VIEW));
  assert.equal('api_key' in untouched.providers.openai, false, '不填就是省略，服务端保持原值');

  const typed = formFromView(VIEW);
  typed.providers.openai.key_input = 'sk-new-5678';
  assert.equal(buildPayload(typed).providers.openai.api_key, 'sk-new-5678');

  const cleared = formFromView(VIEW);
  cleared.providers.openai.key_clear = true;
  assert.equal(buildPayload(cleared).providers.openai.api_key, null, '清空要显式 null');
});

test('新加的自定义 provider 会连着端点一起写进去', () => {
  const form = formFromView(VIEW);
  form.providers.ollama = newProvider('ollama');
  form.providers.ollama.base_url = 'http://127.0.0.1:11434/v1';
  form.providers.ollama.key_input = 'not-needed';
  form.roles.judge.provider = 'ollama';
  form.roles.judge.touched.provider = true;

  const payload = buildPayload(form);
  assert.deepEqual(payload.providers.ollama,
                   {base_url: 'http://127.0.0.1:11434/v1', api_key: 'not-needed'});
  assert.equal(payload.roles.judge.provider, 'ollama');
  assert.deepEqual(knownProviders(form).custom, ['openai', 'ollama']);
});

test('提交前自检说清是哪一行不对（服务端仍会再验一遍）', () => {
  assert.deepEqual(validateForm(formFromView(VIEW)), []);

  const undefinedProvider = formFromView(VIEW);
  undefinedProvider.roles.judge.provider = 'nope';
  assert.ok(validateForm(undefinedProvider)[0].includes('还没有定义'));

  const noBaseUrl = formFromView(VIEW);
  noBaseUrl.providers.openai.base_url = '';
  noBaseUrl.roles.judge.provider = 'openai';
  assert.ok(validateForm(noBaseUrl).some((p) => p.includes('缺 base_url')));

  const noModel = formFromView(VIEW);
  noModel.roles.extract.provider = 'openai';
  noModel.roles.extract.model = '';
  noModel.providers.openai.model = '';
  assert.ok(validateForm(noModel).some((p) => p.includes('没给模型')));
});

test('失败时说的话来自服务，不是界面自己编的', () => {
  assert.deepEqual(describeResult(200, {}), {ok: true, text: '已保存，立刻生效。'});
  const bad = describeResult(400, {error: {message: 'provider 装不起来', hint: '一个字节都没写'}});
  assert.equal(bad.ok, false);
  assert.ok(bad.text.includes('provider 装不起来'));
  assert.ok(bad.text.includes('一个字节都没写'));
});
