# 错题本

单用户的错题资料库：把做错的题录进来、归因打标，再用**纸上重做**与**屏幕重做**两条通道反复重做，
直到掌握。数据全部留在自己机器上，只有模型调用会离开本机，且限境内服务（[ADR 0003](docs/adr/0003-single-user-local-first.md)）。

两条先读这个再读别的：

- **项目用语以 [`CONTEXT.md`](CONTEXT.md) 为准。** 「重做」「判定」「看不清」「正解」「标准答案」
  在这个仓库里有精确含义，那份术语表用 40 条 `_Avoid_` 明令禁掉了一批近义词；文档、代码与界面都用这套词。
- **给 AI 助手看的入口是 [`AGENTS.md`](AGENTS.md)**（指向 `docs/agents/` 里的约定）。

## 这个仓库里同时住着两件事

| | |
|---|---|
| **`server/` + `site/`** —— 活的实现 | 后端从零重建（Python，零第三方依赖）＋界面壳（Docusaurus 静态站）。契约先行：界面与后端之间只认 [`docs/contracts/http-api-v0.md`](docs/contracts/http-api-v0.md) |
| **`proto/`** —— 冻结的证据 | 纵向切片原型，十轮实测。提示词、阈值、统计口径由 `server/` 继承，**代码不被继承**——它是只读证据，一行都不要改 |

## 目录地图

| 路径 | 是什么 | 进仓库 | 从哪读起 |
|---|---|---|---|
| `server/` | 后端：HTTP 层、判定、掌握状态机、页与切分、收入决策 | ✅ | [`server/README.md`](server/README.md) |
| `site/` | 界面：清单页 / 重做页 / 切分修正页 | ✅ | [`site/README.md`](site/README.md) |
| `docs/` | 规格、ADR、契约、验收记录 | ✅ | [`docs/README.md`](docs/README.md) |
| `data/` | 你的资料：题卡、照片、页文件、词表 | ⚠️ 只有词表种子 | [`data/README.md`](data/README.md) |
| `proto/` | 冻结的原型证据（README 里有它的结论） | ✅ | [`proto/README.md`](proto/README.md) |
| `samples/` | 原型用的真实手写照片 | ❌ 只有 README | [`samples/README.md`](samples/README.md) |
| `runs/` | 模型调用留档（跑过才有；契约 §10.1） | ❌ | — |
| `.env.local` | 密钥与本地配置 | ❌ | [`.env.local.example`](.env.local.example) |
| `CONTEXT.md` | 术语表：项目用语的唯一真相 | ✅ | 直接读 |
| `AGENTS.md` | 给 AI 助手的入口（指向 `docs/agents/`） | ✅ | — |
| `pytest.ini` | 测试配置（`proto/` 撞名那道护栏在这里） | ✅ | — |

## 跑起来

```bash
# 1) 后端：默认只监听本机，数据目录默认 ./data
python3 -m server.app --data data --host 127.0.0.1 --port 8765

# 2) 界面：另开一个终端。依赖装在本地缓存里，别让 npm 写到仓库外
cd site
npm_config_cache="$PWD/../.npm-cache" npm ci
AI_NOTE_API=http://127.0.0.1:8765 npm run start -- --port 3000 --host 127.0.0.1
```

模型调用要密钥：`cp .env.local.example .env.local`，再填 `DEEPSEEK_API_KEY`（或 `DASHSCOPE_API_KEY`）。
**不填也能把服务跑起来**：会用到模型的路径都明确报错，不静默降级。

```bash
# 后端测试（server/ 零第三方依赖，直接跑）
python3 -m pytest -q

# 界面测试：纯逻辑 + 真渲染出来的 HTML 的不变量自检
node --test site/tests/
node site/tests/selftest.mjs
```

手机上录入、公开地址、切分修正界面等更细的用法都在 [`server/README.md`](server/README.md) 与
[`site/README.md`](site/README.md) 里。

## 隐私边界（有些目录 clone 下来是空的，不是坏了）

这个仓库是**公开的**（<https://github.com/Nether-wart/ai-note>）。真实学生的手写作业、订正批注、
题卡、打印批次与模型调用留档**一律不进仓库**——`.gitignore` 里写着理由：git 历史很难真正擦干净。

所以 `data/` 下只有两个词表种子是提交的，`samples/` 只有 README，`runs/` 与 `.env.local` 被拦住。
每一项是哪一类、为什么这样切，见 [`data/README.md`](data/README.md)。

## 现在的状态

`server/` 与 `site/` 是在 [PR #16](https://github.com/Nether-wart/ai-note/pull/16) 里落地的
（一次交付 13 张工单，issue [#3](https://github.com/Nether-wart/ai-note/issues/3)–[#15](https://github.com/Nether-wart/ai-note/issues/15)）。
**如果你的 `main` 上还没有这两个目录，就是那张 PR 还没合并。**

两份规格住在 issue 里（[#1 屏幕重做](https://github.com/Nether-wart/ai-note/issues/1)、
[#2 整页切分与错题筛选](https://github.com/Nether-wart/ai-note/issues/2)）；
`docs/specs/` 只放指针、不放副本——**两份副本会漂移**，而这个项目吃过这个亏。
