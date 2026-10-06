import React, {useMemo, useState} from 'react';
import FailurePanel from './FailurePanel';
import {
  REVIEW_STATES, addItem, buildPatch, describeSave, formFromCard, removeItem, validateEdit,
} from '../lib/problem-edit';

/**
 * 「编辑属性」表单（契约 §10.7）：科目、考点、错因、审核状态。
 *
 * 三条纪律：
 *
 *   1. **只提交改动过的字段**（`buildPatch`）。所以"保存"在没改动时是**禁用**的，
 *      而不是让用户按下之后收到一句"什么都没改"。
 *   2. **能改的就这四项**，别的（题面、答案、掌握、重做历史）这里根本不画——
 *      它们各有各的路（审核／重做／判定）。少画一个入口，就是少一条绕过它们的路。
 *   3. **失败照服务原话显示**：错因词表这份界面拿不到，取值由服务端把关；
 *      它拒了会连"可选值"一起带回来，这里原样显示（`describeSave`）。
 *
 * 保存成功后**重新读一次这张卡**（`onSaved`）而不是就地改内存：读数是服务算出来的
 * （科目、审核状态都有派生的地方），就地改容易与服务的读数分叉。
 */
export default function ProblemEdit({problem, apiBase, subjects = null, onSaved = null}) {
  const [form, setForm] = useState(() => formFromCard(problem));
  const [drafts, setDrafts] = useState({topics: '', error_causes: ''});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [failure, setFailure] = useState(null);

  const patch = useMemo(() => buildPatch(form, problem), [form, problem]);
  const problems = useMemo(() => validateEdit(form, {subjects}), [form, subjects]);
  const dirty = Object.keys(patch).length > 0;

  const save = async () => {
    setSaving(true);
    setNotice(null);
    setFailure(null);
    try {
      const response = await fetch(
        `${apiBase}/api/problem/${encodeURIComponent(problem.id)}`,
        {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(patch),
        },
      );
      const envelope = await response.json();
      const described = describeSave(response.status, envelope);
      setNotice(described);
      if (described.ok) {
        onSaved?.();
      } else {
        setFailure({...envelope.error,
                    code: envelope.error?.reason || envelope.error?.code || 'save_failed'});
      }
    } catch (error) {
      setFailure({code: 'network_error', message: `保存时连不上服务：${error.message}`});
    } finally {
      setSaving(false);
    }
  };

  const listEditor = (field, label) => (
    <div className="ai-note-field" data-edit-field={field}>
      <span className="ai-note-meta">{label}</span>
      <ul className="ai-note-badges" data-edit-list={field}>
        {form[field].length === 0 && <li className="ai-note-meta">（还没有{label}）</li>}
        {form[field].map((item) => (
          <li key={item} className="ai-note-badge">
            {item}{' '}
            <button
              type="button"
              className="ai-note-edit__drop"
              data-edit-drop={`${field}:${item}`}
              onClick={() => setForm({...form, [field]: removeItem(form[field], item)})}>
              ✕
            </button>
          </li>
        ))}
      </ul>
      <div className="ai-note-workbench__toolbar">
        <input
          type="text"
          className="ai-note-field"
          value={drafts[field]}
          placeholder={label}
          data-edit-input={field}
          onChange={(event) => setDrafts({...drafts, [field]: event.target.value})}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              setForm({...form, [field]: addItem(form[field], drafts[field])});
              setDrafts({...drafts, [field]: ''});
            }
          }} />
        <button
          type="button"
          className="button button--secondary"
          data-edit-add={field}
          disabled={!drafts[field].trim()}
          onClick={() => {
            setForm({...form, [field]: addItem(form[field], drafts[field])});
            setDrafts({...drafts, [field]: ''});
          }}>
          加
        </button>
      </div>
    </div>
  );

  return (
    <div data-problem-edit="true">
      <div className="ai-note-workbench__toolbar">
        <label className="ai-note-field">
          科目
          <select
            className="ai-note-field"
            value={form.subject ?? ''}
            data-edit-subject="true"
            onChange={(event) => setForm({...form, subject: event.target.value || null})}>
            <option value="">未归类</option>
            {(subjects || []).map((name) => <option key={name} value={name}>{name}</option>)}
            {/* 卡上的科目不在词表里时也要能看见它、别把它悄悄吞掉 */}
            {form.subject && subjects && !subjects.includes(form.subject) && (
              <option value={form.subject}>{form.subject}（不在词表里）</option>
            )}
          </select>
        </label>

        <label className="ai-note-field">
          审核状态
          <select
            className="ai-note-field"
            value={form.review}
            data-edit-review="true"
            onChange={(event) => setForm({...form, review: event.target.value})}>
            {REVIEW_STATES.map((state) => (
              <option key={state.value} value={state.value}>{state.label}</option>
            ))}
          </select>
        </label>
      </div>

      {listEditor('topics', '考点')}
      {listEditor('error_causes', '错因')}

      {problems.length > 0 && (
        <div className="ai-note-banner alert alert--warning" role="alert" data-edit-problems="true">
          <strong>还有 {problems.length} 处要补</strong>
          <ul>{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
        </div>
      )}

      <div className="ai-note-workbench__toolbar">
        <button
          type="button"
          className="button button--primary"
          onClick={save}
          disabled={saving || problems.length > 0 || !dirty}
          data-action="save-problem">
          {saving ? '正在保存…' : '保存属性'}
        </button>
        {!dirty && <span className="ai-note-meta" data-edit-clean="true">还没有改动。</span>}
        {notice && (
          <span className={notice.ok ? 'ai-note-meta' : 'ai-note-warn'}
                data-edit-notice={notice.ok}>
            {notice.text}
          </span>
        )}
      </div>

      {failure && (
        <FailurePanel title="这一次改动没收下" error={failure} />
      )}

      <p className="ai-note-meta">
        题面、标准答案、掌握与重做历史**不在这里改**：前两个走审核，后两个只能由重做与判定那条路走。
      </p>
    </div>
  );
}
