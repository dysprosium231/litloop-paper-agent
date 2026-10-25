# app 与 Agent 共用的数据文件

桌面 app 和 Agent（通过 MCP）是两个进程，只通过 `~/.litloop/screening/` 下的文件交换数据。读写规则由 [`src/litloop/store.py`](../src/litloop/store.py) 实现，app 附带的后台用同一份代码。

## 规则

- **写**：先拿该文件的锁（同名加 `.lock`，`filelock` 库，Windows / macOS / Linux 跨进程都有效），在锁内“读 → 改 → 写”，用 `store.update(path)`；写入先写临时文件再一次替换，读的一方永远看不到半个文件。
- **读**：不用拿锁，用 `store.read_json(path)`。
- **版本**：每个文件带 `schema`（当前为 1）。读到比自己新的版本时抛 `SchemaTooNew`，app 应提示“请更新 LitLoop”，不要硬读。改字段含义或删字段时加版本号；只加字段不用。
- **app 不读候选池**：`<session>.pool.json` 一次深度检索可达上百 MB，只给 Agent 用。app 需要的都在下面三个小文件里。
- **刷新**：进度文件每 0.5 秒内最多写一次，app 每 1–2 秒读一次即可；另外两个文件每出一批或每存一次评分才变，可按修改时间判断是否重读。

## 文件

一个会话（`<session>` 形如 `20261016-213958-含稀土-…`）对应：

| 文件 | 谁写 | 内容 |
|---|---|---|
| `<session>.json` | Agent、app | 筛选记录：问题、检索历史、每篇的评分与理由、用户的决定 |
| `<session>.md` | Agent、app | 筛选记录的表格版，给人读，app 不用 |
| `<session>.summary.json` | Agent | 深度检索概况和当前这一批，只有深度检索有 |
| `<session>.progress.json` | Agent | 正在运行的这一轮在做什么，只有深度检索有 |
| `<session>.pool.json` | Agent | 候选池，app 不读 |

会话列表：`screening.list_sessions()`，只列筛选记录。

### 筛选记录 `<session>.json`

```json
{
  "schema": 1,
  "id": "20261016-213958-…",
  "created": "2026-10-16 21:39:58",
  "updated": "2026-10-16 22:06:47",
  "question": "含稀土（镧系）元素、以非晶/无定形为主的纳米材料作为药物递送载体：…",
  "mode": "deep",
  "criteria": [],
  "searches": [{"query": "lanthanide coordination polymer … | …", "time": "…", "params": {"core_terms": […], "seeds": […]}, "results": 0}],
  "items": [
    {
      "n": 1,
      "doi": "10.1039/c4ra09902h",
      "title": "Multifunctional biodegradable terbium-doped calcium phosphate nanoparticles: …",
      "year": 2014, "journal": "RSC Advances", "journal_rank": "中科院3区 · JCR Q2 IF 6.1 · 新锐2区",
      "authors": ["Han-Bing Shang", "Feng Chen", "Jin Liang Wu"],
      "cited_by_count": 36,
      "abstract": "前 1200 字",
      "abstract_missing": false, "retracted": null, "concern": null,
      "relevance": "高",
      "reason": "Tb掺杂无定形CaP载药成像",
      "user_relevance": "中",
      "decision": "selected"
    }
  ]
}
```

