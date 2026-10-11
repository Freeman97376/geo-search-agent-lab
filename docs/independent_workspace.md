# 独立拆分与搜索工作台

## 更新：本批研究概览

结果区顶部新增自动概览，运行时随记录更新，历史批次也可直接查看。拆分概览汇总原始需求、计划/已记录/有效轮数、输出记录数和按需求去重的 query 数；搜索概览汇总来源需求、query 次数、返回条目、分页进度、前五条页面、指定网站/子域名命中位置、具体失败原因及 Sponsored 报告情况。

“两页都返回但只有 19 条”与“请求超时未取得响应”分开标记；没开始、运行中、失败无响应、成功空结果分别处理。计数表示结果记录和本地请求账本，不是独立网站数或实际扣费。广告仅根据成功响应中的 `ads` 区块统计；没有报告不等于浏览器一定无广告。完整 JSON 导出包含新增 `overview` 字段。

统计由 `geo_search_lab/workspace/overview.py` 只读生成，不发送模型/搜索请求，不更改历史快照。它不自动判断内容质量或需求语义覆盖，也不生成 AI 回答。验证见 `research_overview_validation.json`。

## 更新：域名分类与 Query 次数汇总

- 结果按指定域名和子域名分别显示最佳观测排名、命中 URL 与检出 query 数。例如目标 `example.com`：该 host 为指定域名，`www.example.com` 与 `shop.example.com` 为子域名；`goexample.com` 和 `example.com.evil.com` 均不匹配。新增保存的目标不再去掉 www；历史已保存的目标文字不迁移。
- 指定页面继续沿用原有 URL 规范化规则。API 保留 `best_rank` 作为含子域名的兼容聚合字段，新增 `exact_domain_best_rank`、`subdomain_best_rank`、`exact_page_best_rank` 和 `matches` 明细；界面和 CSV 使用分类列。两类检出 query 可能重叠，不应相加当成独立 query 数。
- 拆分结果新增 `query_frequencies`，按原始需求 ID 和完全相同的 query 文本汇总成功轮次。`occurrence_count` 包含轮内重复，`trial_count` 每轮最多计一次；同时提供有效轮数、按温度计数与逐轮来源。不合并大小写、空格或同义措辞，不把失败输出计入成功频次。
- 新表格及 `GET /api/v1/workspace/batches/{id}/export?format=query_csv` 导出汇总；原始 trial JSON 和旧 CSV 仍保留。历史数据读取时派生这些字段，不改原始快照、不重新请求模型或搜索。
- 下文的域名匹配描述以本节为准；具体页面 URL 的 www 规范化规则没有改变。验证见 `matching_frequency_validation.json`。

2026-10-09。入口：http://127.0.0.1:8766/ 。本次替换主入口的使用流程，旧版离线评审保留在 `/research`，旧库和上游源码不变。

## 使用

1. 保存项目名称和主要网站域名。竞争网站和指定页面可稍后补充。
2. **A 查询拆分**：填写真实需求，多条之间空一行。默认温度 0、0.4 各重复 5 次；可将重复次数设为 1 做连通性检查。每轮结果立即可看，无人工评审前置门槛。发生模型连接/超时等故障时停止批次，已完成结果保留。
3. **B 搜索排名**：配置 SerpAPI 密钥，手动填写 query，或显式导入一个已结束且含成功输出的拆分批次。手动单行 query 默认也是其原始需求；需要多个 query 归属一个需求时，从表格复制两列“原始需求、query”（Tab 分隔），同一原始需求使用相同文字。
4. 设定地区、语言、Google 域名、定位和设备。默认 US / English / google.com / desktop。点击预览：同一批内只对完全相同的 query 合并搜索，保留全部来源及出现频率。**前 20 条按 start=0 和 start=10 两次请求估算**。默认上限 10，即最多 5 条独立 query；超过时不会启动。
5. 点击“启动搜索”才发出付费请求。可以停止后续请求；已经发出的请求先保存。失败计入本地请求账本，不自动重试、不更换 provider、不自动续跑。
6. 在结果区选择批次，查看需求、网站、query 三个视角，或下载 CSV、完整 JSON。补充竞争网站/页面并保存后，同一快照立即重新匹配，不再请求搜索。

密钥只保存在当前服务内存，界面和导出不回显；重启需要重新输入。也支持启动进程显式设置 `SERPAPI_API_KEY`。不会搜索、复制相邻项目中的凭据；配置密钥不会探测余额或调用搜索。

## 启动与本地服务

