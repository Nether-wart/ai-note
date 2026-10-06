import React from 'react';
import Link from '@docusaurus/Link';
import {useLocation} from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {UNCLASSIFIED, VIEWS, parseSubjectQuery, subjectUrl} from '../lib/routes';
import {sidebarTree} from '../lib/tree';
import {useIndex} from '../lib/shell-context';

/**
 * 左侧栏：**运行时自绘的科目索引**（ADR 0009）。
 *
 * 它为什么不是 Docusaurus 的文档侧栏：docs 插件在本项目里是**关掉的**（ADR 0009），
 * 而索引是运行时数据——进构建产物就与 ADR 0001 冲突。所以这棵树由组件画，
 * 但视觉向文档侧栏看齐：用主题的 `--ifm-*` 变量与 `menu` / `menu__list` 一类类名，
 * 不引入任何新依赖。
 *
 * 树的形状（`sidebarTree()`）：
 *
 *   首页总览
 *   科目 ─〔简报｜细则｜考点大纲｜今日重做〕
 *   未归类（道数写在栏上）
 *
 * 四条「不许静默」的落法：
 *   1. **未归类**永远在，道数照服务给的写（0 也写）；
 *   2. **表外科目**（`offVocabulary`）标出来——它是「卡上的值不在受控词表里」，
 *      不是「藏起来」；
 *   3. 服务没给某个科目的桶（`countsMissing`）时说「读数缺失」，**不许装作 0 道**；
 *   4. **简报过期**照 `brief.stale` / `brief.new_problems` 显示，
 *      那句话里的 N 照抄服务给的数，界面不自己算。
 */
export default function Sidebar({onUpload}) {
  const index = useIndex();
  const location = useLocation();
  const {siteConfig} = useDocusaurusContext();
  const baseUrl = siteConfig.baseUrl || '/';

  const pathname = (location?.pathname || '/').replace(/index\.html$/, '');
  const search = location?.search || '';
  const parsed = parseSubjectQuery(search);
  const onSubjectPage = pathname === '/subject' || pathname === '/subject/';
  const current = onSubjectPage && !parsed.error ? parsed : null;
  const onOverview = pathname === baseUrl || pathname === '/' || pathname === '';

  const data = index?.phase === 'ready' ? index.data : null;
  const tree = data ? sidebarTree(data) : null;

  return (
    <nav className="menu ai-note-sidebar" aria-label="科目索引">
      <div className="ai-note-sidebar__top">
        <button
          type="button"
          className="button button--primary button--block"
          onClick={onUpload}
          data-action="open-workbench"
          data-from="sidebar">
          上传
        </button>
        <p className="ai-note-meta ai-note-sidebar__hint">
          上传整页照片 → 切分 → 人工调整 → 入库。
        </p>
      </div>

      {index?.phase === 'loading' && (
        <p className="ai-note-meta ai-note-sidebar__note" data-status="loading">
          正在从服务读索引…
        </p>
      )}
      {index?.phase === 'failed' && (
        <p className="ai-note-warn ai-note-sidebar__note" data-status="failed">
          索引读不到：这一栏画不出来（失败的原话在右边横幅里，可以重试）。
        </p>
      )}

      {tree && (
        <ul className="menu__list ai-note-sidebar__list">
          <li className="menu__list-item">
            <Link
              className={onOverview ? 'menu__link menu__link--active' : 'menu__link'}
              to="/"
              data-sidebar-item="overview">
              首页总览
            </Link>
          </li>

          {tree.subjects.map((subject) => (
            <SubjectItem key={subject.name} subject={subject} current={current} />
          ))}

          {/*
            未归类是**一等状态**，不是空名字：它永远在栏上、道数永远写出来。
            它是**一项**，不是科目（简报与考点大纲都属于科目），所以直接链到它的细则；
            另外三栏照旧能打开（地址里给 `view=` 即可），只是侧栏不替它画四栏。
          */}
          <li className="menu__list-item" data-sidebar-item="unclassified">
            <Link
              className={
                current && current.name === null && current.view === 'detail'
                  ? 'menu__link menu__link--active'
                  : 'menu__link'
              }
              to={subjectUrl(null, 'detail')}
              data-unclassified-count={tree.unclassified.count}>
              {UNCLASSIFIED}（{tree.unclassified.count} 道）
            </Link>
          </li>
        </ul>
      )}
    </nav>
  );
}

/** 一个科目一项，展开就是它的四栏（栏位与顺序都取 `VIEWS`，界面不另排一套）。 */
function SubjectItem({subject, current}) {
  const activeHere = current && current.name === subject.name;
  const brief = subject.brief;

  return (
    <li className="menu__list-item" data-sidebar-item="subject" data-subject={subject.name}>
      <div className="menu__list-item-collapsible">
        <Link
          className={
            activeHere && current.view === VIEWS[0].key
              ? 'menu__link menu__link--sublist menu__link--active'
              : 'menu__link menu__link--sublist'
          }
          to={subjectUrl(subject.name, VIEWS[0].key)}>
          {subject.label}
          {subject.countsMissing ? (
            <span className="ai-note-sidebar__count" data-counts-missing="true">
              读数缺失
            </span>
          ) : (
            <span className="ai-note-sidebar__count" data-subject-count={subject.counts.problems}>
              {subject.counts.problems}
            </span>
          )}
        </Link>
      </div>

      <ul className="menu__list">
        {VIEWS.map((view) => (
          <li className="menu__list-item" key={`${subject.name}-${view.key}`}>
            <Link
              className={
                activeHere && current.view === view.key
                  ? 'menu__link menu__link--active'
                  : 'menu__link'
              }
              to={subjectUrl(subject.name, view.key)}
              data-sidebar-item={`subject:${view.key}`}>
              {view.label}
            </Link>
          </li>
        ))}
      </ul>

      {/* 卡上的值不在受控词表里：标出来，**不隐藏**（隐藏等于第二次静默）。 */}
      {subject.offVocabulary && (
        <p className="ai-note-warn ai-note-sidebar__note" data-off-vocabulary="true">
          这个科目不在受控词表里（卡上的值不在表里，不是没有这个科目）
        </p>
      )}
      {subject.countsMissing && (
        <p className="ai-note-warn ai-note-sidebar__note" data-counts-missing-note="true">
          服务没给这个科目的读数（不是 0 道）。
        </p>
      )}
      {/* 简报过期：N 照抄 `brief.new_problems`，界面不自己数一遍。 */}
      {brief?.stale && (
        <p className="ai-note-warn ai-note-sidebar__note" data-brief-stale="true">
          简报已过期：有 {brief.new_problems} 道新题没进去
        </p>
      )}
    </li>
  );
}