- `relevance`、`reason`：Agent 的评分和理由，取值 `高`、`中`、`低`、`不相关`。
- `abstract_zh`：中文摘要（可选）。翻译服务为 DeepSeek / DeepL 时由 **app 写**（用户打开文献时翻译一次，见 `translate.py`）；为 Agent 时由 Agent 在评分时写，或用 `litloop_screening(action="translate")` 补。重新评分不会抹掉它。
- 只有用户评过、Agent 还没评的文献（app 里「继续检索」出的批次）：**app 写**，`reason` 为空，`relevance` 和 `user_relevance` 都是用户的评分；Agent 之后评分时填上自己的 `relevance` 和 `reason`，`user_relevance` 保留。`litloop_deep_search(action="status")` 的 `unrated` 列出最近一批里 Agent 还没评的。
- `user_relevance`：**app 写**。用户改过的评分，覆盖 Agent 的（排序、停止判断都用它）；Agent 再评同一篇时不会抹掉它。没改过就没有这个字段。
- `decision`：**app 写**（或 Agent 按用户的话写）。`selected`（选中下载）、`rejected`（排除）或 `null`。
- `n`：条目按相关度排序后的编号，每次保存会重排；app 定位条目用 `doi`，没有 DOI 的用标题。
- 一篇算哪一档：`user_relevance`，没有就用 `relevance`（`screening._rating(item)`）。

app 改评分或决定，在锁内改对应条目再写回：

```python
with store.update(screening_dir() / f"{session}.json", indent=2) as data:
    item = next(it for it in data["items"] if it.get("doi") == doi)
    item["user_relevance"] = "高"
```

### 深度检索概况 `<session>.summary.json`

每出一批写一次。

| 字段 | 说明 |
|---|---|
| `session`、`question`、`mode`、`updated` | 会话、问题、`deep`、写入时间 |
| `pool` | 候选池篇数 |
| `screened`、`remaining` | 已评篇数；还没推给评分的篇数 |
| `ranked_by` | `formula`（开头几批）或 `model`（之后） |
| `seeds` | 当前种子篇数（原始种子加评为高的） |
| `batches` | 每批：`batch` 序号、`size`、`rated`，以及 `高`、`中`、`低`、`不相关` 各几篇；每批高相关比例由此算 |
| `rounds` | 每轮：`time`、`event`（`start` / `next`）、`new_seeds`、`pool_added`、`expand_seconds`、`rank_seconds`、`openalex_credits`、`seconds`；`next` 轮另有 `abstracts_asked`、`abstracts_found`、`abstracts_wait_seconds`（补摘要与扩展同时进行，这是扩展结束后还要再等的秒数；2026-10-20 起） |
| `stop_suggested`、`stop_reason` | 停止建议 |
| `remaining_estimate` | 按每批“高”的比例下降趋势粗略外推：`next_batch`（下一批约几篇高）、`remaining`（继续读下去约还有几篇高；比例不降时没有此项）、`batches`（用了几批）、`decay`（每批比例的衰减系数）；不足三批评分时为 `null` |
| `coverage` | 必找文章：`doi`、`status`（`screened` 已评，带 `relevance`；`seed` 已是种子；`in_pool` 在候选池，带当前排名 `rank`；`not_found` 没找到） |
| `api_warnings` | 额度、限流提醒（英文短句） |
| `core_terms`、`queries` | 本次的核心词、检索词 |
| `glossary` | 本次按术语表做的展开（2026-10-25 起）：每项 `term`（检索式里的写法）、`with`（另搜的写法）；检索词的展开带 `id`（术语编号）和 `query`，只有缩写本身、没有展开的另带 `alone: true`；核心词的展开带 `core_term: true`。没有展开时为空列表 |
| `current_batch` | 当前这一批，和 Agent 看到的一样：`n`、`doi`、`title`、`year`、`journal`、`journal_rank`、`authors`（前 3 位）、`cited_by_count`、`abstract`（前 1200 字）、`abstract_missing`、`retracted`、`concern`、`why`（`shared_refs_with_seeds`、`queries_matched`、`found_via`） |

### 进度 `<session>.progress.json`

一轮（开局或下一批）开始时创建，结束时 `state` 变为 `idle`（出错为 `failed`）。

```json
{
  "schema": 1,
  "session": "…",
  "event": "next",
  "state": "running",
  "started": 1792161500.2,
  "updated": 1792161561.7,
  "phase": {"name": "abstracts", "done": 62, "total": 150, "started": 1792161540.0},
  "phases": [{"name": "expand", "seconds": 76.4}]
}
```

