# 纵向切片原型

只验证整张设计里**唯一真正的未知**：AI 能不能可靠地从一张真实的手写照片里，抽出可用的问题卡，并判出重做答案的对错。其余全是已知工作量的堆砌。

刻意不做：不碰 PaddleOCR、不碰 Docusaurus、不碰数据库、不做界面、不做多租户。就是不碰。

## 它要证明的三件事

| # | 要证明的 | 通过标准 |
|---|---|---|
| 1 | **抽取**：从一张手写照片拿到 题面转录 / 原答 / 原解 / 正解 / 标准答案 / 标签 | 真实照片上，一半以上字段你不用改 |
| 2 | **校对素材**：模型给的 bbox 与题面转录能支撑审核对照 | 裁剪不缺边（`cropcheck` 不报警）；转录与照片逐字对照后无需修改 |
| 3 | **等价性**：屏幕重做敲进去的答案与标准答案的等价比对可靠 | 模型自出题 + 人手写固定考卷，**两份全对** |

> #2 的通过标准在 [ADR 0004](../docs/adr/0004-redo-sheet-prints-transcript.md) 之后变了：重做纸改印转录文字，所以裁剪图**不再是打印素材**，而是审核对照用的素材（以及含图形的题的备选）。原来那条"裁出来的图能直接当打印素材"已经不成立。

第 1 项若失败——模型读不出你的手写错解——问题卡就抽不出来，录入这条路要换走法。这正是要提前知道的事，而不是在写完界面之后。

> **已停用：`judge` 子命令（拍照自动判定）**
> 第 1 轮复核翻掉了 D2：纸上重做改为「手动标记 + 页锚点直达」，不再拍照自动判定。`judge` 保留在代码里只因为你可能某天想回头验证那条路径，正常流程不会用到它。
> 因此样例里的第 3 张（第二次重做）不再是必需——但它仍然有用：用真实场景验等价性判定，比纯文本用例更接近实战。

## 怎么跑

```bash
# 0. 把 key 写进 .env.local（不要提交、不要贴进对话）
echo 'DEEPSEEK_API_KEY=sk-xxxx' > .env.local

# 1. 看按角色解析出来的配置，并核实视觉模型的确切型号
python3 proto/slice.py models

# 2. 验收第一关：视觉探针（不过考不许上岗）
python3 proto/slice.py probe --role both

# 3. 抽一道题（默认走 extract 角色）
python3 proto/slice.py extract samples/某一页.jpg --note "照片里是第 12 题"

# 4. 判定角色的通过标准：模型自出题 + 人手写的固定考卷，两份都要过
python3 proto/slice.py equiv data/problems/p-xxxxxx.json
python3 proto/slice.py equiv --cases proto/fixtures/cases-radical.json
python3 proto/slice.py equiv --cases proto/fixtures/cases-interval.json

# 5.（已停用）拍照判定：D2 翻案后纸上走手动标记，留着只为将来回头验证
# python3 proto/slice.py judge data/problems/p-xxxxxx.json samples/重做.jpg

# 6. 裁剪框体检：查框切掉内容、或框里混进彩笔手写（订正最容易被印上重做纸）
python3 proto/slice.py cropcheck                     # 查全部
python3 proto/slice.py cropcheck data/problems/p-xxxxxx.json
```

固定考卷在 `proto/fixtures/`，用例由人手写（根式的符号陷阱、区间的端点开闭）。之所以要另配一份，是因为模型自出的用例有"自出自考"的偏向——它会往自己擅长的方向出题。验收结果记在 [docs/acceptance-log.md](../docs/acceptance-log.md)。

## 模型：按角色配 + 验收上岗

两个角色：**extract**（读照片、解正解、打标，也含"手写在哪"这一定位任务）与 **judge**（等价比对）。各自一个模型、各自一份验收。当前两个角色的默认都指向 `deepseek-flash`（DeepSeek-V4.1-Flash，`/models` 里声明 `input_modalities: ["text","image"]`）。

```bash
# 换模型：环境变量或命令行覆盖，都不用改代码
EXTRACT_PROVIDER=deepseek EXTRACT_MODEL=deepseek-flash \
  python3 proto/slice.py extract samples/x.jpg

python3 proto/slice.py equiv --judge-model deepseek-flash data/problems/p-xxxx.json
```

provider 是一份**白名单常量**（`dashscope`、`deepseek`，都在境内）。单用户之下它防的不是别人，是你自己手滑——手写照片一旦出境就收不回来；指到白名单外会直接拒绝启动。

`probe` 防的是另一种更阴的失败：配置指到纯文本模型时它**不会报错**，它只会忽略图片、转而照着提示词把题卡编出来。探针用一张写着随机六位数的图问它读到了什么，读不出来就不许上岗。

原型不会拦着你硬上——但那样的题卡会带着幻觉进入你的复习流程，而幻觉最贵的地方在于它看起来完全正常。

## 清单页 / 标记页：审核 + 掩膜编辑 + 活页纸 + 判定回写（第一个界面，也是写入服务的第一个端点）