在实验室根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/lab.py --store data/workbench/offline-review serve --workspace --port 8766
```

需要本机 Ollama 在 `127.0.0.1:11434` 提供锁定的 `qwen3:8b`。工作台仅监听 loopback；单实例文件锁加串行工作队列。`--workspace` 与 `--offline` 互斥；工作台模式禁止同时调度旧版 planner，避免上游模块临时补丁并发。搜索不需要模型服务。

## 原生规划保留与适配边界

- 保留固定 GPT Researcher checkout 的 `plan_research_outline`、原生 prompt 及 `json_repair`/normalizer。启动时检查完整 SHA 与 clean checkout；不改第三方源码。
- 新命名适配器 `native-planner-direct-openai-v1` 直接调用本机 OpenAI-compatible endpoint，替换 LangChain completion 传输层。原因是原链路会把 `max_tokens` 序列化为 `max_completion_tokens`，与先前本地兼容性观察不一致。
- 显式发送 `max_tokens=2048`、`top_p=1`、`temperature=0/0.4`、`reasoning_effort=none`、`stream=false`。每轮只允许一次模型 HTTP 请求，阻止上游失败 fallback 把一次试验变成多次抽样。改变的传输层、上游 fallback 边界及 profile 单独命名，不覆盖旧 baseline。
- 记录 model digest、Ollama 版本、完整消息、适配器/上游文件摘要、精确 HTTP request/response bytes、返回 token 计数和 finish reason。top-k 与 seed 使用服务端默认，明确记作未覆盖；配置声明的 context 长度不是测得的运行时长度。
- 同一批固定 prompt 日期，重复轮次交替温度先后顺序。当前独立流程无初搜上下文，明确标记 `empty_ablation`；不额外调用搜索。早期基线的固定初搜快照与严格 replay 仍在旧模块，新适配器没有声称与旧传输逐字节一致。
- 保存 native normalized output，即使 JSON 修复/归一化发生也可检查；非字符串数组、原生 fallback 或截断输出标为 `invalid_output`，不自动作为成功 query 导入搜索。此资格检查单独记录，不改写原始输出。

## 排名与统计口径

- 自然结果按分页返回顺序保留。`observed_rank=page_start+index`；原生 `position` 单独保存在 `provider_position`。不压缩短页的空位，不做 GEO 排序。前两页不足 20 条标为不完整，即使批次调度已完成也不会记为完整 Top 20。
- 原始响应保留广告、购物等区块，展示和统计只使用 `organic_results`。原始 bytes 如含 API key 会先脱敏，分别记录原始 hash、落盘 hash 和脱敏标记；不声称脱敏 bytes 与原始 bytes 相同。
- 请求地区等设置按批固定，保存请求及 provider 回显；已回显字段不一致会记录失败并停止，不能混入请求设置对应的观测。未回显时明确记录不可校验。
- 域名采用完整域名或点边界子域名匹配，不会把 `notexample.com` 或 `example.com.evil.com` 匹配到 `example.com`。具体页面按现有 URL 规范化：保留协议、路径、业务 query 参数；去 fragment、utm 参数和 www。原始 URL 始终保留。
- **原始需求 × 网站是分析单位**，同一需求的 query、重复轮次和温度不当作独立需求。搜索去重只节约请求，所有来源仍存在，可在 JSON 按配置/重复轮次重新聚合；共享快照的来源不是多次独立 SERP 采样。
- `website_detection_rate_observed` 的分母为实际观测到自然条目的独立 query；另列总 query 和完整 Top20 query。未采集与失败不补零；未命中只表示实际采集范围内未发现，排名为 null。
- **语义覆盖率当前未评定**。页面提供原始需求和拆分输出供逐条检查，文字集合 Jaccard、输出版本数、每条 query 出现频次仅是描述性差异。没有把关键词匹配当作语义评估，没有置信度、平台相似度或优化因果结论。
- 后续 AI 引擎批量暴露测量可以按 `project_id/demand_id/query_id/batch_id/cohort_id` 关联；本阶段没有回答生成、引用选择、正文爬取或自动跨批汇总。表格中的排名不能解释为什么某网站排名更高。

## 大系统读取接口

所有写请求需要 `X-Lab-Request: 1`；浏览器限同源。接口前缀 `/api/v1/workspace`。

| 接口 | 用途 |
| --- | --- |
| GET `/status` | 功能与密钥配置状态，不返回密钥 |
| POST `/projects`、PUT `/projects/{id}` | 保存目标网站配置 |
| GET `/projects`、GET `/batches` | 索引与批次进度 |
| POST `/planning` | 单条/批量需求，repeats，temperatures |
| POST `/search/preview` | 精确去重、全来源映射、分页请求量估算；不发请求 |
| POST `/search` | 显式创建搜索任务，重新验证预算及输入 |
| POST `/batches/{id}/cancel` | 保存中间结果，停止后续派发 |
| GET `/batches/{id}` | 独立完整结果、三视角、请求账本 |
| GET `/batches/{id}/export?format=json` | 完整结果及 provenance |
| GET `/batches/{id}/export?format=csv` | 适合表格查看的平面摘要，带公式注入防护 |

数据位于当前 `--store` 下 `workspace/workspace.sqlite3`；原始证据在 `workspace/artifacts/{batch_id}/`。旧 `research/` 保留。

请求账本在网络派发前事务性预留计数。进程在预留与网络调用之间崩溃时可能保守多计；重启后记为未知，不自动退回额度/续跑。这是本地请求上限，不等于 SerpAPI 实际账单或 account credit 校验。两页来自不同请求时点，不是原子搜索快照。当前 API 创建任务不是跨重试幂等端点，集成端 POST 超时应先查批次，不要盲目重发。

## 验证与边界

真实本地 smoke 批次 `4d565d37523f4ae7935c9cf96240a6ef`：1 个工业泵采购需求，温度 0、0.4 各 1 次，均返回 3 条 query，finish_reason=stop，各返回 40 completion tokens。这验证连通性、日志和传输字段，不证明参数所有边界的 runtime 执行，也不能估计温度效应。本次 **0 次真实 SerpAPI 请求**；分页、失败、配额、脱敏、UI 都用独立测试夹具验证，真实 provider 端到端仍待用户显式启动。

接口依据（2026-10-09 查阅）：[SerpAPI 分页](https://serpapi.com/pagination)、[Google Search FAQ](https://serpapi.com/faq/google-search)、[Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)。准确执行证据及覆盖文件摘要见 `workspace_validation.json`。