- 时间都是 Unix 秒。
- `phase.name`：`keywords`（开局的关键词检索）→ `expand`（扩展候选池，`done` / `total` 为已查 / 待查的参考文献数）→ `abstracts`（给排名靠前的补摘要，`done` 为已有结论的篇数）→ `rank`（训练排序模型）→ `batch`（给这一批补摘要、期刊分区）。并非每轮都有每个阶段：没有新种子就没有 `expand`，没缺摘要就没有 `abstracts`。
- `phases`：已经结束的阶段及用时，可用来估计剩余时间。
- `state` 为 `idle` 时，`seconds` 是这一轮总用时；`phase` 为 `null`。
- 读不到或读到坏文件时当作“没有在运行”。

### 下载任务 `<litloop home>/downloads/<job>.json`

app 发起的下载，每次一个文件，由 app 后台的 `download_jobs.py` 写（同样经 `store` 加锁、整份替换）；Agent 走 MCP 的批量下载不写这里。

```json
{
  "schema": 1,
  "job": "20261017-101500",
  "title": "含稀土的非晶纳米药物载体",
  "session": "…",
  "state": "running",
  "created": "2026-10-17 10:15:00",
  "finished": null,
  "output_dir": "C:\Users\…\papers",
  "error": null,
  "items": [
    {"doi": "10.3390/nano8110961", "title": "…", "year": 2018, "journal": "Nanomaterials",
     "status": "done", "source": "mdpi", "file": "C:\…\xxx.pdf", "error": null,
     "zotero": {"status": "added", "key": "ABCD1234", "error": null}}
  ],
  "zotero": {"state": "done", "collection": "LitLoop/含稀土的非晶纳米药物载体", "key": "QWER1234",
             "started": "…", "finished": "…", "error": null}
}
```

- `state`：`running` → `done` / `queued` / `cancelled` / `failed`（引擎本身出错，原因在 `error`）。`queued`：长清单还有文献在出版商队列里等用户开始（2026-10-23 起）。文件里仍是 `running`、但当前后台并没有在跑它（app 中途关闭），读出时当作 `interrupted`。
- `items[].note`：给用户看的说明（如 T&F 章节下载的是整本书、本章所在页码），没有时为空。
- `items[].status`：`waiting` → `done` / `failed` / `queued`。超过 `batch_small_max`（30）篇的清单只跑开放获取和 Sci-Hub 两轮，没下到的标为 `queued`，按 `items[].publisher`（出版商，取自 OpenAlex，去掉 BV、Ltd 这类公司后缀；不知道时为 `null`，归入「其他出版商」）分成队列；用户开始某个队列（`POST /api/downloads/<job>/queue`），它的文献变回 `waiting`，逐篇走出版商官网和学校通道；中途取消的回到 `queued`。`GET /api/downloads/<job>` 另给出 `queues`（`[{publisher, count}]`，篇数多的在前），不存进文件。每篇一出结果就写入 `source`、`file`、`error`（逐篇下载的短清单；批量车道在整批结束时填入）。
- `items[].error`：失败的原始原因。几条路都失败时取最说明问题的一条（没有订阅 > 学校登录未完成 > 人机验证 > 打不开 > 超时 > 付费墙 > 没找到），不再是笼统的“所有来源都失败”；app 显示时由后台换成中文 `error_text`。
- `items[].attempts`：这篇走过的路，`[{source, route, seconds, ok, why}]`。`route` 为 `free`（免费来源和出版商官网）或 `school`（学校通道）；`why` 是失败原因的中文说法。不到 1 秒就答复“没有”的接口查询不列入；没有时为空。（2026-10-18 起）
- 同一时间只运行一个任务：引擎的进度报告和取消开关是整个进程共用的。运行中的实时信息（正在处理哪篇、需要用户处理的验证码或登录提示）不存进这个文件，app 通过 `/api/downloads` 读取 `progress_reporter` 的报告。Agent（MCP）或命令行在别的进程里下载时，`/api/downloads` 的 `external` 给出它的报告（进度文件 `progress/current.json`，`status` 为 running 且写它的进程 `pid` 还活着），下载页只显示、不能跳过或取消。
- `zotero_target`：附件检查发起的下载会带上它，推送时默认推回这个分类；其他任务为 `null`，推送默认用 `<zotero_collection_root>/<title>`。
- `zotero`（推送过才有）：`state` 为 `running` → `done` / `failed`，app 中途关闭读出为 `interrupted`；`collection` 是分类路径，`key` 是它在 Zotero 里的键。`items[].zotero.status`：`pushing` → `added`（新建）/ `reused`（库里已有）/ `no_pdf`（条目在了但 PDF 没附上，再推送一次会补附）/ `failed`。推送和下载可以同时进行，同一时间只推送一个任务。
- “待下载”不单独存文件：取各筛选记录里 `decision` 为 `selected`、且还没有被任何任务下载成功的文章。

