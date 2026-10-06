# `server/` —— 后端（契约 v0 的实现）

**契约是唯一必须跨得过去的东西**（[ADR 0007](../docs/adr/0007-redesign-the-backend-contract-first.md)
第 1 条）。契约文档在 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)——
改行为之前先改那份文档。

这个目录**不演化 `proto/`、不 import `proto/`**。`proto/` 是冻结的只读证据：
提示词、阈值、统计口径与实测结论被继承，代码不被继承。

## 跑

```bash
# 拿仓库里的**测试语料**跑（生产数据默认在用户数据目录，见 ../data/README.md 与 ADR 0008）；
# 题卡／资产／索引只读。
python3 -m server.app --data data --host 127.0.0.1 --port 8765

curl -s http://127.0.0.1:8765/api/index | python3 -m json.tool | head -40

# 写端点（#5）：作答进 → 判定出 → 回写。判定由**服务**自己做，客户端只提交作答。
curl -s -X POST http://127.0.0.1:8765/api/attempt/p-20261004-41c86b \
     -H 'Content-Type: application/json' \
     -d '{"channel":"screen","answer":"A"}' | python3 -m json.tool

# 定点修正（#6）：只改**既有那一次**重做的错因／判定。不新建记录、不重跑判定、不调模型。
# attempt_at 就是那一次记录的尝试时刻；找不到就 404，绝不落到最近一次。
curl -s -X POST http://127.0.0.1:8765/api/attempt/p-20261004-41c86b \
     -H 'Content-Type: application/json' \
     -d '{"attempt_at":"2026-10-04T09:39:57+00:00","verdict":"wrong","error_causes":["计算失误"]}' \
     | python3 -m json.tool
```

**切分角色是默认接上的**（#17 §8）：`main()` 起服务时会按配置构造一个真的
`HttpSegmenter` 注入进去。注意这条接线**故意不放在 `build_server` 里**——
`segmenter=None` 在本项目里有一个确切含义：**切分不可用**（那时页照建、`blocks` 是
`null`、人在照片上自己画框，见契约 §10.2.1）。测试要造的就是那一档，所以「不注入」
必须继续等于「不可用」。于是：**库的默认是「不可用」，产品（`python3 -m server.app`）
的默认是「可用」。**

连带的一件事：切分接缝上的**模型调用失败是 502**（契约 §10.3 与「最终修复 pass」那笔账），
所以**没有配密钥时 `POST /api/page` 会 502，而不是退回手动画框**。想走手动画框那条路，
要么配密钥，要么显式用不注入 segmenter 的库接口起服务（`build_server(..., segmenter=None)`）。
`get /api/index` 与手机上传页不受影响。

**存量题卡补科目**（#17 §10.4）：科目是导航的根，而旧卡没有这个字段。只认人给的映射表：

```bash
python3 -m server.subject_assign --data data --map 科目表.json           # 预演，一个字节都不写
python3 -m server.subject_assign --data data --map 科目表.json --apply   # 真写
# 映射表：{"p-20261004-41c86b": "数学"}；表里有一条不在 vocab/subjects.json 里就**一票否决**
```

**模型上岗验收**（`CONTEXT.md`「验收」：换模型就要重跑，不过考不许上岗）：

