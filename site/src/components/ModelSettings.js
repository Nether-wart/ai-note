import React, {useCallback, useEffect, useMemo, useState} from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import FailurePanel from './FailurePanel';
import {
  ROLE_ROWS, buildPayload, describeResult, formFromView, knownProviders, newProvider,
  sourceLabel, validateForm,
} from '../lib/settings';
import {useMounted} from '../lib/use-mounted';

/**
 * 模型设置表单（契约 §10.6）。
 *
 * 这一页是**运行时组件**而不是 MDX 内容：它要读 `/api/settings`、要写回去，
 * 而「设置存哪、改了什么」是运行时事实。页面外壳（`pages/settings.mdx`）是 MDX——
 * 「没有数据的壳长在 Docusaurus 框架上，读写接口的活留在运行时」这条线在这里同样成立。
 *
 * 三条界面纪律：
 *
 *   1. **密钥只显示「已设置 ＋ 末四位」**，永不回显整把。输入框留空＝**不改动**（不是清掉），
 *      清掉要显式勾。
 *   2. **保存前先把服务的话说完**：`PUT` 失败时显示的是服务原话（含"一个字节都没写"），
 *      不自己编一句委婉说法。
 *   3. **失效不许看起来像成功**：读不到设置就是一个失败面板，不是一片空表单。
 */
