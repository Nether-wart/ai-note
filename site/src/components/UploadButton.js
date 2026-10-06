import React from 'react';
import {useWorkbench} from './Shell';

/**
 * 「上传整页照片」按钮：**只是开工作台的那一下**。
 *
 * 工作台本身是运行时组件（`components/Workbench.js`）：它要拿服务给的页、要 POST 照片、
 * 要跟着回执重画块框——所以它不能是 MDX（构建期编译不了运行时的东西）。
 * 但**开它的按钮**可以放在任何地方：开关住在 `ShellProvider` 里（`theme/Root.js` 挂在
 * 路由之上），所以 MDX 页面里的这个按钮与侧栏那个是同一件事、同一个工作台。
 *
 * 这也是「没有数据的页面长在 Docusaurus 框架上、有数据的留在运行时」这条线的落点：
 * 页面是 MDX，能力是运行时组件。
 */
export default function UploadButton({label = '上传整页照片', from = 'mdx'}) {
  const workbench = useWorkbench();
  return (
    <button
      type="button"
      className="button button--primary"
      onClick={() => workbench?.open(null)}
      disabled={!workbench}
      data-action="open-workbench"
      data-from={from}>
      {label}
    </button>
  );
}
