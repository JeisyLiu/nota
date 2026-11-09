# Part A → Part B 对照

Part A 的 18 道设计题我写到 T5 为止（`answer.txt`）。这里只记录**已经写下的判断，在 Part B 里落成了什么**——没写的题目不在这里编造，避免自说自话。

## T1（架构分层：3 云节点 + 2 客户端）

Part A 我画的是「LLM-API 中转站 / Rust+DDD 后端 / VueJS 前端」三个云节点加 Flutter App + 硬件设备两个客户端。

Part B 的落地取舍：

| Part A | Part B | 为什么 |
|---|---|---|
| Rust + gRPC 后端 | Python + FastAPI | 单机 MVP 用 Rust 编译链路会把验证时间耗在工具链上，而本题考的是 Agent 编排与工程严谨性。DDD 分层（domain/application/infrastructure/interfaces）完整保留，见 `docs/architecture.md` |
| 独立 LLM-API 中转站 | 直接对接 provider | 中转站是运维组件，不是产品能力；`.env.example` 里留了 OpenAI 兼容槽位（DeepSeek/月之暗面/智谱/通义都走这个协议），需要时再加一层即可 |
| VueJS 前端 | 无构建的单页 | 见 ADR-008，同一套 HTML/CSS/JS 也能被 FastAPI 直接托管 |
| Flutter App | Flutter App（保留） | 见 `app/nota_app/` |
| 硬件设备（唯一机器码 + BLE） | 硬件设备（唯一机器码，无 BLE） | 见 `docs/hardware.md`。BLE 配网在 MVP 里用编译期宏替代，因为配对流程不是本题考点 |

## T3（Agent Harness：云端 + 端侧分工，权限与审批的矛盾）

Part A 里我写的核心担心是：Agent 要替用户办事，但有些事办错了收不回来；如果所有写操作都要确认，交互就没法用了。

Part B 的答案：**把风险判断从模型挪到工具的契约里**。工具声明 `risk(READ|WRITE|IRREVERSIBLE)`，只有 IRREVERSIBLE 走 `pending_actions` 挂起审批，其余直接执行。分界线由人画，不由模型每次现猜。见 ADR-006。

端侧分工也照做了：客户端（网页 / Flutter / 硬件）只做采集与展示，智能全在云端。

## T4（记忆：设备不存，App 存全文，云端做长期记忆）

Part A 我否掉了设备端存储，主张全文在 App、云端做向量化长期记忆。

Part B 的落地：
- 云端记忆：`memories` 表 + FTS5 trigram + 字符 n-gram 向量，混合 RRF 召回（ADR-005）。
- **没有用 LanceDB**：单机 MVP 引入向量库要额外进程和依赖，而 SQLite FTS5 在这个数据量下够用。`EmbeddingProvider` 留成协议，换真 embedding 或向量库不用动业务代码。
- 设备仍不存任何东西，只做采集。

## T5（模型变更评估：结果 / token / 时间 / 流程 / 输出 多维度打分）

这是 Part B 里我做得最实的一块，因为题面要求「自己造一套评估方法」。

`eval/run_eval.py` 用 15 条标注中文语料 + 5 条历史问答，量化四个维度：

| 维度 | 指标 | 当前值 |
|---|---|---|
| 结果正确性 | 任务抽取 F1（多建了算 FP，漏建算 FN） | 1.000 |
| 时间解析 | 绝对时间断言（11 个时间点，含「下周三」「下午两点半」） | 1.000 |
| 编造（幻觉） | 陈述句是否被凭空生成待办 | 0 例 |
| 引用可信 | 回答的 `[note:id]` 是否都能对上检索结果；空语料时是否拒答 | 4/4 + 1/1，0 编造 |

流程与成本维度在 `agent_runs` / `agent_steps` 里：每次运行记 provider、model、prompt 版本、tokens_in/out、latency_ms、轮次数。所以「换模型后变好还是变坏」是可以用同一份数据集 + 同一套指标回答的，`--provider openai` 直接切换对比。

这套评估不是一次性脚本：`tests/test_memory.py::test_eval_suite_is_green` 会在每次 `pytest` 时重跑，指标掉了测试就红。
