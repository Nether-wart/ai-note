# `server/` —— HTTP 层（契约 v0 + 一个写端点）

**契约是唯一必须跨得过去的东西**（[ADR 0007](../docs/adr/0007-redesign-the-backend-contract-first.md)
第 1 条）。契约文档在 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)——
改行为之前先改那份文档。

这个目录**不演化 `proto/`、不 import `proto/`**。`proto/` 是冻结的只读证据：
提示词、阈值、统计口径与实测结论被继承，代码不被继承。

## 跑

```bash
# 只监听本机，数据目录默认是仓库根的 data/（题卡／资产／索引只读）
python3 -m server.app --data data --host 127.0.0.1 --port 8765

curl -s http://127.0.0.1:8765/api/index | python3 -m json.tool | head -40

# 写端点（#5）：作答进 → 判定出 → 回写。判定由**服务**自己做，客户端只提交作答。
curl -s -X POST http://127.0.0.1:8765/api/attempt/p-20261004-41c86b \
     -H 'Content-Type: application/json' \
     -d '{"channel":"screen","answer":"A"}' | python3 -m json.tool
```

**手机上录入**（局域网，ADR 0007 第 4 条）：绑通配地址，并把**手机能打开的地址**显式给它——
不给的话启动日志与索引里都会警告手机打不开（#13 验收 2）：

```bash
python3 -m server.app --host 0.0.0.0 --port 8765 --public-base http://192.168.1.50:8765
# 手机浏览器打开上面印出来的 http://192.168.1.50:8765/upload，拍照 → 上传
# 手动扫一遍收件目录（目录监视没有实现，这是它的等价入口）：
curl -X POST http://127.0.0.1:8765/api/inbox/scan
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--data` | 仓库根的 `data/`（可用 `AI_NOTE_DATA` 覆盖） | 数据目录。题卡／资产／索引只读；写端点只写这里面的题卡与收件目录 |
| `--host` | `127.0.0.1` | **默认只监听本机**（ADR 0003）。要让手机连上来用 `0.0.0.0`，同时**必须**给 `--public-base` |
| `--port` | `8765` | `0` = 让系统挑一个空闲端口（测试用） |
| `--public-base`（`AI_NOTE_PUBLIC_BASE`） | 按**绑定之后**的 `host:port` 推导 | **对外可达地址**（上传页链接、页锚点）。不许从 `--host` 猜（ADR 0007 第 5 条记着这处债）。读在 `GET /api/index` 的 `data.server.public_base` / `upload_url`；推出来的地址别的设备打不开时，启动日志与索引里都会出现 `public_base_not_reachable` 警告 |
| `--inbox`（`AI_NOTE_INBOX`） | **数据目录**下面的 `inbox/`（跟着 `--data` 走） | 收件目录（「往这里放一个文件」就是录入）。不监视——手动等价入口是 `POST /api/inbox/scan` |

> 上面这几个 `AI_NOTE_*` 可以走 shell（`AI_NOTE_PUBLIC_BASE=... python3 -m server.app`），
> 也可以写进 `.env.local`——`main()` 在**建 parser 之前**先把 `.env.local` 灌进环境，
> 所以文件里的值对这三个参数同样生效（顺序有讲究：parser 的默认值是现算的）。

## 端点

| 路径 | 用途 |
|---|---|
| `GET /api/index` · `GET /api/problem/<pid>` · `GET /api/problem/<pid>/image/<kind>` | 只读三件套（契约 §3/§5/§7） |
| `GET /upload` | 手机上传页：后端托管的**单文件** HTML，无构建步骤、不引外部资源（#13） |
| `POST /api/inbox` | `multipart/form-data`，part 名 `file`；一个 part = 一页，多个 = 多页（#13） |
| `POST /api/inbox/scan` | 手动扫一遍收件目录（目录监视失效时的等价入口，#13） |

环境变量（`.env.local` 可覆盖，见 `.env.local.example`）：`JUDGE_PROVIDER` / `JUDGE_MODEL` /
`JUDGE_THRESHOLD`。**坏配置起不来**（provider 不在 `dashscope`/`deepseek` 白名单、
阈值 NaN／无穷／越界 → 退出码 2），不是每个请求里再验一遍。缺密钥不是启动失败：
调用那一刻给 502 `model_unavailable`，可以重试。