```bash
python3 proto/server.py --port 8765
#   清单页（审核 + 掩膜编辑 + 掌握状态）  http://127.0.0.1:8765/
#   活页纸（浏览器直接打印）             http://127.0.0.1:8765/sheet          ← 预览，无页锚点
#   某一叠（页锚点稳定）                 http://127.0.0.1:8765/sheet?batch=<批次>
#   标记重做（三个入口共用一个界面）      http://127.0.0.1:8765/mark?anchor=3F2A-P1
#                                        /mark?batch=<批次>   /mark?pid=<题号>
```

`python3 proto/server.py --selftest` 不启动服务，只跑界面不变量自检（题目块有没有 id、块内该有的元素在不在、页锚点指不回来时会不会明确失败）。

`server.py` 自己也会加载 `.env.local` / `~/.env.local`——因为写入服务本身就会调模型（「重新定位手写」按钮、`--repair`）。少这一行，那个按钮就是死的。

**闭环是这么转的**：清单页勾选 → 「生成活页纸」**落一个打印批次**并打开 → 打印 → 在纸上重做 → 打开 `/mark?anchor=<页脚的码>` → 一屏里逐题标「对 / 错 / 看不清」（判错顺手记错因，错因属于那一次重做）→ 回写 `attempts` 与掌握状态。

**为什么页锚点必须指向批次**：排版会随复习进度变化（毕业、脱离冷却、新录入都会改变分页），若锚点按"当前排版"现算，上周打印的那张纸就会指向另一批题。存下来的批次让纸上的码永远指得回同一批题。

**判定回写没有模型**：纸上重做不拍照、不自动判（CONTEXT「判定」），所以这一条通道一律由人给出结论。自动判定属于屏幕重做，还没做。

掌握与冷却的规则全部在 `slice.apply_attempt` 里，验收不联网、不问模型：

```bash
python3 proto/test_mastery.py     # 假时钟钉住 12 条：当天判对不计入、判错永远清零、看不清两不相干…
```

**落盘数据体检**（只读、可重跑、什么都不改）：

```bash
python3 proto/server.py --audit                   # 卡片自检 + 资产 + 索引 + 批次 + 掌握状态
python3 proto/server.py --rebuild-index           # 只重建派生索引
python3 proto/server.py --repair <pid>            # 对着整页原图重抽，只修可疑字段（预览）
python3 proto/server.py --repair <pid> --apply    # 真写盘：退回未审核，痕迹进 provenance.repairs[]
```

它为什么存在：**验收是对 `runs/` 里那次调用打的分，而题卡能被后来的路径改坏。** 真发生过一次——界面把第一题的文字写进了第二题，那张解答题带着标准答案 `A` 被标成"已审核"，而验收日志（读的是 `runs/`）说这道题的转录完整、还带着 `√42 ≈ 6.48`。所以检查必须跑在**落盘的数据**上，而且必须随时能重跑。

体检里有两条不依赖看图的抓法：字段之间自相矛盾（解答题却带着选项答案），以及**两张卡的题干逐字相同**——两类不同的题不可能有同一段题干（见 `CONTEXT.md`「串题」）。

`--repair` 只修三个「可能来自另一道题」的字段（题干、标准答案、原答），**不整卡重跑**：卡上其余部分（掩膜、版面格、重做历史、考点错因）是人动过的，整卡重跑等于以"修数据"为名的第二次破坏。改完**退回未审核**，因为被改的正是审核覆盖的字段。

它对应三处旧承诺：**ADR 0001**（所有写入只经过它，并从题目文件派生 `data/index.json`）、
`CONTEXT.md` 的**审核**（擦除结果、原答与订正、标准答案与正解、考点错因、标签提案都在这一个界面里过）、
以及**重做纸 / 版面格 / 页锚点**。

界面上每道题左图是「原图 + 掩膜」（红＝将擦掉，黄＝手写压在印刷体上），右图是擦除结果。
**按住鼠标拖一个框就能加/减掩膜**——擦除是半自动的（[ADR 0005](../docs/adr/0005-redo-sheet-prints-cleaned-photo.md)）：机器给初值，你定稿。
掩膜规格（`boxes_norm` ∪ `manual.add` − `manual.drop`，一律归一化坐标）存进题卡，所以**擦除可重放**：
改掩膜只重算，不重新问模型；换分辨率也不会漂。

每道题的标题栏显示**客观闸门**读数，它们是判据（模型探针的判词只当线索）：

| 读数 | 应 | 它在防什么 |
|---|---|---|
| 新出现深色 | 0 | 填补出黑斑（真发生过：边缘补零的 bug） |
| 残留彩笔 | 0 | 没擦净 |
| 疑咬印刷体 | 0 | 被擦掉的墨迹左右两侧都还有墨迹 → 像在印刷笔画上啃了个洞 |

