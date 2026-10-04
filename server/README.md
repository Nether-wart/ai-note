# `server/` —— 只读 HTTP 层（契约 v0）

**契约是唯一必须跨得过去的东西**（[ADR 0007](../docs/adr/0007-redesign-the-backend-contract-first.md)
第 1 条）。契约文档在 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)——
改行为之前先改那份文档。

这个目录**不演化 `proto/`、不 import `proto/`**。`proto/` 是冻结的只读证据：
提示词、阈值、统计口径与实测结论被继承，代码不被继承。

## 跑

```bash
# 只监听本机，数据目录默认是仓库根的 data/（v0 只读，不写任何文件）
python3 -m server.app --data data --host 127.0.0.1 --port 8765

curl -s http://127.0.0.1:8765/api/index | python3 -m json.tool | head -40
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--data` | 仓库根的 `data/`（可用 `AI_NOTE_DATA` 覆盖） | 数据目录。只读 |
| `--host` | `127.0.0.1` | **默认只监听本机**（ADR 0003） |
| `--port` | `8765` | `0` = 让系统挑一个空闲端口（测试用） |
| `--public-base` | 按**绑定之后**的 `host:port` 推导 | **对外可达地址**（手机要打开的链接、页锚点）。不许从 `--host` 猜（ADR 0007 第 5 条记着这处债）。它是可读的：`GET /api/index` 的 `data.server.public_base` |
| `--inbox` | `data/inbox/` | 收件目录（「往这里放一个文件」就是录入）。v0 只报出来，不监视——归 #13 |

## 测试

```bash
python3 -m pytest server/tests -q
```

测试数据一律**自造在临时目录里**、测完即删；真实题卡只有两张、重做次数是 0，
测试绝不碰它们。唯一的被测接缝是 HTTP：`server.http.Api.handle(method, target)`，
另有一条真起 socket 的冒烟测试。纯逻辑（冷却／排序／可判性／警告码）用**假时钟**喂进去测，
不联网、不花钱、不画图。

## 模块的角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `autojudge.py` | **「能不能自动判定」的唯一实现**（三个拒绝理由 + 优先级）。`#5` 的写端点**必须调用它**；界面只显示 `reason_text` |
| `mastery.py` | **掌握与冷却的只读读数**：冷却基准（从未重做过的以录入时间起算）、比较前归一化到 UTC |
| `assets.py` | 题卡里的图片路径 → 磁盘文件 → 服务 URL。**一处实现**，顺带挡住路径穿越 |
| `warnings.py` | 会喊的检查（ADR 0007 第 6 条）：逐卡自检 + 索引级检查（串题、id 不一致） |
| `records.py` | **Problem 记录的唯一构造函数**：列表与详情由它产出，详情是它的超集 |
| `catalog.py` | 一个数据目录的只读访问：索引、一题、数据目录形状 |
| `http.py` | 路由 + 信封（`Api.handle` 是纯函数，不碰 socket） |
| `app.py` | CLI 与 `http.server` 接线 |

## 两件容易搞错的事

- **`data/index.json` 是 proto 遗留物**：新服务**不写它、不读它、不删它**（索引每次请求现算，
  理由见 ADR 0001）。磁盘上那个文件（及其旧形状）保持原样——它是私人数据，也是历史证据。
  **形状基准是 `docs/contracts/http-api-v0.md`，不是那个文件**；客户端一律经 HTTP 读索引。
- **屏幕重做的两个数字有两套口径**：`data.screen_redo.bases` 里
  `in_default_list`（开关没勾）与 `including_cooling`（勾了「显示冷却中的题」）都算好了，
  界面按开关取，不许重算。`stats.*` 的分母是**全部题卡**，与它们不是一回事。

## v0 不做

- **任何写端点**（`POST /api/attempt/<pid>` 的形状已在契约 §10.1 预留，实现归 #5）。
  `GET` 到预留命名空间会返回一句明说「预留、还没实现」的 404。
- **不改 `proto/`**、**不落索引盘**（每次请求现算）、**不出 HTML**（ADR 0007 第 2 条）。
