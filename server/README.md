# `server/` —— HTTP 层（契约 v0 + 一个写端点）

**契约是唯一必须跨得过去的东西**（[ADR 0007](../docs/adr/0007-redesign-the-backend-contract-first.md)
第 1 条）。契约文档在 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)——
改行为之前先改那份文档。

这个目录**不演化 `proto/`、不 import `proto/`**。`proto/` 是冻结的只读证据：
提示词、阈值、统计口径与实测结论被继承，代码不被继承。

## 跑

```bash
# 只监听本机，数据目录默认是仓库根的 data/
python3 -m server.app --data data --host 127.0.0.1 --port 8765

curl -s http://127.0.0.1:8765/api/index | python3 -m json.tool | head -40

# 写端点（#5）：作答进 → 判定出 → 回写。判定由**服务**自己做，客户端只提交作答。
curl -s -X POST http://127.0.0.1:8765/api/attempt/p-20261004-41c86b \
     -H 'Content-Type: application/json' \
     -d '{"channel":"screen","answer":"A"}' | python3 -m json.tool
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--data` | 仓库根的 `data/`（可用 `AI_NOTE_DATA` 覆盖） | 数据目录。只有 `POST /api/attempt/<pid>` 会往里写 |
| `--host` | `127.0.0.1` | **默认只监听本机**（ADR 0003） |
| `--port` | `8765` | `0` = 让系统挑一个空闲端口（测试用） |
| `--public-base` | 按**绑定之后**的 `host:port` 推导 | **对外可达地址**（手机要打开的链接、页锚点）。不许从 `--host` 猜（ADR 0007 第 5 条记着这处债）。它是可读的：`GET /api/index` 的 `data.server.public_base` |
| `--inbox` | `data/inbox/` | 收件目录（「往这里放一个文件」就是录入）。v0 只报出来，不监视——归 #13 |

环境变量（`.env.local` 可覆盖，见 `.env.local.example`）：`JUDGE_PROVIDER` / `JUDGE_MODEL` /
`JUDGE_THRESHOLD`。**坏配置起不来**（provider 不在 `dashscope`/`deepseek` 白名单、
阈值 NaN／无穷／越界 → 退出码 2），不是每个请求里再验一遍。缺密钥不是启动失败：
调用那一刻给 502 `model_unavailable`，可以重试。

## 测试

```bash
python3 -m pytest server/tests -q
```

测试数据一律**自造在临时目录里**、测完即删；真实题卡只有两张、重做次数是 0，
测试绝不碰它们。唯一的被测接缝是 HTTP：`server.http.Api.handle(method, target, body=None)`，
另有一条真起 socket 的冒烟测试。纯逻辑（冷却／排序／可判性／警告码／状态机）用**假时钟 + 构造记录**
喂进去测，判定角色走注入的 stub——不联网、不花钱、不画图、不写真 `runs/`。

## 模块的角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `autojudge.py` | **「能不能自动判定」的唯一实现**（三个拒绝理由 + 优先级）。`#5` 的写端点**调用**它，不重写 |
| `judge.py` | **映射的唯一实现**：模型输出（等价 / 置信度 / 残缺）→ 三值判定。纯逻辑，脱网可测 |
| `mastery.py` | 掌握与冷却的**读数 + 状态机**（`apply_attempt`）。冷却基准（从未重做过的以录入时间起算）、比较前归一化到 UTC、冷却必须在更新 `last_attempt_at` **之前**算 |
| `config.py` | 装载处配置：provider 白名单、阈值校验一次（坏配置起不来）、`.env.local` 读取 |
| `judge_client.py` | 判定角色的调用接缝：纯文本提示词（不发图）、HTTP transport 可注入、留档 `runs/`、失败抛 `ModelUnavailable` |
| `attempt.py` | 写端点：客户端不碰判定、调用 autojudge/judge/mastery、原子回写题卡、索引重建 |
| `assets.py` | 题卡里的图片路径 → 磁盘文件 → 服务 URL。**一处实现**，顺带挡住路径穿越 |
| `ink.py` | **红笔痕迹阈值的唯一定义处 + 统计的唯一实现**（#11）：`COLORED_SATURATION_MIN`（像素级：一颗像素算不算红笔）与 `COLOR_MIN_PIXELS`（框级：一个框里几个像素才算有红笔）是**两颗**回答不同问题的常量，筛选/体检/擦除三条路径都读它们；深色掩膜的两颗也在这里。只回答「有没有红笔、多少」，**不做语义判断**（勾还是订正归 #12 的模型）。PNG 读写也在这里，用**标准库**（`zlib`），不引入 PIL/numpy |
| `warnings.py` | 会喊的检查（ADR 0007 第 6 条）：逐卡自检 + 索引级检查（串题、id 不一致） |
| `records.py` | **Problem 记录的唯一构造函数**：列表与详情由它产出，详情是它的超集 |
| `catalog.py` | 一个数据目录的访问：索引、一题、数据目录形状 |
| `http.py` | 路由 + 信封（`Api.handle` 是纯函数，不碰 socket） |
| `app.py` | CLI 与 `http.server` 接线（含 POST body 的读取） |

## 三件容易搞错的事

- **`data/index.json` 是 proto 遗留物**：新服务**不写它、不读它、不删它**（索引每次请求现算，
  理由见 ADR 0001）。磁盘上那个文件（及其旧形状）保持原样——它是私人数据，也是历史证据。
  **形状基准是 `docs/contracts/http-api-v0.md`，不是那个文件**；客户端一律经 HTTP 读索引。
- **屏幕重做的两个数字有两套口径**：`data.screen_redo.bases` 里
  `in_default_list`（开关没勾）与 `including_cooling`（勾了「显示冷却中的题」）都算好了，
  界面按开关取，不许重算。`stats.*` 的分母是**全部题卡**，与它们不是一回事。
- **客户端不碰判定**：`POST /api/attempt/<pid>` 的屏幕重做形态只收 `{channel, answer}`；
  带了 `verdict`／`source`／`confidence`／`provider`／`model` 一律 400。

## v0 不做

- **纸上重做与定点修正的形态**（`channel:"paper"` 与 `attempt_at`）归 #6；
  `POST` 到 `/api/page*`、`/api/inbox*` 仍是「预留、还没实现」的 404。
- **不改 `proto/`**、**不落索引盘**（每次请求现算）、**不出 HTML**（ADR 0007 第 2 条）。