### app 发起的检索 `<litloop home>/agent_runs/<run>.json`

app 的「新建检索」把表单拼成一段话（`agent_runner.build_prompt`），从标准输入发给设置里的 Agent 命令（默认 `claude -p`，只允许调用 LitLoop 的工具和技能）。Agent 照常检索、评分，并用 `litloop_screening` / `litloop_deep_search` 保存筛选会话。每次一个文件，由 app 后台的 `agent_runner.py` 写（经 `store` 整份替换）；Agent 的原始输出另存为同名 `.log`。

```json
{
  "run": "20261017-194500",
  "state": "running",
  "started": "2026-10-17T19:45:00",
  "finished": null,
  "question": "SiC 颗粒增强铝基复合材料的界面反应",
  "mode": "deep",
  "form": {"question": "…", "criteria": "…", "mode": "deep", "batches": 3, "year_from": 2015, "…": "…"},
  "prompt": "用 litloop 技能做深度检索：…",
  "doing": "深度检索：取下一批",
  "events": [{"t": "19:45:12", "text": "深度检索：开始"}],
  "session": "20261017-194530-SiC-…",
  "agent_session": "…",
  "result": "已保存 …",
  "error": null
}
```

- `state`：`running` → `done` / `failed` / `cancelled`；文件里是 `running`、但后台没有在跑它（app 中途关闭），读出时当作 `interrupted`。同一时间只运行一个。
- `session`：Agent 保存的筛选会话（从工具返回里取到的最后一个），app 据此打开结果；`agent_session` 是 Agent 自己的对话编号，可用 `claude --resume` 接着聊。
- `events` 只保留最近 200 条，`doing` 是当前一步，结束后为 `null`。

### 术语表 `<litloop home>/glossary.json`

app 和 Agent 共用，由 `glossary.py` 经 `store` 读写（2026-10-25 起）。

```json
{"schema": 1,
 "entries": [{"id": "3f9a1c2e", "terms": ["PDC", "polymer-derived ceramic"], "status": "active",
              "source": "user", "note": "", "added": "2026-10-25"}],
 "dismissed": [["pdc", "polycrystalline diamond compact"]],
 "disabled": ["element:La", "spelling:aluminum"]}
```

- `terms`：同一个意思的几种写法，至少两种；第一种通常是缩写。
- `status`：`active` 检索时使用；`suggested` 等用户确认（`source` 为 `agent` 或 `auto` 的条目都从这里开始）。
- `source`：`user` 用户自己加的；`agent` Agent 建议的；`auto` 从读过的摘要里自动发现的（另有 `papers`：在几篇摘要里这样写）。
- `dismissed`：用户删掉的自动建议（缩写和全称都已归一化），不再建议。
- `disabled`：用户停用的内置条目（2026-10-25 起）：`element:<元素符号>`，或 `spelling:<美式拼写>`（这个词不再按英美拼写展开、统一）。
- 概况文件 `glossary` 里来自内置元素表的展开带 `builtin: "element"`。
