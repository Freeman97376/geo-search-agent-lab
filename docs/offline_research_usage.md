# 离线研究评审使用说明

这一版可以管理研究草案、保存分组、导入已产生的开发集输出、单人盲评、保留修订历史，并导出供其他系统使用的结果。裁判传输只有模拟实现，没有联网适配器或密钥读取逻辑。

## 打开工作台

在 `C:\Users\Zheng\Desktop\market data tracker\geo_search_agent_lab` 运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/lab.py --store data/workbench/offline-review serve --offline --port 8766
```

访问 <http://127.0.0.1:8766/>。只监听本机。`--offline` 会拒绝原工作台的模型运行/重放调度，首页直接进入研究盲评。保留旧服务的默认行为；原工作台新增 `/research` 入口。不要在同一结果目录启动两个服务。

## 首次试用

1. 点击“创建模拟研究”。系统建立 30 个草案问题的清单和 300 个计划槽位，只为开发集的 36 个参考槽位生成明确标为模拟的 query。不会运行模型，也不会生成任何人工评分。24 个审计参考槽位仍封存。
2. 点选一条样本，按信息需求和约束分别选择完整、部分、缺失或不确定，再独立标记冲突。完整/部分覆盖或有冲突时，必须引用对应 query 的逐字证据。
3. 保存初评。每次修改保存为新版本；汇总固定采用初评，修改版本在 JSON/CSV 中保留。初评期间不显示温度、模型标识、裁判内容和原始响应。
4. 所有开发集样本完成初评或导入明确失败/不可评状态后，开放分析与导出。这样可以避免先看一部分模型信息影响后续盲评。

模拟数据只能验证工作流，不能用于判断模型表现。可以创建多个独立研究；同一研究的语料、规则和分组冻结后不允许覆盖。API 和数据库隔离是本地工作流约束，不是针对有本机文件权限人员的保密系统。

## 准备真实研究

[30题及规则草案](../examples/research_draft_v1.json) 覆盖五行业、六需求类型；它们是作者构造的问题，**没有冒充真实客户日志或已核实的公开资料**。`source_status=pending_verification`、`review_status=draft`，当前文件使用 `data_kind=fixture`。

用户需先审阅问题自然度、家族独立性、评分项和时间参照，补充 `source_urls`、带时区的 `captured_at`，再标为 `source_status=verified`、`review_status=single_reviewed`。真实研究使用新的 `study_id` 和 `version`，将 `data_kind` 改为 `imported` 后导入。`verified` 是人工声明，程序只校验字段与 URL 格式，不自行认证来源。

每类别固定 3 个开发家族、2 个审计家族；每题从五次重复中预选一个重复序号，两温度共用该序号。清单包含 300 个槽位、精确执行顺序和种子，但**本轮不自动运行这 300 次实验**。审计划分和槽位通过内部清单持久化；公开评阅 API 只展示 36 个开发参考槽位。初搜快照与模型实参验证仍须在真实执行前完成。

## CLI 与接口

```powershell
# 输出新的草案文件；已有文件不会被覆盖
.\.venv\Scripts\python.exe -X utf8 scripts/research.py draft --output examples/my-draft.json

