import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
  commitPage,
  pageImageUrl,
  patchPage,
  postPage,
  readPage,
  resegmentPage,
} from '../lib/api';
import {
  PROBLEM_TYPES,
  blockBoxToDisplay,
  editOutcome,
  editPayload,
  editsRequest,
  pageView,
  readBox,
  resegmentCardWarnings,
  resegmentRows,
  resegmentSummary,
} from '../lib/split';
import FailurePanel from './FailurePanel';
import {useIndex} from '../lib/shell-context';

/**
 * 一条修正的形状。
 *
 * 七条既有动作照旧走 `lib/split.js` 的 `editPayload`（形状与判据**只有那一份**）。
 * `add` / `delete` 是 #17 加进契约的两条新动作，而 `split.js` 的动作闭集与它的测试
 * 是**冻结物**（`site/tests/split.test.mjs` 逐字钉住那七条），所以这两条的形状按
 * 契约 §10.2.1b 写在这里：`delete` 只要 `block_id`；`add` 要一个读得出来的
 * `bbox_norm`（与 `move` 同一个拒绝形状），`question_no`／`problem_type`／`keep`
 * **一律省略**——`keep` 省略时服务落 `true`（手工块默认「收」，#26），界面不替它定。
 * 框的判据本身也不重写：用 `split.js` 的 `readBox`。
 */
function buildEdit(action, fields = {}) {
  if (action === 'delete') {
    if (!fields.block_id) throw new Error('删块要指名是哪一块（block_id）');
    return {action, block_id: fields.block_id};
  }
  if (action === 'add') {
    const box = readBox(fields.bbox_norm);
    if (box === null) throw new Error('新增一块要给一个读得出来的 bbox_norm（xywh，宽高为正）');
    return {action, bbox_norm: box};
  }
  return editPayload(action, fields);
}

/**
 * 全屏工作台：**录入一页**，或者是**改已有的一页**（`/split`）。
 *
 * 一条流程走完（spec #17 §4）：
 *
 *     上传整页照片 → 机器切分（只是预设）→ 人工调整／新建／删除块 → 渲染草稿 → 入库
 *
 * 五条纪律：
 *
 * 1. **页文件是真相，界面内存不是**。每一次修正都发 `PATCH /api/page/<id>`，然后用
 *    **服务返回的那份页**刷新显示——「切分漏了一道题」查不出来的根源就是切分结果
 *    只活在界面内存里。
 * 2. **坐标只有一份**：块框画在整页照片上走 `lib/split.js` 的 `blockBoxToDisplay`
 *    （整页归一化 → 显示像素）。这里是**唯一**一处坐标换算的实现，不重写第二份。
 * 3. **切分不可用不是错误**（`blocks: null`）：明说「切分不可用，请在这张照片上
 *    自己画框」，并直接开放「新增一块」。`null`（不知道）与 `[]`（确实没有题）
 *    是两件事，不许混。
 * 4. **重置为预设是破坏性动作**：先被 **409** `resegment_needs_confirmation` 拦下，
 *    把 `details.discarded`（会丢掉几处人工改动）照原话摆出来，**再问一次**，
 *    带上 `{"confirm_discard_manual": true}` 重发。
 * 5. **不许静默**：`blocks_removed`／`blocks_added`（含 0）要报，入库的四类结果
 *    （`created` / `reused` / `skipped` / `refused`）一条都不许吞，服务给的
 *    `warnings[]` / `skipped[]` 照原话显示。
 *
 * 判定字段（`verdict`／`source`／`confidence`／`rule`）一个都不由客户端给：
 * 界面只发「人做了什么」（块的 id、边界、题号、题型、收不收），全是既有的闭集动作。
 */
