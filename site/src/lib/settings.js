/**
 * 模型设置表单的纯逻辑（一行 React 都没有；组件那一半在 `components/ModelSettings.js`）。
 *
 * 为什么值得单独一层：这个表单最容易写错的地方不是画控件，而是**保存时写了什么**。
 * `PUT /api/settings` 是**整体替换**，而那份文件的语义是「**只写用户真正设过的字段**，
 * 缺字段＝回落下一层」。所以：
 *
 *   · 界面上显示的 provider／model 是**生效值**（可能来自 `.env.local` 或预设默认）；
 *   · 把这些生效值统统写回文件，就等于**从今往后把它们冻进文件**、把环境变量那一层架空；
 *   · 所以每个字段都要带「用户是不是动过它」＋「它本来是不是就来自设置文件」两个标记，
 *     只有其中之一为真才写。
 *
 * 密钥另有一条：**不填＝不改动**（省略 `api_key`），**清空＝显式 `null`**。
 * “页面加载一次再保存一次就把密钥抹掉”是这类设置页最常见的静默数据丢失。
 */

/** 四个角色的中文名与顺序（界面按它画表；顺序固定，免得每次渲染都换位置）。 */
export const ROLE_ROWS = [
  {role: 'extract', label: '抽取', note: '题面转录、红笔语义'},
  {role: 'segmenter', label: '切分', note: '一整页照片切成块'},
  {role: 'judge', label: '判定', note: '重做的等价比对'},
  {role: 'brief', label: '简报', note: '科目的近况总结'},
];

/** `source` 三档 → 给人看的一句话。 */
export function sourceLabel(source) {
  if (source === 'settings') return '来自设置文件';
  if (source === 'env') return '来自 .env.local';
  return '来自预设默认';
}

/** `GET /api/settings` 的 `data` → 表单状态。 */
export function formFromView(view) {
  const roles = {};
  for (const row of ROLE_ROWS) {
    const item = (view?.roles || {})[row.role] || {};
    roles[row.role] = {
      provider: item.provider?.value ?? '',
      model: item.model?.value ?? '',
      sources: {
        provider: item.provider?.source ?? 'default',
        model: item.model?.source ?? 'default',
      },
      touched: {provider: false, model: false},
      error: item.error || null,
    };
  }

  const providers = {};
  for (const [name, item] of Object.entries(view?.providers || {})) {
    providers[name] = {
      preset: item.preset === true,
      base_url: item.base_url || '',
      model: item.model || '',
      key_set: item.key?.set === true,
      key_hint: item.key?.hint || null,
      // 用户这一次填的密钥：空串＝**不改动**（与"清掉"是两件事）
      key_input: '',
      key_clear: false,
      key_env: item.key_env || null,
      touched: false,
    };
  }

  return {
    file: view?.file || '',
    file_ok: view?.file_ok !== false,
    applies: view?.applies || '',
    roles,
    providers,
  };
}

/** 一个只有名字的新自定义 provider（界面点「自定义…」时加的那一行）。 */
export function newProvider(name) {
  return {
    preset: false, base_url: '', model: '', key_set: false, key_hint: null,
    key_input: '', key_clear: false, key_env: null, touched: true, is_new: true, name,
  };
}

/** 某个角色现在指着的 provider 在不在「有名有姓」的那批里（预设 ∪ 设置文件里的）。 */
export function knownProviders(form) {
  const preset = Object.entries(form.providers)
    .filter(([, item]) => item.preset).map(([name]) => name);
  const custom = Object.entries(form.providers)
    .filter(([, item]) => !item.preset).map(([name]) => name);
  return {preset, custom, all: [...preset, ...custom]};
}

/**
 * 表单 → `PUT /api/settings` 的请求体。
 *
 * 只写**该写的**：用户动过的字段，或本来就来自设置文件的字段。
 * 预设 provider 一律不写进文件——写进去等于把预设抄成一份自定义，从此不再跟随预设更新。
 */
export function buildPayload(form) {
  const roles = {};
  for (const row of ROLE_ROWS) {
    const item = form.roles[row.role];
    if (!item) continue;
    const spec = {};
    if (item.touched.provider || item.sources.provider === 'settings') {
      spec.provider = item.provider;
    }
    if (item.touched.model || item.sources.model === 'settings') {
      spec.model = item.model;
    }
    if (Object.keys(spec).length > 0) roles[row.role] = spec;
  }

  const providers = {};
  for (const [name, item] of Object.entries(form.providers)) {
    if (item.preset) continue;
    const spec = {};
    if (item.base_url) spec.base_url = item.base_url;
    if (item.model) spec.model = item.model;
    if (item.key_clear) {
      spec.api_key = null;                 // 显式清掉
    } else if (item.key_input) {
      spec.api_key = item.key_input;       // 换一把
    }                                     // 否则**省略**：省略＝不改动
    providers[name] = spec;
  }

  return {roles, providers};
}

/**
 * 提交前的自检。**服务端仍会再验一遍**（那一道才是权威）；这里只是把话说得更早、更具体。
 * 返回 `[]` 表示可以提交。
 */
export function validateForm(form) {
  const problems = [];
  const {all} = knownProviders(form);

  for (const row of ROLE_ROWS) {
    const item = form.roles[row.role];
    if (!item) continue;
    if (!item.provider) {
      problems.push(`${row.label}：没选 provider`);
      continue;
    }
    if (!all.includes(item.provider)) {
      problems.push(`${row.label}：provider ${item.provider} 还没有定义（自定义的要填端点）`);
      continue;
    }
    const definition = form.providers[item.provider] || {};
    if (!definition.preset && !definition.base_url) {
      problems.push(`${row.label}：provider ${item.provider} 缺 base_url`);
      continue;
    }
    const providerDefaultModel = definition.model || '';
    if (!item.model && !providerDefaultModel) {
      problems.push(`${row.label}：没给模型，而 provider ${item.provider} 也没有默认模型`);
    }
  }

  for (const [name, item] of Object.entries(form.providers)) {
    if (item.preset) continue;
    if (!item.base_url) problems.push(`provider ${name}：缺 base_url`);
  }
  return problems;
}

/** 提交结果 → 给人看的一句话（成功与失败都从**服务原话**来）。 */
export function describeResult(status, envelope) {
  if (status === 200) return {ok: true, text: '已保存，立刻生效。'};
  const error = envelope?.error || {};
  return {ok: false, text: `${error.message || '保存失败'}${error.hint ? `；${error.hint}` : ''}`};
}
