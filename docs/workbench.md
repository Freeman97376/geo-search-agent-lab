# 独立实验工作台与大系统结果接口

当前交付的是 **Query 规划模块的可用入口与结果管理层**：自定义问题、固定初搜、原生 Planner、本地 Qwen、记录与离线重放、单次结果查看、人工标注、批量描述性评价。完整搜索→网页抓取→回答→引用仍是后续模块。本轮没有新增付费搜索。

## 直接使用

默认地址：[http://127.0.0.1:8765](http://127.0.0.1:8765)。服务仅绑定本机，界面文件不依赖外部 CDN。

在 `geo_search_agent_lab` 内启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_lab.ps1 -StartModel
```

去掉 `-StartModel` 可以只查看和导出现有结果。需要看服务日志或手动结束时，可以前台启动 `.venv/Scripts/python.exe scripts/lab.py serve`；已运行的同一服务不能重复启动。旧服务和新服务不得同时操作同一个结果目录。

页面分为三个部分：

1. **运行记录**：按实验组/状态筛选，查看输入、子查询、原始格式、Tokens、配置、阶段日志与原始证据。每条记录有独立链接 `/#run=<run_id>`。
2. **新建实验**：可先选择公司与题目，再点击“载入问题”，也可自由输入。企业题库现有8家公司、每家6题，共48题，包含同义改写；载入不自动运行，问题和评价维度可继续修改。无品牌题与指定品牌题分别归组。“查看同组记录”可筛选同一公司同类题目。也可选择已捕获的初搜快照，但快照与原问题严格绑定，不能套用到新题。无初搜模式明确属于消融实验。
3. **综合评价**：勾选多条记录后汇总，下载 JSONL 明细或 JSON 评价报告。单条运行可按预先声明的信息维度和约束进行人工标注，保存时生成新的评价版本。

已导入的 12 份历史 Planner 结果属于 `commercial-pilot-v1` 实验组；接入验证运行单独标为 `workbench-smoke`。历史导入保留原始记录副本，反复执行 `scripts/lab.py import-pilot` 不会重复建立同一条实验。

界面采用信息列表与详情并排的桌面布局，小屏改为上下布局；明确区分加载、失败、空结果、未评价。设计检索匹配到 Data-Dense Dashboard 风格；营销落地页布局不适用，因此实际工作区布局采用通用内部工具模式。颜色沿用蓝色主色，字体使用系统本地字体，交互提供可见焦点和低动态模式。

## 三种结果获取方式

| 方式 | 适用场景 | 内容 |
| --- | --- | --- |
| 本地页面与独立链接 | 人工逐条审阅、标注与复核 | 输入、输出、配置、阶段、原始证据、评价 |
| JSON HTTP API | 其他系统触发任务、轮询、独立评价 | 版本化运行状态、结果、事件游标和评价记录 |
| JSON / JSONL 导出与 CLI | 批量分析、离线归档、任务编排 | 每次运行的结果、事件、评价历史及原始文件清单 |

所有方式共享同一个 `run_id` 和同一份最终结果。API 不从页面抓取数据；页面本身使用公开的 API。导出包含原始证据的名称、相对位置和 SHA-256，原始文件通过 artifact 接口分别下载，不把大体积响应全塞入结果摘要。

本轮不做没有权重依据的“综合总分”。`quality_score`/`platform_gap` 仍可为 `null`，后续系统必须保留它与零分的区别。

## 接口契约 v1

完整机器契约：`GET /openapi.json`。单次结果 JSON Schema：`GET /api/v1/schemas/result`。输入与结果 Schema 均来自 Pydantic；结果主体标识 `schema_version="1.0"`、`kind="planner"`。

离线契约也已保存：[OpenAPI v1](schemas/workbench-openapi.v1.json)、[Planner 结果 JSON Schema v1](schemas/planner-result.v1.json)，可交给后续系统生成客户端或校验导入数据。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/v1/health` | 服务状态、当前范围和是否允许付费搜索 |
| `GET /api/v1/catalog` | 已验证的模型配置、题集、可用初搜快照和功能范围 |
| `GET /api/v1/prompt-library` | 只读企业题组，返回公司、题目、品牌类别、同义改写关联及兼容现有接口的request；不创建运行 |
| `POST /api/v1/runs` | 提交自定义问题，返回202、run_id和查看/轮询链接 |
| `GET /api/v1/runs` | 按experiment_id、status筛选，limit/offset分页 |
| `GET /api/v1/runs/{id}` | 运行状态与输入 |
| `GET /api/v1/runs/{id}/result` | 最终结构化结果；尚无结果返回409 |
| `GET /api/v1/runs/{id}/events?after=0` | 增量阶段日志；用next_after继续读取 |
| `POST /api/v1/runs/{id}/replay` | 对成功Live记录发起严格离线重放 |
| `POST /api/v1/runs/{id}/cancel` | 取消尚未开始的排队任务 |
| `GET/POST /api/v1/runs/{id}/review` | 最新评价与保存新版本 |
| `GET /api/v1/runs/{id}/reviews` | 所有评价版本 |
| `GET /api/v1/runs/{id}/artifacts/{name}` | 下载清单内的原始证据，下载前验证哈希 |
| `GET /api/v1/runs/{id}/export` | 单条完整JSON导出 |
| `POST /api/v1/exports` | 选中运行的JSONL导出 |
| `POST /api/v1/evaluations` | 对指定run_ids做描述性汇总 |

POST 请求需 `Content-Type: application/json` 与 `X-Lab-Request: 1`。浏览器跨源写入被拒绝，没有开放 CORS。此接口面向本地进程集成；当前没有对外账号/权限层，不应直接改成公网监听。

提交示例见 [planner.request.json](../examples/planner.request.json)。`input_mode` 必须明确选择 `empty_ablation` 或 `snapshot`。后者需要 `/catalog` 中的 `snapshot_id`，且 `prompt` 必须逐字匹配。当前只接受已验证的 Qwen profile，不接受调用方传入任意模型地址、磁盘路径或付费搜索后端。

`client_request_id` 由调用方提供：相同请求重试返回同一运行；同一 ID 携带不同内容返回409。要有意再跑一次，应使用新 ID。重放也可通过 query 参数传入自己的 `client_request_id`。

结果结构包括：

```text
schema_version / run_id / kind / status / mode
experiment_id / parent_run_id / started_at / finished_at
input          原问题、问题哈希、快照引用、评价维度与约束
configuration  完整有效profile、profile哈希、上游SHA、依赖哈希、初搜模式、日期
output         子Query、待执行Query；answer/citations当前为null
metrics        格式、条数、完全重复数、调用数、报告Tokens、模型HTTP耗时
evaluation     评价状态、未知质量与平台差距、是否具备引用比较条件
provenance     新运行或历史导入、归一化版本
error/warnings 错误与实验限制
artifacts      原始记录名称、相对位置、大小、SHA-256
```

`result.evaluation` 是生成时的状态，后续人工标注不修改原结果；最新评价从 `/review` 读取，导出同时包含结果、最新评价及完整评价历史。消费系统不能只看生成时的 `needs_review` 字段来判断后续是否已标注。

运行状态为 `queued → running → succeeded/failed`；排队可变成 `cancelled`。服务重启后遗留的排队/运行任务标为 `interrupted`，保留证据，不自动重跑。`failed` 通常有带错误信息的最终结果；`cancelled/interrupted` 不一定有最终结果。查询状态后再取结果。

## 大系统如何调用

1. 业务系统生成 `experiment_id`、`client_request_id`，提交问题与事先定义的评价维度。
2. 保存返回的 `run_id` 和独立查看链接，用状态接口轮询；事件接口可以增量展示进度。
3. 完成后取 `/result`，把子查询交给下一组件；当前本模块不会自动执行它们。
4. 保留配置与原始证据清单，方便独立审计；需要人工复核时直接打开该运行链接。
5. 评价系统提交带 `expected_revision` 的标注，或读取JSONL自行分析；版本冲突返回409，不覆盖其他人的评价。
6. 批量评价时传入明确的run_id集合。不要把重放结果当新增样本，也不要把失效/缺失结果补成零分。

这允许将来把 Planner、Search、Crawler、Context、Answer/Citation 逐步拆为独立阶段：统一沿用运行身份、父运行引用、版本化结果与原始证据，而不依赖某个界面的内部格式。新增阶段要新增明确的组件契约，不能把当前 `planner` 结果伪装成完整研究回答。

CLI 示例：

```powershell
.venv/Scripts/python.exe scripts/lab.py list --experiment commercial-pilot-v1
.venv/Scripts/python.exe scripts/lab.py run --request examples/planner.request.json --wait
.venv/Scripts/python.exe scripts/lab.py get <run_id>
.venv/Scripts/python.exe scripts/lab.py export <run_id>
.venv/Scripts/python.exe scripts/lab.py evaluate <run_id_1> <run_id_2>
```

CLI 输出JSON。`run --wait` 最多等待300秒，成功退出0，终态失败/取消/中断退出1，等待超时退出2（任务可能仍在运行，应继续按run_id查询）。一次只运行一个原生Planner，避免其全局模块/环境补丁相互影响；排队上限24，超过返回409。

## 综合评价的边界

- 先统计所有选中运行的状态，失败与中断不丢弃。
- 重放单独计数，从生成样本数中排除；新增模型请求/Tokens记为0。
- 成功Live按实验组、模型配置、模型digest、上游SHA、依赖哈希、初搜模式、provider和提示日期分组。
- 同问题的不同初搜快照单独保留统计；格式指标先按问题内条件汇总，再对不同问题等权。模型耗时中位数和Tokens是运行级运营指标，明确显示有值的分母。
- 人工信息覆盖/约束保留各自独立。只有该类别的所有预声明条目均已标注，才计算该次运行的类别覆盖比例；部分标注保持未知。不同评分项不强行合并。
- 原始格式不合法但原生修复成功，仍将“格式合规”计为否，将“运行成功”计为是。两者不混用。
- 当前没有平台样本差距、效果迁移、统计等效性或商业ROI结论，也不输出未经约定的总分。

## 存储与验证

`data/workbench/index.sqlite3` 保存索引、状态、事件、版本化人工标注；`data/workbench/runs/<run_id>/` 保存不可覆盖的 `result.json` 和 `trace/` 原始证据。目录已加入 Git 忽略。备份时先停止服务并备份整个 `data/workbench`，或使用 SQLite 一致性备份与原始文件清单；仅复制正在写入的数据库主文件可能遗漏WAL内容。

原生 GPT Researcher 仓库未修改。原规划函数只增加可选的显式case输入和阶段观察回调，保持原提示词、原解析与本地profile不变。旧 `scripts/pilot.py` 入口继续可用。

验证证据在 [workbench_validation.json](workbench_validation.json)。接口和静态资源交付测试、JavaScript语法、真实本地生成、历史重放、导出与批量评价已执行。浏览器自动化初始化继续受到 Windows ACL 故障影响，尚未完成真实浏览器视觉与点击检查；不能把API通过当作视觉验收通过。