活页纸按**版面格**排版：一页四格，题占 1/2/4 格（AI 按题型给初值、你在界面上改），页脚印**页锚点**（形如 `P1-9D49`）与该页题目名单。默认只印**已审核**且未毕业、未在冷却期的题；界面上有「包含未审核」「显示冷却中的题」两个开关，供原型阶段试打。

## 产出落在哪

```
data/pages/     整页原图留底（永不裁剪母本，切分错了可以重切）
data/assets/    题面裁剪图 + <id>-clean.png（擦除手写后，重做纸印它）+ <id>-cleanmask.png（掩膜可视化）
data/problems/  一题一个 JSON —— 这就是「一题一文件」（ADR 0001）
data/batches/   打印批次：一叠重做纸的构成快照，页锚点指向它（排版变了，旧纸仍指得回来）
data/index.json 派生索引：由写入服务从题目文件重建，不是数据来源（ADR 0001）
data/vocab/     受控词表种子（错因清单 + 考点大纲）
runs/           每次调用的请求与响应留档（含用量），用于回看提示词效果
```

## 数据形状 ↔ 领域术语

题卡 JSON 同时是领域模型的第一版草稿，字段与 `CONTEXT.md` 的对应关系：

| 字段 | 术语 |
|---|---|
| `source.page_image` + `source.bbox_norm` + `problem.image` | 原题（整页留底 + 裁剪图） |
| `problem.clean_image` + `problem.clean.boxes_norm/manual` | 擦除手写后的题面图 + **手写掩膜**规格（自动框 ∪ 人工补 − 人工减，可重放） |
| `problem.clean.health` | 客观闸门读数（新出现深色 / 残留彩笔 / 疑咬印刷体） |
| `original_solution.original_answer` | **原答**（学生自己原本给的答案，不含订正；看不清就 null） |
| `original_solution.transcript` | 原解（手写转录，可为空） |
| `original_solution.correction_transcript` | **订正**（后来补上的正答/批注，常是红笔；不是原答，也不是 AI 的正解） |
| `correct_solution.text` | 正解（AI 现解，来源记为 `ai`） |
| `standard_answer.value` | 标准答案（自动判定的唯一基准） |
| `problem.type` | 题型（`choice` / `fillin` / `solution` ↔ 术语里的 选择 / 填空 / 解答） |
| `problem.transcript` | 题面转录（**只用于检索与打标，不印**；重做纸印擦除后的照片） |
| `problem.options` | 选择题的选项结构（受控词表与转录分离） |
| `print.cells` / `print.cells_source` | **版面格**（1/2/4 格；`manual` 表示你在界面上改过） |
| `topics` / `error_causes` | 考点 / 错因（只能取词表里的值） |
| `new_tag_proposals` | 越出受控词表的新标签提案，待审核 |
| `review.status` | 未审核 / 已审核 |
| `attempts[]` | 每次重做：`at` / `verdict`（对·错·看不清）/ `channel`（纸上·屏幕）/ `source`（人工确认·自动判定）/ `error_causes`（**属于那一次重做**）/ `note` |
| `mastery.state` / `streak` / `last_attempt_at` | 在池 / 毕业 · 连续正确计数 · 冷却起算点（为空则用 `created_at`） |
| `data/batches/<id>.json` | **打印批次**：一叠重做纸的构成快照（每页的题与格数、页锚点指向它） |

两处约定：

- **枚举用英文、界面显示中文**（`choice/fillin/solution`、`in_pool/graduated`、`correct/wrong/unreadable`），显示映射集中在 `TYPE_CN` / `MASTERY_CN` 与 `S.VERDICT_CN`。题卡里的字段值是机器读的，中文只出现在界面上。
- **掌握与冷却的规则只有一份实现**：`slice.apply_attempt`。界面、CLI 都调它，验收在 `proto/test_mastery.py`。规则里最容易被写错的两处——冷却要拿"上次重做**或录入时间**"起算、且判断必须发生在更新 `last_attempt_at` 之前——都在那份验收里钉着。

## 已知限制（原型故意不解决的）

- 一次只处理一道题。一页多题时要靠 `--note` 指认，批量切分还没做。
- 考点大纲是六条假数据的种子，不是真大纲。
- 单用户、数据在本机——这是设计本身（ADR 0003），不是待办。
- 判定只看最终答案，不看过程。
- 活页纸是**浏览器打印**的 HTML，不是 PDF 生成；打孔与双面还没考虑。复习纸还没做。
- 页锚点目前印成一段短码与一个本机网址，**不是二维码**；扫一下打开这件事本身还没做（它不承担回写，所以只是快捷入口）。
- **屏幕重做还没做**：所以自动判定（把敲进去的答案与标准答案做等价比对）目前无处可用，判定只有人工确认这条通道。
- 界面没有鉴权（单用户本机，ADR 0003），也不该暴露到公网。
- 验收只实现了探针与判定那一半；抽取角色的「直接可用率」要你自己数。
- 擦除是半自动的：机器给初值，掩膜要你在界面上过一遍（ADR 0005）。全自动需要自训分割模型，见该 ADR。
