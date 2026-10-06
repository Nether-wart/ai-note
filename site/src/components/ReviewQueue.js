import React, {useCallback, useEffect, useMemo, useState} from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import FailurePanel from './FailurePanel';
import {useIndex} from './Shell';
import {useMounted} from '../lib/use-mounted';
import {
  buildTranscriptPayload, describeReadout, describeSaved, formFromReadout, hasSomethingToSave,
  pendingFromIndex,
} from '../lib/review';

/**
 * 审核页：还没转录的卡 → 「跑一次转录」（**人触发**）→ 看图对草稿 → 改 → 写进卡（§10.8）。
 *
 * 四条纪律：
 *
 *   1. **转录是人按的**。进这一页什么都不发生；每次模型调用都由你按一下触发
 *      （骨架卡等审核填，所以钱只花在你真的要看的那几张上）。
 *   2. **图是准绳**。这一页必须把整页照片画出来——不然"对一遍草稿"无从谈起。
 *   3. **保存只写你动过的（或草稿里非空的）**：服务端把 `""` 当**显式清空**，
 *      误发空值会把内容清掉而**不报任何错**。这条逻辑在 `lib/review.js` 里，有测试钉着。
 *   4. **失败照服务原话**：触发失败、保存失败都走 `FailurePanel`，不自己编委婉说法。
 */
export default function ReviewQueue() {
  const {siteConfig} = useDocusaurusContext();
  const apiBase = siteConfig?.customFields?.apiBase || '';
  const mounted = useMounted();
  const index = useIndex();

  const [selected, setSelected] = useState(null);
  const [readout, setReadout] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const [notice, setNotice] = useState(null);
  const [markReviewed, setMarkReviewed] = useState(true);
  const [done, setDone] = useState([]);

  const pending = useMemo(() => pendingFromIndex(index), [index]);
  const visible = pending.filter((row) => !done.includes(row.id));

  useEffect(() => {
    // 换一张卡就把上一张的读数清掉：**不把 A 的草稿留在 B 的表单里**
    setReadout(null);
    setForm(null);
    setFailure(null);
    setNotice(null);
  }, [selected]);

  const runTranscribe = useCallback(async (pid) => {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const response = await fetch(`${apiBase}/api/problem/${encodeURIComponent(pid)}/transcribe`,
                                   {method: 'POST'});
      const envelope = await response.json();
      if (!response.ok) {
        setFailure({code: envelope?.error?.reason || envelope?.error?.code || 'transcribe_failed',
                    message: envelope?.error?.message || '触发转录失败',
                    hint: envelope?.error?.hint});
        return;
      }
      setReadout(envelope.data);
      setForm(formFromReadout(envelope.data));
    } catch (error) {
      setFailure({code: 'network_error', message: `连不上服务：${error.message}`,
                  hint: `服务起了吗？${apiBase}`});
    } finally {
      setBusy(false);
    }
  }, [apiBase]);

  const save = async () => {
    if (!form || !selected) return;
    const payload = buildTranscriptPayload(form, {markReviewed});
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const response = await fetch(
        `${apiBase}/api/problem/${encodeURIComponent(selected)}/transcript`,
        {method: 'PUT', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify(payload)});
      const envelope = await response.json();
      const described = describeSaved(response.status, envelope);
      setNotice(described);
      if (described.ok) {
        setDone((current) => [...current, selected]);
        setSelected(null);
      } else {
        setFailure({...envelope.error,
                    code: envelope?.error?.reason || envelope?.error?.code || 'save_failed'});
      }
    } catch (error) {
      setFailure({code: 'network_error', message: `保存时连不上服务：${error.message}`});
    } finally {
      setBusy(false);
    }
  };

  if (!mounted) {
    return <p className="ai-note-meta">正在从服务读索引…</p>;
  }

  return (
    <div className="row" data-review-queue="true">
      <div className="col col--4">
        <h2>还没转录的卡（{visible.length}）</h2>
        {visible.length === 0 ? (
          <p className="ai-note-meta" data-review-empty="true">
            没有待审的卡。入库建的是**骨架卡**（题面还空着），它们会出现在这里。
          </p>
        ) : (
          <ul className="ai-note-detail-list">
            {visible.map((row) => (
              <li key={row.id}>
                <button type="button" className="button button--secondary"
                  data-review-card={row.id}
                  onClick={() => setSelected(row.id)}>
                  {row.subject || '未归类'} · {String(row.created_at || '').slice(0, 10)}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="col col--8">
        {!selected && <p className="ai-note-meta">左边点一张卡：这一页不会自动做任何事。</p>}

        {selected && (
          <div data-review-detail={selected}>
            <div className="ai-note-workbench__toolbar">
              <button type="button" className="button button--primary" data-review-trigger="true"
                disabled={busy} onClick={() => runTranscribe(selected)}>
                {busy ? '正在跑…' : '跑一次转录'}
              </button>
              <span className="ai-note-meta" data-review-selected={selected}>
                {selected}
              </span>
            </div>

            {readout && readout.ok && (
              <>
                <figure className="ai-note-page-photo">
                  {/* 图是准绳：整页照片来自既有的 `GET /api/page/<id>/image` */}
                  <img data-review-image="true"
                    src={`${apiBase}/api/page/${encodeURIComponent(readout.page_id)}/image`}
                    alt="这一页的整页照片" />
                </figure>
                <ul className="ai-note-meta" data-review-readout="true">
                  {describeReadout(readout).lines.map((line) => <li key={line}>{line}</li>)}
                </ul>
              </>
            )}

            {form && (
              <div data-review-form="true">
                <label className="ai-note-field">
                  题面（可以改；清空＝这道题没有题面）
                  <textarea className="ai-note-field" rows={6} value={form.transcript}
                    data-review-transcript="true"
                    onChange={(event) => setForm({
                      ...form, transcript: event.target.value,
                      touched: {...form.touched, transcript: true},
                    })} />
                </label>
                <p className="ai-note-meta">
                  草稿来自模型；**保存只写你动过的（或草稿里非空的）字段**——
                  没动过的空字段一个都不发，因为服务端把空串当成"显式清空"。
                </p>
                <label className="ai-note-field">
                  <input type="checkbox" checked={markReviewed} data-review-mark="true"
                    onChange={(event) => setMarkReviewed(event.target.checked)} />
                  {' '}确认并标记为**已审核**
                </label>
                <div className="ai-note-workbench__toolbar">
                  <button type="button" className="button button--primary" onClick={save}
                    disabled={busy || !hasSomethingToSave(form, {markReviewed})}
                    data-review-save="true">
                    {busy ? '正在保存…' : '写进卡'}
                  </button>
                  {notice && (
                    <span className={notice.ok ? 'ai-note-meta' : 'ai-note-warn'}
                      data-review-notice={notice.ok}>
                      {notice.text}
                    </span>
                  )}
                </div>
              </div>
            )}

            {failure && <FailurePanel title="这一趟没成" error={failure} />}
          </div>
        )}
      </div>
    </div>
  );
}