# 与上述服务共用研究目录；选择另一目录时就是独立实验空间
.\.venv\Scripts\python.exe -X utf8 scripts/research.py --store data/workbench/offline-review/research create --input examples/my-reviewed-study.json
.\.venv\Scripts\python.exe -X utf8 scripts/research.py --store data/workbench/offline-review/research status my-study
```

接口前缀 `/api/v1/research`。所有写请求必须带 `X-Lab-Request: 1`；浏览器写请求还要通过同源检查。完整契约位于 `/api/v1/research/schemas`。

| 用途 | 接口 |
| --- | --- |
| 新建冻结研究 | `POST /studies` |
| 状态与盲评队列 | `GET /studies/{study_id}` |
| 单条盲评输入 | `GET /studies/{study_id}/slots/{slot_id}` |
| 导入离线输出 | `PUT /studies/{study_id}/slots/{slot_id}/output` |
| 新增评分版本 | `POST /studies/{study_id}/slots/{slot_id}/reviews` |
| 解盲后单条完整记录 | `GET /studies/{study_id}/slots/{slot_id}/result` |
| 汇总 | `GET /studies/{study_id}/analysis` |
| 导出 | `GET /studies/{study_id}/export/json` 或 `/csv` |
| 模拟裁判 | `POST /studies/{study_id}/slots/{slot_id}/grades/{ordinal}/fixture` |
| 人工严重错误注释 | `POST /studies/{study_id}/slots/{slot_id}/critical-errors` |
| 模拟预算状态 | `GET /budget` |
| 核实模拟请求费用 | `POST /attempts/{attempt_id}/reconcile-fixture` |

输出导入要求 `query_origin=model_only`，不得把上游自动追加的原问题当成模型拆分成果。`source_sha256` 必须等于 `raw_output` UTF-8 字节的 SHA-256。它校验保存内容完整性，不证明调用者声明的模型或 query 来源真实；真实采集器接入仍需核实映射。同一 `source_run_id` 不得占据两个槽位。失败输出有独立原因、没有可评分 query，不允许用重跑结果覆盖。

评分含 `expected_revision`，版本冲突返回 409；字段错误返回 422。审计、非参考槽位和过早解盲返回 409。审计输出没有可调用的解锁路由。已有输出、研究版本和评分历史不会被原地覆盖。

JSON 提供研究、家族、槽位、来源运行、模型/配置、时间戳、初评和修订、裁判尝试及关键错误关联。CSV 每行对应一条人工评分项及版本，并处理公式注入；完整原始数据以 JSON 为准。错误注释与模拟裁判目前通过接口使用，界面可在解盲后查看完整记录。

## 模拟裁判与预算

配置是 DeepSeek Pro、低强度思考、4,096 生成 token 目标、180 秒总时限、不自动重试。请求只带原任务、冻结规则、时间参照及模型 query，隐藏被评模型和温度。无联网传输代码；真实服务是否执行 combined token cap 尚未验证。

预算以整数微美元记录，同一研究数据库内所有研究共用 10 美元上限。按保守的输入上界和最大输出预留，事务保证并发不会超额入场；任何未确定费用的请求仍占预留，超时/网络/限流/账户错误暂停批次。重启后运行中的尝试转为中断并暂停。核实模拟费用后仅继续未开始的请求，已开始的相同 ordinal 不能再调用。

这里的 charged/reserved 都是**模拟账本**，并非真实账户消费；每个独立测试数据库的账本是隔离的。未来启用真实服务时必须接入统一累计账本、核验最新价格及完整 token 计费，不允许通过换目录重置真实预算。

每类预选一个开发家族的两温度参考输出，共 12 个诊断槽位，每份最多评分三次；第二、三次必须事先入选且按顺序运行。第一次失败也保留为主记录。无有效 JSON、重复字段、缺少评分项、伪造引用或输出截断均记录失败，保留原响应，不自动修复。

## 统计边界

- 信息需求和约束分别计算完整覆盖；部分覆盖不自动折算半分，不确定标签给出上下界。上下界不是置信区间。
- 按家族等权，保留两温度与不同模型/配置分组。没有人工评分或裁判失败时不补零。
- 主分析采用初次人工评分和首次裁判评分，报告人机标签混淆、等家族一致率、偏差/绝对误差上下界及两温度效应偏差。重复裁判仅报告自身波动，不制造新家族。
- 当前是开发集参考子集，不提供正式通过线、温度赢家、平台等效结论或人际一致率。正式置信区间、功效校准、300次全样本分析及第二位人工验证仍未执行。

## 复验

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
node --test tests/test_prompt_library.cjs
```

`tests/test_research_browser.cjs` 使用本机已有 Playwright/Chromium、临时服务 18766 和临时数据库，测试真实浏览器点击、输入校验、版本历史、汇总与导出。需要可解析 `playwright` 的 `NODE_PATH`；若浏览器不在默认缓存位置，用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定已有可执行文件。本轮未安装依赖或下载浏览器。测试不会访问外部网页。