```bash
python3 -m pytest server/tests/test_acceptance_models.py -v
# 三关：视觉探针（合成图，不需要私人内容）／真实照片上的人眼判定（本机 data/pages/*.png）／
#       三条确定性对账判据。**没配密钥时是 skip 而不是 pass**——「没跑」不许看起来像「通过了」。
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
对外地址解析／状态机／页与切分对账）用**假时钟 + 构造记录／构造的块列表**喂进去测，
判定角色走注入的 stub——不联网、不花钱、不画图、不写真 `runs/`。

## 模块的角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `autojudge.py` | **「能不能自动判定」的唯一实现**（三个拒绝理由 + 优先级）。`#5` 的写端点**调用**它，不重写 |
| `judge.py` | **映射的唯一实现**：模型输出（等价 / 置信度 / 残缺）→ 三值判定。纯逻辑，脱网可测 |
| `mastery.py` | 掌握与冷却的**读数 + 状态机**。规则本体一处（`step`）：`apply_attempt`（#5，追加）与 `recompute_mastery`（#6，改判后重放整段历史）都从它走。冷却基准（从未重做过的以录入时间起算）、比较前归一化到 UTC、冷却门必须在更新 `last_attempt_at` **之前**算（`cooldown_gate` 就是那**一份**门）。`peek_step`（#6，只改错因时的**只读**读数）读同一份门，不改任何东西 |
| `amend.py` | 定点修正（#6）：按 `attempt_at` 定位**那一次**既有重做（0 个 → 404、≥2 个 → 409，绝不猜），只改 `verdict`／`error_causes`，审计字段只有它能写。掌握**按 payload 是否改判据分流**（裁决 D10）：改判 → `recompute_mastery` 重放重算；只改错因 → 一个字都不重算，卡上 `mastery` 与 `attempts[i].note` 逐字保留 |
| `config.py` | 装载处配置：provider 白名单、阈值校验一次（坏配置起不来）、`.env.local` 读取 |
| `judge_client.py` | 判定角色的调用接缝：纯文本提示词（不发图）、HTTP transport 可注入、留档 `runs/`、失败抛 `ModelUnavailable` |
| `segmenter_client.py` | **切分角色的调用接缝**（#17 §8）：整页照片 → 模型**原文**（**不解析**——解析归 `segmentation.parse_candidate_blocks` 一处）。提示词不在这里重写（`segmentation.SEGMENT_SYSTEM` 与解析它的代码必须一起改）。留档 tag `segment`。**前置一关是视觉探针**：纯文本模型收到图片不会报错，它会忽略图片、照着提示词**凭空编块**。一处已知的审计缺口记在它的 docstring 里：页文件的 `segmentation` 没有 provider/model/run_id，「这一页是哪个模型切的」目前只在留档里查得到 |
| `brief.py` | **简报的生成与数字闸门的唯一实现**（#17 §10.5）：汇总读数 → 问 `brief` 角色 → 把正文用到的**每一个数字**落成 `window_facts`／`history_facts`（`path` 指向本次索引）→ **逐条解出来核对**。任何一条解不出或对不上 → **不落盘** + 502 `brief_unverifiable`。闸门是纯函数（`verify_facts`），脱网可测。它与 `model_unavailable` 是两个码：一个重试无用、一个可以重试 |
| `brief_client.py` | `brief` 角色的调用接缝：**纯文本**（只看索引，一张图都不发），形状同 `judge_client`／`intake_client`；留档 tag `brief` |
| `attempt.py` | 写端点：客户端不碰判定、调用 autojudge/judge/mastery、原子回写题卡、索引重建 |
| `assets.py` | 题卡里的图片路径 → 磁盘文件 → 服务 URL。**一处实现**，顺带挡住路径穿越 |
| `ink.py` | **红笔痕迹阈值的唯一定义处 + 统计的唯一实现**（#11）：`COLORED_SATURATION_MIN`（像素级：一颗像素算不算红笔）与 `COLOR_MIN_PIXELS`（框级：一个框里几个像素才算有红笔）是**两颗**回答不同问题的常量，筛选/体检/擦除三条路径都读它们；深色掩膜的两颗也在这里。只回答「有没有红笔、多少」，**不做语义判断**（勾还是订正归 #12 的模型）。PNG 读写也在这里，用**标准库**（`zlib`），不引入 PIL/numpy |
| `warnings.py` | 会喊的检查（ADR 0007 第 6 条）：逐卡自检 + 索引级检查（串题、id 不一致） |
| `records.py` | **Problem 记录的唯一构造函数**：列表与详情由它产出，详情是它的超集 |
| `catalog.py` | 一个数据目录的访问：索引、一题、数据目录形状、对外地址拼法（`public_url`） |
| `subjects.py` | **科目与考点大纲的唯一读取实现**（#17）：`<数据目录>/vocab/subjects.json` 是「有哪些科目」的唯一来源，大纲是 `科目 → 章 → 节 → 点`；卡上 `subject` 的判据（未归类 = `hint`，表外 = `warning`，且**只在词表真读出来时**才报）与按科目汇总（`stats.by_subject`／`unclassified`，那条「一道题都不许少」的不变式就落在这里）都在这一处。按 **mtime** 记忆：文件一动就重读，不重启服务、也不用旧值 |
| `subject_assign.py` | 存量题卡 → 科目的**显式**迁移命令（`python3 -m server.subject_assign --map 表.json [--apply]`，默认预演）。只认人给的映射表，**不按考点去推**；映射表里有一条不在词表里就**一票否决**，`--apply` 一个字节都不写 |
| `pages.py` | **页的唯一实现**（B1 / #9）：页文件读写、`page_binding`（旧卡缺绑定 = 提示 vs 页↔卡对不上账 = 警告）、`rebind`（重切按位置重合保留绑定，**匹配只有这一处**）、`allocate_card_id`/`assign_card_ids`（首次入库时分配 id） |
| `segmentation.py` | **切分与对账**（B2 / #10）：模型候选块的解析与校验（拒块逐条给理由）、三条确定性判据（题号连续性／块重叠／覆盖率）、`reconcile` 的结构化结论、`classify_resegment` 的新增／保留对照。纯逻辑：不联网、不画图、不写题卡。警告一律是契约 §2 的 `Warning`（`{code, message, id, level}`，构造走 `warnings._warn` 那一处）；页级对账码表见契约 §8 |
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
  带了 `verdict`／`source`／`confidence`／`provider`／`model` 一律 400。定点修正形态
  （`{attempt_at, error_causes?, verdict?}`）**允许**人给 `verdict` 与 `error_causes`——
  那是 spec #1 US 16 的当场改判——但 `source`／`confidence`／`provider`／`model`／`overrode`
  仍然只有服务能写（改判后 `source` 变 `human`，原判定留在 `overrode` 里）。