export default function ModelSettings() {
  const {siteConfig} = useDocusaurusContext();
  const apiBase = siteConfig?.customFields?.apiBase || '';
  const mounted = useMounted();
  const [form, setForm] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [failure, setFailure] = useState(null);
  const [notice, setNotice] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newName, setNewName] = useState('');

  const load = useCallback(async () => {
    setFailure(null);
    try {
      const response = await fetch(`${apiBase}/api/settings`);
      const envelope = await response.json();
      if (!response.ok) {
        setFailure({code: `http_${response.status}`, message: envelope?.error?.message || '读不到设置',
                    hint: envelope?.error?.hint});
        return;
      }
      setForm(formFromView(envelope.data));
      setWarnings(envelope.warnings || []);
    } catch (error) {
      // 网络层失败也要说清是**哪一种**失败：服务没起、还是地址不对
      setFailure({code: 'network_error', message: `连不上服务：${apiBase}/api/settings`,
                  hint: `服务起了吗？在仓库根跑：python3 -m server.app --port 8765`});
    }
  }, [apiBase]);

  useEffect(() => {
    if (mounted) {
      load();
    }
  }, [mounted, load]);

  const providers = useMemo(() => (form ? knownProviders(form) : {preset: [], custom: [], all: []}),
                          [form]);
  const problems = useMemo(() => (form ? validateForm(form) : []), [form]);

  const touch = (role, field, value) => {
    setForm((current) => ({
      ...current,
      roles: {
        ...current.roles,
        [role]: {
          ...current.roles[role],
          [field]: value,
          touched: {...current.roles[role].touched, [field]: true},
        },
      },
    }));
    setNotice(null);
  };

  const editProvider = (name, field, value) => {
    setForm((current) => ({
      ...current,
      providers: {
        ...current.providers,
        [name]: {...current.providers[name], [field]: value, touched: true},
      },
    }));
    setNotice(null);
  };

  const addProvider = () => {
    const name = newName.trim();
    if (!name) return;
    setForm((current) => ({...current, providers: {...current.providers, [name]: newProvider(name)}}));
    setNewName('');
  };

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const response = await fetch(`${apiBase}/api/settings`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(buildPayload(form)),
      });
      const envelope = await response.json();
      const described = describeResult(response.status, envelope);
      setNotice(described);
      if (described.ok) {
        setForm(formFromView(envelope.data));
        setWarnings(envelope.warnings || []);
      } else {
        setFailure(envelope.error ? {...envelope.error, code: envelope.error.reason || envelope.error.code}
                                  : {code: 'save_failed', message: '保存失败'});
      }
    } catch (error) {
      setFailure({code: 'network_error', message: `保存时连不上服务：${error.message}`,
                  hint: '服务还在跑吗？'});
    } finally {
      setSaving(false);
    }
  };

  if (!mounted || (!form && !failure)) {
    return <p className="ai-note-meta" data-settings-loading="true">正在从服务读设置…</p>;
  }
  if (failure) {
    return (
      <div data-settings-failed="true">
        <FailurePanel title="读不到模型设置" error={failure} onRetry={load} />
      </div>
    );
  }

  return (
    <div data-model-settings="true">
      <p className="ai-note-meta" data-settings-file={form.file}>
        设置文件：<code>{form.file}</code>（{form.applies || '立刻生效'}）
      </p>

      {/* 文件坏着的时候服务**每一次**都会带上这条警告（§10.6）：界面照原样显示。 */}
      {!form.file_ok && warnings.length > 0 && (
        <div className="ai-note-banner alert alert--warning" role="alert" data-settings-file-broken="true">
          <strong>设置文件现在用不了</strong>
          {warnings.map((warning) => (
            <p key={warning.code} data-warning-code={warning.code}>{warning.message}</p>
          ))}
          <p className="ai-note-meta">模型调用退回环境变量与预设——原因就是上面这一条。</p>
        </div>
      )}

      <table className="ai-note-table" data-settings-roles="true">
        <thead>
          <tr><th>角色</th><th>provider</th><th>模型</th><th>现在用的是什么</th></tr>
        </thead>
        <tbody>
          {ROLE_ROWS.map((row) => {
            const item = form.roles[row.role];
            const definition = form.providers[item.provider] || {};
            return (
              <tr key={row.role} data-role={row.role}>
                <td>
                  <strong>{row.label}</strong>
                  <span className="ai-note-meta"> {row.note}</span>
                </td>
                <td>
                  <select
                    className="ai-note-field"
                    value={item.provider}
                    data-role-provider={row.role}
                    onChange={(event) => touch(row.role, 'provider', event.target.value)}>
                    {providers.all.map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                    {!providers.all.includes(item.provider) && (
                      <option value={item.provider}>{item.provider}（未定义）</option>
                    )}
                  </select>
                  <div className="ai-note-meta" data-role-source={row.role}>
                    {sourceLabel(item.sources.provider)}
                  </div>
                </td>
                <td>
                  <input
                    className="ai-note-field"
                    type="text"
                    value={item.model}
                    data-role-model={row.role}
                    onChange={(event) => touch(row.role, 'model', event.target.value)} />
                  <div className="ai-note-meta">{sourceLabel(item.sources.model)}</div>
                </td>
                <td className="ai-note-meta">
                  {item.error ? (
                    <span className="ai-note-warn" data-role-error={item.error.code}>{item.error.message}</span>
                  ) : (
                    <>
                      <div>{definition.base_url || '（没有端点）'}</div>
                      <div data-role-key={row.role}>
                        密钥：{definition.key_set ? `已设置（${definition.key_hint}）` : '没有'}
                        {definition.key_env ? ` · 也可以放在 ${definition.key_env}` : ''}
                      </div>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h2>自定义的模型服务</h2>
      <p className="ai-note-meta">
        预设之外的名字由你自己定义：任何 <strong>OpenAI 兼容</strong>端点都可以
        （自建 vLLM、本机 Ollama、官方接口……）。照片与答案会发到你填的那一家。
      </p>
      <ul data-settings-providers="true">
        {providers.preset.map((name) => {
          const item = form.providers[name];
          return (
            <li key={name} data-provider={name} data-preset="true">
              <strong>{name}</strong>
              <span className="ai-note-meta">
                {' '}预设 · {item.base_url} · 密钥
                {item.key_set ? `已设置（${item.key_hint}）` : '没有'}
                {item.key_env ? `（环境变量 ${item.key_env}）` : ''}
              </span>
            </li>
          );
        })}
        {providers.custom.map((name) => {
          const item = form.providers[name];
          return (
            <li key={name} data-provider={name} data-preset="false">
              <div className="ai-note-badges">
                <strong>{name}</strong>
                <span className="ai-note-meta">
                  {item.key_set ? `密钥已设置（${item.key_hint}）` : '密钥没有'}
                </span>
              </div>
              <label className="ai-note-field">
                base_url
                <input type="text" value={item.base_url} data-provider-base-url={name}
                  placeholder="https://api.openai.com/v1"
                  onChange={(event) => editProvider(name, 'base_url', event.target.value)} />
              </label>
              <label className="ai-note-field">
                默认模型（可留空）
                <input type="text" value={item.model} data-provider-model={name}
                  onChange={(event) => editProvider(name, 'model', event.target.value)} />
              </label>
              <label className="ai-note-field">
                换一把密钥（留空＝不改动）
                <input type="password" value={item.key_input} data-provider-key={name}
                  autoComplete="new-password"
                  onChange={(event) => editProvider(name, 'key_input', event.target.value)} />
              </label>
              {item.key_set && (
                <label className="ai-note-meta">
                  <input type="checkbox" checked={item.key_clear} data-provider-key-clear={name}
                    onChange={(event) => editProvider(name, 'key_clear', event.target.checked)} />
                  {' '}清掉这把密钥（不勾＝保留）
                </label>
              )}
            </li>
          );
        })}
      </ul>

      <div className="ai-note-workbench__toolbar">
        <input className="ai-note-field" type="text" value={newName} placeholder="新 provider 的名字"
          data-settings-new-provider="true"
          onChange={(event) => setNewName(event.target.value)} />
        <button type="button" className="button button--secondary" onClick={addProvider}
          disabled={!newName.trim()} data-action="add-provider">
          加一家
        </button>
      </div>

      {problems.length > 0 && (
        <div className="ai-note-banner alert alert--warning" role="alert" data-settings-problems="true">
          <strong>还有 {problems.length} 处要补</strong>
          <ul>{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
        </div>
      )}

      <div className="ai-note-workbench__toolbar">
        <button type="button" className="button button--primary" onClick={save}
          disabled={saving || problems.length > 0} data-action="save-settings">
          {saving ? '正在保存…' : '保存'}
        </button>
        {notice && (
          <span className={notice.ok ? 'ai-note-meta' : 'ai-note-warn'} data-settings-notice={notice.ok}>
            {notice.text}
          </span>
        )}
      </div>
    </div>
  );
}