## 测试

```bash
python3 -m pytest server/tests -q
```

测试数据一律**自造在临时目录里**、测完即删；真实题卡只有两张、重做次数是 0，
测试绝不碰它们。唯一的被测接缝是 HTTP：
`server.http.Api.handle(method, target, body, content_type)`，
另有一条真起 socket 的冒烟测试（PUT/POST 的 body 读取、上传、扫描、413 都在真 socket 上
验过一遍，端口用 `port=0` 让系统挑，不占固定端口）。纯逻辑（冷却／排序／可判性／警告码／
对外地址解析／状态机）用**假时钟 + 构造记录**喂进去测，判定角色走注入的 stub——
不联网、不花钱、不画图、不写真 `runs/`。

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
| `catalog.py` | 一个数据目录的访问：索引、一题、数据目录形状、对外地址拼法（`public_url`） |
| `publicbase.py` | **对外可达地址的唯一实现**：显式优先、否则按绑定之后的 `host:port` 推导；推出来的地址打不开就喊（ADR 0007 第 5 条） |
| `inbox.py` | **收件目录与管道接缝**：收文件（内容哈希命名）、手动扫描、multipart 解析；切分（#10）的接缝在这里，没接上就报 `segmentation_not_implemented` |
| `static/upload.html` | 手机上传页（单文件） |
| `http.py` | 路由 + 信封（`Api.handle` 是纯函数，不碰 socket） |
| `app.py` | CLI 与 `http.server` 接线（读 `Content-Length`、上传上限在读之前判、`.env.local` 在建 parser 之前灌进环境） |

## 三件容易搞错的事

- **`data/index.json` 是 proto 遗留物**：新服务**不写它、不读它、不删它**（索引每次请求现算，
  理由见 ADR 0001）。磁盘上那个文件（及其旧形状）保持原样——它是私人数据，也是历史证据。
  **形状基准是 `docs/contracts/http-api-v0.md`，不是那个文件**；客户端一律经 HTTP 读索引。
- **屏幕重做的两个数字有两套口径**：`data.screen_redo.bases` 里
  `in_default_list`（开关没勾）与 `including_cooling`（勾了「显示冷却中的题」）都算好了，
  界面按开关取，不许重算。`stats.*` 的分母是**全部题卡**，与它们不是一回事。
- **客户端不碰判定**：`POST /api/attempt/<pid>` 的屏幕重做形态只收 `{channel, answer}`；
  带了 `verdict`／`source`／`confidence`／`provider`／`model` 一律 400。

## 不做

- **改已有数据的写端点只有屏幕重做那一种形态**（`POST /api/attempt/<pid>`，归 #5）。
  `GET`/`POST` 到没落地的预留命名空间会返回一句明说「预留、还没实现」的 404。
  #13 的 `POST /api/inbox` 只往收件目录放**新**文件。
- **不改 `proto/`**、**不落索引盘**（每次请求现算）。
- **不渲染页面**：`GET /upload` 是后端**托管**的一个静态文件，页面里没有一处服务端注入的值
  （ADR 0007 第 2 条：托管文件不是渲染）。
- **不监视收件目录**（`inotify` 在同步盘上不可靠）。所以 `POST /api/inbox/scan` 是它的
  手动等价入口；扫描响应里的 `watch.implemented: false` 就是在说这件事。
- **不做切分与入库**：切分归 #10、页实体归 #9、入库归 #9/#15。所以
  `POST /api/inbox` 的响应里 `pipeline.segmentation.available` 是 `false`、
  `pages[].blocks` 是 `null`（**不是 `[]`**）、`committed` 是 `false`——
  没有块列表、没有红笔统计、没有「已入库」。`inbox.py` 里的 `segmenter` 就是 #10 接上来的口子。
- **纸上重做与定点修正的形态**（`channel:"paper"` 与 `attempt_at`）归 #6；
  `POST` 到 `/api/page*` 仍是「预留、还没实现」的 404。
