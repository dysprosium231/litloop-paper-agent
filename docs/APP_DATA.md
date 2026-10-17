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
| `rounds` | 每轮：`time`、`event`（`start` / `next`）、`new_seeds`、`pool_added`、`expand_seconds`、`rank_seconds`、`openalex_credits`、`seconds` |
| `stop_suggested`、`stop_reason` | 停止建议 |
| `coverage` | 必找文章：`doi`、`status`（`screened` 已评，带 `relevance`；`seed` 已是种子；`in_pool` 在候选池，带当前排名 `rank`；`not_found` 没找到） |
| `api_warnings` | 额度、限流提醒（英文短句） |
| `core_terms`、`queries` | 本次的核心词、检索词 |
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
     "status": "done", "source": "mdpi", "file": "C:\…\xxx.pdf", "error": null}
  ]
}
```

- `state`：`running` → `done` / `cancelled` / `failed`（引擎本身出错，原因在 `error`）。文件里仍是 `running`、但当前后台并没有在跑它（app 中途关闭），读出时当作 `interrupted`。
- `items[].status`：`waiting` → `done` / `failed`。引擎每完成一篇就更新一次状态；`source`、`file`、`error` 在整批结束时填入。
- 同一时间只运行一个任务：引擎的进度报告和取消开关是整个进程共用的。运行中的实时信息（正在处理哪篇、需要用户处理的验证码或登录提示）不存进这个文件，app 通过 `/api/downloads` 读取 `progress_reporter` 的报告。
- “待下载”不单独存文件：取各筛选记录里 `decision` 为 `selected`、且还没有被任何任务下载成功的文章。