export default function Workbench({apiBase, pageId = null, subjects = [], onClose = null}) {
  const [state, setState] = useState(() => ({
    phase: pageId ? 'loading' : 'pick',
    page: null,
    error: null,
  }));
  const [subject, setSubject] = useState('');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);       // 「建」那一趟的回执（服务给的）
  const [last, setLast] = useState(null);           // 上一次修正的回执
  const [resegment, setResegment] = useState(null); // 重跑切分：需要确认／跑完了／失败
  const [commit, setCommit] = useState(null);       // 入库结果
  const [selected, setSelected] = useState(null);   // 选中的块
  const [drawMode, setDrawMode] = useState(false);  // 「在照片上拖出一个框」
  const [size, setSize] = useState({w: 0, h: 0});   // 照片**显示出来**的尺寸
  const [drag, setDrag] = useState(null);           // 拖动块时的预览框（归一化）
  const [draft, setDraft] = useState(null);         // 新框的预览（归一化）
  const [questionNo, setQuestionNo] = useState('');

  const photoRef = useRef(null);
  const imageRef = useRef(null);
  const gestureRef = useRef(null);
  // 外壳那一份索引：**入库／建页之后**请它重读一次。
  // 这不是「每一页各取一次索引」（那一条由 `ShellProvider` 保证），而是一次**写**
  // 之后的显式刷新——不然侧栏的道数会停在旧值上，看起来像「录进去的题不见了」。
  const shellIndex = useIndex();

  const page = state.page;
  const pid = page?.id || pageId;
  const view = useMemo(() => pageView(page || {blocks: []}), [page]);
  const blocksKnown = Array.isArray(page?.blocks);
  const selectedRow = view.blocks.find((row) => row.id === selected) || null;

  // 「读一页」走 `readPage`（`dry_run` 的空预演）：契约里 **没有** `GET /api/page/<id>`
  // 这条路由，`fetchPage` 在真实 socket 上是 405——理由与实测写在 `lib/api.js` 那里。
  const load = useCallback(async (targetId) => {
    setState({phase: 'loading', page: null, error: null});
    try {
      const {page: loaded} = await readPage(apiBase, targetId);
      setState({phase: 'editing', page: loaded, error: null});
    } catch (error) {
      setState({phase: 'failed', page: null, error});
    }
  }, [apiBase]);

  useEffect(() => {
    if (pageId) {
      load(pageId);
    }
  }, [load, pageId]);

  // 切分不可用（`blocks` 是 null，不是 []）：直接开放手画，别让人先去找按钮
  useEffect(() => {
    if (state.phase === 'editing' && !blocksKnown) {
      setDrawMode(true);
    }
  }, [state.phase, blocksKnown]);

  // 换了一块就把题号输入框清掉：留着上一块的题号，一次手误就会把题号写到另一块上
  useEffect(() => {
    setQuestionNo('');
  }, [selected]);

  // 照片显示尺寸：块框要按它缩放（`blockBoxToDisplay` 的入参就是它）
  const measure = useCallback(() => {
    const image = imageRef.current;
    if (!image) return;
    setSize({w: image.clientWidth, h: image.clientHeight});
  }, []);

  useEffect(() => {
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [measure]);

  /** 服务返回的整封回应里那几样**必须显示**的东西（原话，不重拼）。 */
  const noticesOf = (envelope) => ({
    warnings: envelope?.warnings || [],
    skipped: envelope?.skipped || [],
  });

  // ------------------------------------------------------------------ 建一页

  const create = useCallback(async () => {
    if (!file) {
      setState((current) => ({
        ...current,
        error: {
          code: 'file_missing',
          message: '还没有选文件：先选一张整页照片（或者把它拖进来）。',
          hint: '一页 = 一张照片；手机上传页与这里走同一条管道。',
        },
      }));
      return;
    }
    setBusy(true);
    setNotice(null);
    setState({phase: 'creating', page: null, error: null});
    try {
      const envelope = await postPage(apiBase, {
        file,
        filename: file.name,
        subject: subject || null,
      });
      const row = (envelope.data?.pages || [])[0] || null;
      setNotice({
        row,
        segmentation: envelope.data?.segmentation || null,
        ...noticesOf(envelope),
      });
      if (!row?.page_id) {
        // 服务没有建出页 → 明说，**绝不装作成功**
        setState({
          phase: 'failed',
          page: null,
          error: {
            code: 'page_not_created',
            message: '服务没有建出这一页（回执里没有 page_id）。',
            hint: '上面那张回执是服务的原话，照着它查。',
          },
        });
        return;
      }
      await load(row.page_id);
      // 建了一页：索引里多了一页（还没有题卡），让侧栏跟着刷新
      shellIndex?.reload?.();
    } catch (error) {
      // 400（科目不在受控词表里）／502（模型没问成）都照服务的原话显示
      setState({phase: 'pick', page: null, error});
    } finally {
      setBusy(false);
    }
  }, [apiBase, file, subject, load, shellIndex]);

  // ------------------------------------------------------------------ 改一页

  const sendEdits = useCallback(async (edits) => {
    setBusy(true);
    setLast(null);
    try {
      const envelope = await patchPage(apiBase, pid, editsRequest(edits));
      const report = envelope.data;
      setLast({
        ...editOutcome(report),
        // 两个读数**每一次都报**（0 也报）：「每次删除都要报数」（契约 §10.2.1b）
        blocks_removed: report.blocks_removed ?? 0,
        blocks_added: report.blocks_added ?? 0,
        ...noticesOf(envelope),
      });
      if (report.page) {
        setState((current) => ({...current, phase: 'editing', page: report.page, error: null}));
      } else {
        // 服务没回整份页（界面内存不是真相）→ 回去读一次，别拿内存里的旧块列表当真
        await load(pid);
      }
    } catch (error) {
      setLast({failed: true, error});
    } finally {
      setBusy(false);
    }
  }, [apiBase, pid, load]);

  const run = useCallback((action, fields) => {
    let edit;
    try {
      edit = buildEdit(action, fields);
    } catch (error) {
      // 形状不对是界面自己手误（服务一定拒绝），先挡住、并如实说是界面挡的
      setLast({failed: true, error: {code: 'ui_rejected', message: error.message, hint: null}});
      return;
    }
    sendEdits([edit]);
  }, [sendEdits]);

  // ------------------------------------------------------------------ 笔画（拖框／画框）

  /** 显示像素 → 整页归一化：**同一个** `blockBoxToDisplay`，把缩放反过来用。
   *  基准只有一个（页文件里的 `bbox_norm`），所以这里不写第二份换算。 */
  function displayToNorm(box, width, height) {
    if (!(width > 0) || !(height > 0)) return null;
    return blockBoxToDisplay(box, 1 / width, 1 / height);
  }

  function clampBox(box) {
    const [x, y, w, h] = box;
    return [
      Math.min(Math.max(x, 0), Math.max(0, 1 - w)),
      Math.min(Math.max(y, 0), Math.max(0, 1 - h)),
      w,
      h,
    ];
  }

  function startMove(event, row) {
    if (drawMode || busy) return;
    event.preventDefault();
    event.stopPropagation();
    setSelected(row.id);
    const rect = photoRef.current?.getBoundingClientRect();
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) return;
    // 起点按**显示像素**记下来：拖动与落点都经过 `blockBoxToDisplay` / 它的逆，
    // 于是「画出来的框」与「发出去的框」用的是同一份换算。
    const originPx = blockBoxToDisplay(row.page_box, rect.width, rect.height);
    if (!originPx) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    gestureRef.current = {
      kind: 'move',
      pointerId: event.pointerId,
      blockId: row.id,
      origin: row.page_box,
      originPx,
      startX: event.clientX,
      startY: event.clientY,
      rect,
      preview: row.page_box,
    };
    setDrag({blockId: row.id, box: row.page_box});
  }

  function onMoveStep(event) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.kind !== 'move' || gesture.pointerId !== event.pointerId) return;
    const box = displayToNorm(
      [
        gesture.originPx[0] + (event.clientX - gesture.startX),
        gesture.originPx[1] + (event.clientY - gesture.startY),
        gesture.originPx[2],
        gesture.originPx[3],
      ],
      gesture.rect.width,
      gesture.rect.height,
    );
    if (!box) return;
    const clamped = clampBox(box);
    gesture.preview = clamped;
    setDrag({blockId: gesture.blockId, box: clamped});
  }

  function endMove(event) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.kind !== 'move' || gesture.pointerId !== event.pointerId) return;
    gestureRef.current = null;
    setDrag(null);
    const box = gesture.preview;
    if (box[0] === gesture.origin[0] && box[1] === gesture.origin[1]) {
      // 没动就不发请求：发一条 changed=false 的空修正只会往回执里灌噪声
      return;
    }
    run('move', {block_id: gesture.blockId, bbox_norm: box});
  }

  function startDraw(event) {
    if (!drawMode || busy) return;
    const rect = photoRef.current?.getBoundingClientRect();
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) return;
    if (event.target !== event.currentTarget && event.target !== imageRef.current) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const originPx = [event.clientX - rect.left, event.clientY - rect.top];
    gestureRef.current = {kind: 'draw', pointerId: event.pointerId, rect, originPx, preview: null};
    setDraft([0, 0, 0, 0]);
  }

  function onDrawStep(event) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.kind !== 'draw' || gesture.pointerId !== event.pointerId) return;
    const x = event.clientX - gesture.rect.left;
    const y = event.clientY - gesture.rect.top;
    const box = displayToNorm(
      [
        Math.min(gesture.originPx[0], x),
        Math.min(gesture.originPx[1], y),
        Math.abs(x - gesture.originPx[0]),
        Math.abs(y - gesture.originPx[1]),
      ],
      gesture.rect.width,
      gesture.rect.height,
    );
    if (!box) return;
    gesture.preview = box;
    setDraft(box);
  }

  function endDraw(event) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.kind !== 'draw' || gesture.pointerId !== event.pointerId) return;
    gestureRef.current = null;
    setDraft(null);
    const box = gesture.preview;
    if (!box || box[2] < 0.02 || box[3] < 0.02) {
      setLast({
        failed: true,
        error: {
          code: 'draw_too_small',
          message: '这个框太小了（不到照片的 2%），没有新增——先画一个看得出是一道题的框。',
          hint: '重新在照片上拖一次；也可以先拖大一点再调整。',
        },
      });
      return;
    }
    run('add', {bbox_norm: box});
  }

  // ------------------------------------------------------------------ 重置为预设（破坏性）

  const doResegment = useCallback(async ({confirmDiscardManual = false} = {}) => {
    setBusy(true);
    setResegment(null);
    try {
      const envelope = await resegmentPage(apiBase, pid, {confirmDiscardManual});
      setResegment({done: true, data: envelope.data, ...noticesOf(envelope)});
      // 重跑之后块的来源与留痕都可能变了 → 回去读一次，不拿旧块列表当真
      const {page: fresh} = await readPage(apiBase, pid);
      setState((current) => ({...current, page: fresh}));
    } catch (error) {
      if (error?.reason === 'resegment_needs_confirmation' || error?.code === 'resegment_needs_confirmation') {
        // 破坏性动作**先报告、再动手**：把将丢弃的东西摆出来，再问一次
        setResegment({needsConfirmation: true, error});
      } else {
        setResegment({failed: true, error});
      }
    } finally {
      setBusy(false);
    }
  }, [apiBase, pid]);

  // ------------------------------------------------------------------ 入库

  const doCommit = useCallback(async () => {
    setBusy(true);
    setCommit(null);
    try {
      const envelope = await commitPage(apiBase, pid);
      setCommit({ok: true, data: envelope.data, ...noticesOf(envelope)});
      // 入库会把 card_id 回写到块上：回去读一次，别拿内存里的旧绑定当真
      const {page: fresh} = await readPage(apiBase, pid);
      setState((current) => ({...current, page: fresh}));
      // 新题卡进了库 → 请外壳重读索引，侧栏／总览的道数才不是旧值
      shellIndex?.reload?.();
    } catch (error) {
      setCommit({ok: false, error});
    } finally {
      setBusy(false);
    }
  }, [apiBase, pid, shellIndex]);

  // ------------------------------------------------------------------ 渲染

  if (state.phase === 'pick' || state.phase === 'creating') {
    return (
      <WorkbenchFrame onClose={onClose} title="录入一页">
        <p className="ai-note-meta">
          往这里放一张整页照片：机器先切一遍（只是预设），人再调整，最后入库。
        </p>

        <label className="ai-note-field">
          科目（取自受控词表）
          <select
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            data-subject-select="true"
            disabled={busy}>
            <option value="">未归类（不指定科目）</option>
            {subjects.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {subjects.length === 0 && (
          <p className="ai-note-hint" data-vocabulary-empty="true">
            受控词表里现在没有科目（读不到还是空的）：先按未归类收入。
          </p>
        )}

        <label className="ai-note-field">
          整页照片
          <input
            type="file"
            accept="image/*"
            capture="environment"
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            data-file-input="true"
          />
        </label>

        <div
          className="ai-note-dropzone"
          data-drop-zone="true"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const dropped = event.dataTransfer?.files?.[0];
            if (dropped) setFile(dropped);
          }}>
          把照片拖到这里也可以{file ? `（已选：${file.name}）` : ''}
        </div>

        <button
          type="button"
          className="button button--primary"
          disabled={busy || !file}
          onClick={create}
          data-action="create-page">
          {busy ? '正在建这一页（机器在切分，慢一点）…' : '建这一页'}
        </button>

        {state.error && <FailurePanel title="这一页没有建成" error={state.error} />}
        {notice && <CreateNotice notice={notice} />}
      </WorkbenchFrame>
    );
  }

  if (state.phase === 'loading') {
    return (
      <WorkbenchFrame onClose={onClose} title="切分与录入">
        <p data-status="loading">正在从服务读这一页…</p>
      </WorkbenchFrame>
    );
  }

  if (state.phase === 'failed') {
    return (
      <WorkbenchFrame onClose={onClose} title="切分与录入">
        <FailurePanel title="读不到这一页" error={state.error} onRetry={() => load(pid)} />
      </WorkbenchFrame>
    );
  }

  return (
    <WorkbenchFrame
      onClose={onClose}
      title="切分与录入"
      status={
        <span className="ai-note-meta" data-page-id={pid} data-segmentation={page?.segmentation?.mode || ''}>
          页 <code>{pid}</code> · 科目 {page?.subject || '未归类'} · 块的来源{' '}
          <code>{page?.segmentation?.mode || '（服务没给）'}</code>
        </span>
      }>
      <div className="ai-note-workbench__toolbar">
        <button
          type="button"
          className="button button--danger button--sm"
          disabled={busy}
          onClick={() => doResegment()}
          data-action="resegment">
          重跑切分（重置为预设）
        </button>
        <span className="ai-note-meta">破坏性：会丢掉人工增删改的块，服务会先报清丢几处再问你一次。</span>
        <button
          type="button"
          className="button button--primary button--sm"
          disabled={busy}
          onClick={doCommit}
          data-action="commit-page">
          {busy ? '正在处理…' : '入库'}
        </button>
        <button
          type="button"
          className={drawMode ? 'button button--secondary button--sm' : 'button button--outline button--sm'}
          disabled={busy}
          onClick={() => setDrawMode((on) => !on)}
          data-action="toggle-draw">
          {drawMode ? '正在画框（点这里停）' : '新增一块（在照片上拖出框）'}
        </button>
      </div>

      {blocksKnown ? (
        <p className="ai-note-meta" data-block-counts>
          块 {view.counts.blocks} 块 · 收 {view.counts.kept} / 不收 {view.counts.dropped} / 待定{' '}
          {view.counts.pending}
        </p>
      ) : (
        <p className="ai-note-warn" data-segmentation-unavailable="true">
          切分不可用：服务没有给出块列表（不是「这一页没有题」）。
          请在这张照片上自己画框——「新增一块」已经打开，直接在照片上拖一个框出来。
        </p>
      )}

      {resegment && <ResegmentPanel resegment={resegment} busy={busy}
        onConfirm={() => doResegment({confirmDiscardManual: true})} />}
      {last && <EditOutcomePanel last={last} />}
      {commit && <CommitPanel commit={commit} />}
      {state.error && <FailurePanel title="服务拒绝了这一步" error={state.error} />}

      {/* `--draw` 是给触摸屏的：画框模式下照片要吃掉所有手势（`touch-action: none`），
          不画的时候竖向手势留给页面滚动——否则手机上照片占掉大半屏，页面就滚不动了。
          拖块框的手势由 `.ai-note-block-box` 自己收，见 `custom.css`。 */}
      <figure className={drawMode ? 'ai-note-page-photo ai-note-page-photo--draw' : 'ai-note-page-photo'}
        ref={photoRef}
        onPointerDown={startDraw} onPointerMove={onDrawStep} onPointerUp={endDraw}>
        <img
          ref={imageRef}
          src={pageImageUrl(apiBase, pid)}
          alt="整页照片（上面画的是切出来的块框，整页坐标）"
          data-image-kind="page"
          onLoad={measure}
          draggable={false}
        />
        {/* 块框：整页归一化 → 显示像素，只有 `blockBoxToDisplay` 这一份换算。 */}
        {size.w > 0 &&
          view.blocks.map((row) => {
            const box = drag?.blockId === row.id ? drag.box : row.page_box;
            const px = blockBoxToDisplay(box, size.w, size.h);
            if (!px) return null;
            return (
              <div
                key={row.id}
                className={
                  selected === row.id
                    ? 'ai-note-block-box ai-note-block-box--selected'
                    : 'ai-note-block-box'
                }
                data-block-box={row.id}
                data-keep={row.keep === null ? 'pending' : String(row.keep)}
                style={{left: px[0], top: px[1], width: px[2], height: px[3]}}
                onPointerDown={(event) => startMove(event, row)}
                onPointerMove={onMoveStep}
                onPointerUp={endMove}
                title={`块 ${row.id}：拖它就能移动（move）`}>
                <span className="ai-note-block-box__label">
                  {row.id}
                  {row.card_id ? ` · ${row.card_id}` : ' · 还没入库'}
                </span>
              </div>
            );
          })}
        {draft && size.w > 0 && (() => {
          const px = blockBoxToDisplay(draft, size.w, size.h);
          if (!px) return null;
          return (
            <div
              className="ai-note-block-box ai-note-block-box--draft"
              data-block-box-draft="true"
              style={{left: px[0], top: px[1], width: px[2], height: px[3]}}
            />
          );
        })()}
      </figure>
      {size.w === 0 && (
        <p className="ai-note-warn" data-photo-size-unknown="true">
          照片还没量出显示尺寸（或者这张照片取不到）：量不出来就画不出块框——
          框的换算靠显示尺寸，绝不猜一个。
        </p>
      )}

      <section data-block-inspector="true" data-block-id={selectedRow?.id || ''}>
        <h2>这一块</h2>
        {!selectedRow ? (
          <p className="ai-note-meta">
            在照片上点一块，或者点下面列表里的一块。拖动框 = 移动（move）。
          </p>
        ) : (
          <BlockInspector
            row={selectedRow}
            busy={busy}
            questionNo={questionNo}
            setQuestionNo={setQuestionNo}
            onRun={run}
          />
        )}
      </section>

      <section>
        <h2>块</h2>
        {blocksKnown && view.blocks.length === 0 ? (
          <p className="ai-note-meta" data-blocks-empty="true">
            页文件里记着确实切出 0 块。要录题就在照片上自己画框。
          </p>
        ) : (
          <ul className="ai-note-block-list">
            {view.blocks.map((row) => (
              <li
                key={row.id}
                data-block-row={row.id}
                data-keep={row.keep === null ? 'pending' : String(row.keep)}
                className={selected === row.id ? 'ai-note-card ai-note-card--selected' : 'ai-note-card'}>
                <button type="button" className="ai-note-block-list__pick"
                  onClick={() => setSelected(row.id)} data-action="select-block">
                  块 {row.id} · {row.keep_cn}
                  {row.question_no !== null ? ` · 第 ${row.question_no} 题` : ''}
                  {row.problem_type_cn ? ` · ${row.problem_type_cn}` : ''}
                  {row.card_id ? ` · ${row.card_id}` : ' · 还没入库'}
                </button>
                {row.decision?.reason && (
                  <p className="ai-note-meta" data-decision-rule={row.decision.rule}>
                    {row.decision.reason}
                  </p>
                )}
                {row.ink && (
                  <p className="ai-note-meta">
                    红笔 {row.ink.colored_px}px（占 {(100 * (row.ink.colored_ratio || 0)).toFixed(1)}%）
                    —— 只展示，不当闸门
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>被删掉的块（留痕）</h2>
        {(page?.removed_blocks || []).length === 0 ? (
          <p className="ai-note-meta" data-removed-blocks="0">
            这一页还没有删过块。
          </p>
        ) : (
          <ul data-removed-blocks={page.removed_blocks.length}>
            {page.removed_blocks.map((entry, index) => (
              <li key={`${entry.block_id}-${index}`} className="ai-note-meta">
                块 {entry.block_id}（删于 {entry.removed_at || '（服务没给时刻）'}）
              </li>
            ))}
          </ul>
        )}
      </section>
    </WorkbenchFrame>
  );
}

/** 全屏的壳：入口形态是「弹窗」，尺寸是全屏工作台（spec #17 决定 28）。 */
function WorkbenchFrame({title, status = null, onClose, children}) {
  return (
    <div className="ai-note-workbench" role="dialog" aria-modal="true" aria-label={title}
      data-workbench="true">
      <header className="ai-note-workbench__head">
        <h1>{title}</h1>
        {status}
        {onClose && (
          <button type="button" className="button button--outline" onClick={onClose}
            data-action="close-workbench">
            关掉
          </button>
        )}
      </header>
      <div className="ai-note-workbench__body">{children}</div>
    </div>
  );
}

/** 一块的操作面板：去留、删除、题号、题型。全是既有闭集动作，走 PATCH。 */
function BlockInspector({row, busy, questionNo, setQuestionNo, onRun}) {
  return (
    <div>
      <p className="ai-note-meta">
        块 <code>{row.id}</code> · {row.keep_cn}
        {row.card_id ? <> · 已绑题卡 <code>{row.card_id}</code></> : ' · 还没入库（入库才分配 id）'}
      </p>

      <div className="ai-note-workbench__toolbar">
        {row.keep === true ? (
          <button type="button" className="button button--sm" disabled={busy}
            onClick={() => onRun('drop', {block_id: row.id})} data-action="drop">
            不收它（drop）
          </button>
        ) : (
          <button type="button" className="button button--sm" disabled={busy}
            onClick={() => onRun('keep', {block_id: row.id})} data-action="keep">
            收进库（keep）
          </button>
        )}
        <button type="button" className="button button--danger button--sm" disabled={busy}
          onClick={() => onRun('delete', {block_id: row.id})} data-action="delete">
          删掉这一块（delete）
        </button>
        <span className="ai-note-meta">拖动照片上的框 = 移动（move）。</span>
      </div>
      {row.card_id && (
        <p className="ai-note-hint" data-delete-bound-note="true">
          这一块已经绑了题卡 <code>{row.card_id}</code>：不许删它（删块会造出孤儿绑定），要「不要它」只能用「不收」（drop）。
        </p>
      )}

      <form
        className="ai-note-workbench__toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const parsed = Number(questionNo);
          if (!Number.isInteger(parsed) || parsed <= 0) {
            // 题号要么是正整数、要么留空（`editPayload` 的同一条判据）——不许猜一个
            onRun('question_no', {block_id: row.id, question_no: null});
            return;
          }
          onRun('question_no', {block_id: row.id, question_no: parsed});
        }}>
        <label className="ai-note-field">
          题号
          <input type="number" min="1" value={questionNo} disabled={busy}
            onChange={(event) => setQuestionNo(event.target.value)} data-input="question-no" />
        </label>
        <button type="submit" className="button button--sm" disabled={busy} data-action="set-question-no">
          改题号
        </button>
        <button type="button" className="button button--sm" disabled={busy}
          onClick={() => onRun('question_no', {block_id: row.id, question_no: null})}
          data-action="clear-question-no">
          题号看不清（留空）
        </button>
      </form>

      <div className="ai-note-workbench__toolbar">
        {Object.keys(PROBLEM_TYPES).map((type) => (
          <button key={type} type="button" className="button button--sm" disabled={busy}
            onClick={() => onRun('type', {block_id: row.id, problem_type: type})}
            data-action={`type-${type}`}>
            {PROBLEM_TYPES[type]}
            {row.problem_type === type ? ' ✓' : ''}
          </button>
        ))}
      </div>
    </div>
  );
}

/** 上一次修正的回执。`blocks_removed` / `blocks_added` **含 0 都要报**。 */
function EditOutcomePanel({last}) {
  if (last.failed) {
    const bound = last.error?.code === 'block_delete_bound_to_card'
      || last.error?.reason === 'block_delete_bound_to_card';
    return (
      <FailurePanel
        title="这次修正没成"
        error={last.error}
        extra={
          bound
            ? '要「不要它」只能用「不收」（drop）：删块会让已经存在的那张题卡变成孤儿绑定。'
            : null
        }
      />
    );
  }
  return (
    <section className="ai-note-summary" data-edit-outcome="true" data-changed={String(last.changed)}>
      <p
        data-edit-blocks-removed={last.blocks_removed}
        data-edit-blocks-added={last.blocks_added}>
        这一趟删了 <strong>{last.blocks_removed}</strong> 块、新增了{' '}
        <strong>{last.blocks_added}</strong> 块（0 也报：每次删除都要报数）。
      </p>
      <p data-edit-counts>
        {last.changed
          ? `改动了 ${last.applied} 条${last.noop ? `，另有 ${last.noop} 条没动` : ''}`
          : '一条都没改动（幂等重复提交，或点的块本来就是这个样子）'}
      </p>
      <ul>
        {last.rows.map((row) => (
          <li key={`${row.index}-${row.action}`} data-edit-action={row.action}>
            {row.label}：{row.changed ? '已改' : '没动'}
            {row.blocks_changed.length ? `（块 ${row.blocks_changed.join('、')}）` : ''}
          </li>
        ))}
      </ul>
      <NoticeList warnings={last.warnings} skipped={last.skipped} />
    </section>
  );
}

/** 重跑切分：需要确认／跑完了／失败。`details.discarded` 照原话显示。 */
function ResegmentPanel({resegment, busy, onConfirm}) {
  if (resegment.needsConfirmation) {
    const discarded = resegment.error?.details?.discarded || null;
    return (
      <div className="ai-note-banner" role="alert"
        data-error-code={resegment.error?.code || 'resegment_needs_confirmation'}>
        <strong>重置为预设前要你确认（会丢掉人工改动）</strong>
        <p data-error-message="true">{resegment.error?.message}</p>
        {discarded ? (
          <>
            <p data-discarded="true">
              将丢弃：<DiscardedLine discarded={discarded} />
            </p>
            <p className="ai-note-meta">
              服务的原话：<code>{JSON.stringify(discarded)}</code>
            </p>
          </>
        ) : (
          <p className="ai-note-meta">
            服务没有说明会丢掉几处。
          </p>
        )}
        {resegment.error?.hint && <p className="ai-note-meta">怎么办：{resegment.error.hint}</p>}
        <button type="button" className="button button--danger" disabled={busy}
          onClick={onConfirm} data-action="confirm-resegment">
          确实要重置为预设（带上 confirm_discard_manual 重发）
        </button>
      </div>
    );
  }

  if (resegment.failed) {
    return <FailurePanel title="重跑切分没成" error={resegment.error} />;
  }

  const summary = resegmentSummary(resegment.data);
  const rows = resegmentRows(resegment.data);
  const cardWarnings = resegmentCardWarnings(resegment.data);
  const discarded = resegment.data?.discarded || null;

  return (
    <section className="ai-note-summary" data-resegment="true" data-ran={String(summary.ran)}>
      <h2>重跑切分的结果</h2>
      {summary.segmentation_unavailable ? (
        <p data-segmentation="unavailable">
          这一趟没有可用的切分（服务没接上切分那一层）→ 没有块可以对照。这里不显示假的块列表。
        </p>
      ) : (
        <>
          <p data-resegment-counts>{summary.line}</p>
          {discarded && (
            <p data-discarded="true">
              真的丢掉了：<DiscardedLine discarded={discarded} />
            </p>
          )}
          <ul className="ai-note-resegment-rows">
            {rows.map((row) => (
              <li key={row.block_id} data-block-id={row.block_id} data-state={row.state}>
                <strong>{row.label}</strong>
                <span className="ai-note-meta">
                  {` 块 ${row.block_id} · ${row.detail}`}
                  {row.card_id ? ` · 卡片 ${row.card_id}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {cardWarnings.map((warning) => (
        <p key={warning.message} className="ai-note-meta" data-warning-code={warning.code}>
          {warning.message}
        </p>
      ))}
      <NoticeList warnings={resegment.warnings} skipped={resegment.skipped} />
    </section>
  );
}

/**
 * 「将丢弃／真的丢掉了」那几个数：**服务给几个就报几个**。
 *
 * 契约 §9 与 §10.2.1b 对 `details.discarded` 的键说法不完全一样（前者两个、后者三个），
 * 所以这里不假设键一定在：缺的键显示成「服务没给」，**不补 0**——把「没给」写成 0
 * 就是替服务说「一处都不会丢」，而这是一次不可逆的决定。
 */
function DiscardedLine({discarded}) {
  const parts = [
    ['人工加的块', 'manual_blocks', '块'],
    ['删除留痕', 'removed_blocks', '条'],
    ['人改过的块', 'human_edited_blocks', '块'],
  ];
  return (
    <>
      {parts.map(([label, key, unit], index) => {
        const value = discarded[key];
        const known = value !== undefined && value !== null;
        return (
          <React.Fragment key={key}>
            {index > 0 ? ' · ' : ''}
            {label}：
            {known ? (
              <>
                <strong>{value}</strong> {unit}
              </>
            ) : (
              '（这个数服务没给）'
            )}
          </React.Fragment>
        );
      })}
    </>
  );
}

/** 入库：四类结果**逐条**显示，一条都不许吞。 */
function CommitPanel({commit}) {
  if (!commit.ok) {
    return <FailurePanel title="入库没成" error={commit.error} />;
  }
  const data = commit.data || {};
  const groups = [
    {key: 'created', title: '新建的题卡', rows: data.created || []},
    {key: 'reused', title: '已经生成过的块（没动它）', rows: data.reused || []},
    {key: 'skipped', title: '没有入库的块（没收／待定）', rows: data.skipped || []},
    {key: 'refused', title: '拒绝入库的块', rows: data.refused || []},
  ];
  return (
    <section className="ai-note-summary" data-commit="true">
      <h2>入库结果</h2>
      <p data-commit-blocks>
        块 {data.blocks?.total ?? 0} 块：收 {data.blocks?.kept ?? 0} / 不收{' '}
        {data.blocks?.dropped ?? 0} / 待定 {data.blocks?.pending ?? 0}
        {data.blocks?.not_an_object ? ` / 不是对象 ${data.blocks.not_an_object}` : ''}
        {' · '}索引里现在有 {data.index?.count ?? 0} 道题。
      </p>
      {groups.map((group) => (
        <div key={group.key} data-commit-group={group.key} data-commit-count={group.rows.length}>
          <strong>
            {group.title}：{group.rows.length} 条
          </strong>
          {group.rows.length === 0 ? (
            <p className="ai-note-meta">0 条。</p>
          ) : (
            <ul>
              {group.rows.map((row, index) => (
                <li key={`${group.key}-${row.block_id}-${index}`} data-commit-row={group.key}>
                  {group.key === 'created' && `块 ${row.block_id} → 题卡 ${row.card_id}（${row.path}）`}
                  {group.key === 'reused' && `块 ${row.block_id} → 题卡 ${row.card_id}`}
                  {group.key === 'skipped' &&
                    `块 ${row.block_id}（keep=${String(row.keep)}，rule=${row.rule ?? '没判过'}）：${row.reason}`}
                  {group.key === 'refused' &&
                    `块 ${row.block_id}：${row.reason}${row.message ? ` —— ${row.message}` : ''}`}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      <NoticeList warnings={commit.warnings} skipped={commit.skipped} />
    </section>
  );
}

/** 「建」那一趟的回执：服务的原话，外加这一页当场的样子。 */
function CreateNotice({notice}) {
  const row = notice.row;
  return (
    <section className="ai-note-summary" data-create-notice="true">
      <h2>建这一页的回执</h2>
      <p data-create-message>{row?.message || '（服务没有给这一页的话）'}</p>
      {row?.page_id && (
        <p className="ai-note-meta">
          页 <code>{row.page_id}</code> · {row.created ? '新建' : '已经存在'} ·{' '}
          {row.existing ? '照片再传一次就是同一页（不重跑切分）' : ''}
        </p>
      )}
      {notice.segmentation?.message && (
        <p className="ai-note-warn" data-segmentation-message="true">
          {notice.segmentation.message}
        </p>
      )}
      {row?.not_kept?.message && (
        <>
          {/* 「另有 M 道没有入库」这句原话由服务给（M=0 也照印） */}
          <p data-not-kept-message="true">{row.not_kept.message}</p>
          <ul data-not-kept={row.not_kept.count ?? 0}>
            {(row.not_kept.by_rule || []).map((entry) => (
              <li key={entry.rule} className="ai-note-meta">
                {entry.message}（{entry.count} 块：{(entry.ids || []).join('、') || '——'}）
              </li>
            ))}
          </ul>
        </>
      )}
      <NoticeList warnings={notice.warnings} skipped={notice.skipped} />
    </section>
  );
}

/** 服务给的 `warnings[]` / `skipped[]`：**一条都不许吞**（ADR 0007 第 6 条）。 */
function NoticeList({warnings = [], skipped = []}) {
  if (warnings.length === 0 && skipped.length === 0) {
    return (
      <p className="ai-note-meta" data-notice="empty">
        服务这一趟没有警告、也没有跳过任何记录。
      </p>
    );
  }
  return (
    <>
      {warnings.map((warning, index) => (
        <p
          key={`w-${warning.code}-${index}`}
          className={warning.level === 'hint' ? 'ai-note-hint' : 'ai-note-warn'}
          data-warning-code={warning.code}
          data-level={warning.level || 'warning'}>
          {warning.message}
          {warning.id ? <span className="ai-note-meta">（{warning.id}）</span> : null}
        </p>
      ))}
      {skipped.map((entry, index) => (
        <p
          key={`s-${entry.code}-${index}`}
          className="ai-note-warn"
          data-skipped-code={entry.code}>
          {entry.message}
          {entry.id ? <span className="ai-note-meta">（{entry.id}）</span> : null}
        </p>
      ))}
    </>
  );
}