## 不做

- **改「作答历史」的写端点只有 `POST /api/attempt/<pid>` 一个入口，现有两种形态**：
  屏幕重做（#5）与定点修正（#6）。#13 的 `POST /api/inbox` 只往收件目录放**新**文件，
  不动已有数据；页资源的四个动作在 `/api/page*`（写页文件与题卡，见下）。
- **不改 `proto/`**、**不落索引盘**（每次请求现算）。
- **不渲染页面**：`GET /upload` 是后端**托管**的一个静态文件，页面里没有一处服务端注入的值
  （ADR 0007 第 2 条：托管文件不是渲染）。
- **不监视收件目录**（`inotify` 在同步盘上不可靠）。所以 `POST /api/inbox/scan` 是它的
  手动等价入口；扫描响应里的 `watch.implemented: false` 就是在说这件事。
- **切分只有接缝，没有会真去问模型的实现**：`main()` 给的是 `segmenter=None`。于是
  `POST /api/inbox` 的响应里 `pipeline.segmentation.available` 是 `false`、
  `pages[].blocks` 是 `null`（**不是 `[]`**）、`committed` 是 `false`；建页与重切回
  `segmentation_not_implemented` 并明说原因——**不返回一个看起来能跑的假块列表**。
  接缝本身已经接好：三处（`inbox.run_pipeline`、`page_create`、`page_api.resegment`）
  都把 `ModelUnavailable` 翻成 502 `model_unavailable`、带上**哪一页**、不留下半截块。
  缺的只是那份会真问模型的实现——提示词与口径在 `proto/` 里，纯逻辑核心
  （`server/segmentation.py`：候选块解析与校验、三条对账判据、重切三态对照）已经落地。
- **纸上重做的形态**（`channel:"paper"`）还没实现，仍是 400。定点修正（`attempt_at`）已随 #6 落地。
- **页资源四个动作都已落地**（契约 §10.2）：`POST /api/page`（建）、`PATCH /api/page/<id>`（改）、
  `POST /api/page/<id>/resegment`（重切）、`POST /api/page/<id>/commit`（入库）、
  `GET /api/page/<id>/image`（页图）。现在**没有**「预留命名空间」了——`http.py` 的 `RESERVED`
  是空的，那条带说明的 404 只是留给以后用的机制。
