# 独立站与制造商搜索需求测试包 v1

16个模拟情境：独立站8个、制造商8个。日期固定为2026-09-28，每题预留2次独立生成。所有业务背景为虚构，参考搜索词为人工编写的草案，尚未运行或确认为验收标准。

## 如何使用

1. 打开 http://127.0.0.1:8765/ ，把下方完整输入或 prompts/ 中对应文本粘贴到问题框。只粘贴需求，不把参考答案放入模型输入。
2. 选择“无初搜实验”（empty_ablation）。本批问题没有匹配初搜快照，不能套用旧的6题快照。它与原来有初搜结果的baseline必须分组比较。
3. 记录运行ID和返回的完整sub_queries。每题重新生成2次；Replay只测可重复性，不能作为第二次随机生成。
4. 在表格“实测记录”中填写结果、已覆盖维度、遗漏约束和标注者。若失败也保留该行；未运行与失败不应记成内容质量零分。
5. “参考拆分”按语义判断，不要求逐字匹配、相同排序或每条查询都重复全部条件。每个关键方面至少应有有效检索路径。数量、预算可保留为后续筛选约束；当前工作台没有验证下游筛选，应人工记下是否遗漏。

当前输出范围仅为Query Planner。每题列出的“检索后应取得的信息”是未来完整Agent的目标结构，不是本次生成结果。当前不检查最终答案、真实网页引用或平台差距。D07和M08用于观察数据缺失和歧义处理，不应混入常规题后直接宣称通过率。

## 机器接口

`cases.json`保存题目、人工参考和评价维度；`prompts/*.txt`保存可直接粘贴的原始输入；`requests/*.json`是现有工作台RunRequest请求，`requests.jsonl`是相同32个请求的逐行集合。参考答案未放入请求。

在lab目录可手动调用：`.\.venv\Scripts\python.exe scripts\lab.py run --request data\benchmarks\commerce_manufacturing_v1\requests\D01_r1.json --wait`。这会执行一次本地模型生成；本次仅生成资料，未提交任何请求。

API提交使用POST /api/v1/runs，并带X-Lab-Request: 1头和JSON请求体。JSONL不是已实现的批量上传接口，需要逐行调用；文本文件不是网页附件上传。重复发送同一个client_request_id会返回既有运行，重做试验需使用新的试验ID/请求ID，保留旧记录。

## 用例目录

| 编号 | 类别 | 场景 | 类型 |
| --- | --- | --- | --- |
| D01 | 独立站 | 消费者寻找品牌 | 常规 |
| D02 | 独立站 | 竞品定价与定位 | 常规 |
| D03 | 独立站 | 选品与用户痛点 | 常规 |
| D04 | 独立站 | SEO内容与搜索意图 | 常规 |
| D05 | 独立站 | 跨境退货政策核查 | 常规 |
| D06 | 独立站 | 仓储履约供应商选择 | 常规 |
| D07 | 独立站 | 广告与SEO投入比较 | 边界 |
| D08 | 独立站 | 平台政策时效追踪 | 常规 |
| M01 | 制造商 | 品牌方寻找OEM工厂 | 常规 |
| M02 | 制造商 | 工业零件采购 | 常规 |
| M03 | 制造商 | 出口获客与经销商发现 | 常规 |
| M04 | 制造商 | 材料替代与技术核查 | 常规 |
| M05 | 制造商 | 同行能力与网站证据 | 常规 |
| M06 | 制造商 | 出口产品合规路径 | 常规 |
| M07 | 制造商 | 海外展会与渠道预算 | 常规 |
| M08 | 制造商 | 模糊需求与前提识别 | 边界 |

## 输入与参考输出

### D01 消费者寻找品牌

我住在美国，想从品牌独立站购买适合夏季跑步的美利奴羊毛短袖，预算每件80美元以内，女款M码。请寻找3个候选品牌，比较羊毛含量、尺码、现价、寄往美国的运费和退货条件。只把能找到官网证据的商品列为候选，不要用亚马逊商品替代。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. women merino wool running t shirt lightweight summer under 80 USD official store USA
2. women merino running shirt official fabric composition size M price
3. merino running apparel official store United States shipping returns policy

**检查重点：** 搜索集合包含商品发现、规格核验和购买条件；不能只搜美利奴知识科普。预算为单件商品预算，到手成本单列。

**常见错误：** 转为男款、厚毛衣或平台代购；漏掉运费退货；提前断言某品牌有库存。

**后续完整检索应取得的信息：** 候选品牌、商品链接、材质、尺码证据、价格、运费、退货政策；缺失字段标待核实。

### D02 竞品定价与定位

我们准备做面向美国市场的不锈钢保温杯独立站，主推带手柄、带吸管的40oz产品。请研究 Stanley、Owala、Simple Modern 的相近官网产品，比较常规售价与促销价、容量和材质、配件、质保及免邮门槛，为我们寻找差异化方向。不要把不同容量混在一起，也不要把标价当成销量。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. Stanley Owala Simple Modern 40 oz handle straw tumbler official US price sale
2. Stanley Owala Simple Modern 40 oz tumbler materials accessories official
3. Stanley Owala Simple Modern US warranty free shipping threshold official

**检查重点：** 三个品牌均有检索路径；价格、规格和购买政策分别有覆盖。缺少可比产品时应保留缺口。

**常见错误：** 仅搜索一个品牌；用20oz或普通水瓶直接比较；捏造销量、份额。

**后续完整检索应取得的信息：** 按品牌和可比型号列产品表，区分促销、原价与不可比项，再列有证据支持的定位机会。

### D03 选品与用户痛点

我们经营面向英国消费者的通勤背包独立站，想开发可装16英寸笔记本、适合雨天骑行的新款。请检索2025年1月1日至2026年9月28日公开的用户评价和讨论，整理防水、背负舒适、电脑仓尺寸及拉链耐用性方面的反复抱怨，并对照品牌官网规格。把论坛个案与多来源重复问题分开，不要把评论数量当作市场规模。

**参考搜索方向（人工草案）**

1. UK cycling commute backpack 16 inch laptop waterproof complaints reviews 2025 2026
2. commuter backpack rain zipper durability shoulder comfort user reviews UK 2025 2026
3. cycling commuter backpack official laptop compartment dimensions waterproof specifications

**检查重点：** 既寻找用户经验，也寻找规格证据；时间范围可在检索词或后续筛选中实现。

**常见错误：** 只返回品牌宣传词；把防泼水当成完全防水；仅按16英寸标签断言所有笔记本适配。

**后续完整检索应取得的信息：** 痛点、对应评论链接与日期、独立来源数、官网规格冲突及待验证产品假设。

### D04 SEO内容与搜索意图

我们面向美国销售儿童不锈钢保温饭盒，准备做英语SEO内容。请研究用户关于能否放洗碗机、食物保温时长、漏汤和密封圈清洁的搜索需求，区分购买比较、使用方法和问题排查，给出值得单独建页的主题及证据来源。不凭空给搜索量或关键词难度，也不要为了排名编造安全承诺。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. kids stainless steel insulated food jar dishwasher safe cleaning gasket
2. children food thermos how long keep food warm leaking seal troubleshooting
3. best kids insulated lunch food jar comparison leakproof dishwasher safe USA

**检查重点：** 搜索词覆盖不同意图，含饭盒具体问题；可以用food jar、thermos等同义词。

**常见错误：** 三条都是best lunch box；扩展成成人塑料便当盒；输出无来源的精确搜索量。

**后续完整检索应取得的信息：** 主题簇、意图、页面建议、可支持内容的资料；搜索量和难度留空待专门数据源。

### D05 跨境退货政策核查

我们是一家从中国向德国消费者销售女士亚麻连衣裙的独立站，既有普通现货，也有按客户具体尺寸定制的商品。请查找截至2026年9月28日欧盟和德国官方关于网购撤回权、退货费用、瑕疵商品及定制商品例外的资料，帮助列出制定退货条款前应核查的事项。不要假设所有定制标签都能免退，也不要把英国规则用于德国。

**参考搜索方向（人工草案）**

1. EU Germany online purchase withdrawal right return shipping costs trader outside EU official
2. Germany distance selling personalised made to measure clothing withdrawal exception official
3. EU Germany defective goods legal guarantee online purchase official

**检查重点：** 针对德国和欧盟分别找依据；撤回权、瑕疵责任和定制例外不能混为一项。

**常见错误：** 只搜英国或美国政策；把定制商品不退写成无条件结论；拿其他店铺条款当法律依据。

**后续完整检索应取得的信息：** 事项、官方来源、适用条件、现货与定制差异、仍需专业核实的问题；不预写法律结论。

### D06 仓储履约供应商选择

我们的宠物用品独立站每月约800个美国订单，平均每单1件、包裹1磅，50个SKU。准备在美国找第三方仓库，请搜索有Shopify对接能力的3PL，比较入库、仓储、拣货打包、配送、退货处理和最低月费。优先寻找公开计费规则，区分公开价格与必须询价的部分，不要把单个运费示例当全美国报价。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. US ecommerce 3PL Shopify integration 800 orders per month 50 SKUs pricing minimum
2. US fulfillment provider receiving storage pick pack fees one item 1 lb parcel
3. US 3PL returns processing shipping zones minimum monthly fee official pricing

**检查重点：** 覆盖仓库操作费与承运费；数量和重量作为询价场景保留，不要求所有词塞进每条搜索。

**常见错误：** 只搜快递公司；省略最低月费；假定全国统一运费或直接承诺两日达。

**后续完整检索应取得的信息：** 服务商、计费组成、公开价格证据、缺失的尺寸/区域分布/库容和待询价清单。

### D07 广告与SEO投入比较

我们准备在美国销售家用手冲咖啡器具，月营销预算3000美元，暂时没有真实订单转化数据。请检索Google搜索广告和SEO内容获客的公开参考资料，关注购买意图、广告基准数据口径、内容制作成本和从点击到毛利的评估方法。给出后续试验需要收集的数据，不要凭行业平均值承诺ROAS，也不要直接声称某渠道一定更划算。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. US pour over coffee equipment buy search intent keywords comparison
2. Google Search Ads ecommerce benchmark CPC conversion rate methodology date sample
3. ecommerce SEO content cost paid search incremental gross profit measurement

**检查重点：** 寻找成本和测量依据，不把找一个平均CPC当作完成全部需求；区分广告样本与自家效果。

**常见错误：** 虚构本站转化率、利润或回本天数；把SEO曝光当作增量订单；直接给最佳渠道定论。

**后续完整检索应取得的信息：** 基准来源及样本口径、成本项目、需采集的点击/订单/毛利/退款数据和小额试验设计。

### D08 平台政策时效追踪

我们的独立站使用Shopify Payments，店铺经营主体在美国。请查询2026年7月1日至9月28日期间Shopify官方发布的支付费用或拒付处理相关变化，分清公告日期、实际生效日期、美国适用范围和其他地区。没有找到明确变化就标记证据不足，不要把帮助页面更新时间直接当成政策生效日。

**参考搜索方向（人工草案）**

1. site:shopify.com Shopify Payments United States fee changes July August September 2026
2. site:shopify.com Shopify Payments chargeback update United States July 1 September 28 2026
3. site:help.shopify.com Shopify Payments fee chargeback effective date United States 2026

**检查重点：** 将费用和拒付拆开，并保留美国与明确日期范围；后续必须读页面核验日期。

**常见错误：** 只搜最新Shopify新闻；以旧文重发冒充新规；把欧洲费率套到美国。

**后续完整检索应取得的信息：** 变化条目、公告日、生效日、适用区域、官方链接；无法证实则记录未发现足够证据。

### M01 品牌方寻找OEM工厂

我们是英国护肤品牌，计划采购30ml可回收PET材质真空泵瓶，首单5000套，需要定制颜色和丝印。请寻找中国具有该类包装生产能力的制造商，核查材料、泵体兼容性、起订量、打样周期、模具归属和是否实际生产。优先工厂官网，把贸易商与制造商分开，不要把自称环保当作有认证。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. China manufacturer 30ml PET airless pump bottle custom color silk screen MOQ 5000
2. PET airless cosmetic packaging factory pump compatibility sample lead time official
3. custom airless bottle manufacturer tooling ownership recyclable certification evidence

**检查重点：** 围绕具体包装规格和OEM能力找候选，再核实证据；对无法证实的实际生产身份保留未知。

**常见错误：** 变成采购护肤配方或玻璃滴管瓶；把宣传认证直接当验证结果；漏掉模具和泵体问题。

**后续完整检索应取得的信息：** 供应商官网、规格匹配证据、生产身份依据、MOQ和打样公开信息、需询价及索证字段。

### M02 工业零件采购

我们是一家德国自动化设备公司，要采购CNC加工的6061-T6铝合金支架，单批200件，关键孔位公差±0.02mm，黑色阳极氧化。请寻找可服务德国客户的欧洲或中国工厂，比较加工与检测能力、表面处理、质量体系和交期证据。图纸尚未发出，请不要给精确单价或把工厂宣传的最高精度等同于本零件保证公差。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. CNC machining manufacturer 6061 T6 aluminium bracket 200 pieces tolerance 0.02 mm Germany Europe China
2. CNC aluminium black anodizing precision hole inspection CMM manufacturer capability
3. CNC machining supplier Germany ISO 9001 certificate verification lead time quotation drawing

**检查重点：** 保留材料牌号、精度单位、数量和表面处理；认证与检测属于核实目标，不能自动视为已有。

**常见错误：** 误把0.02mm写成0.2mm；搜成3D打印或其他材质；声称所有零件都能达到网页最高精度。

**后续完整检索应取得的信息：** 工厂候选及工艺证据、检测方式、证书核验线索、图纸/尺寸/验收标准等RFQ缺项。

### M03 出口获客与经销商发现

我们是中国食品级硅胶厨房用品制造商，想在德国拓展B2B客户。请寻找公开经营硅胶烘焙模具或厨房工具的进口商、批发商和私牌品牌，区分三种客户类型，核查产品匹配、德国业务存在和官网公开商务联系渠道。不要收集个人私人联系方式，不要仅凭销售类似产品就断言其正在采购。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. Deutschland Silikon Backformen Küchenhelfer Importeur Großhandel
2. Germany silicone kitchenware bakeware private label brand wholesale official
3. Silikon Küchenartikel Großhändler Deutschland Unternehmen Kontakt Einkauf

**检查重点：** 搜索目标是潜在买家而不是更多中国同行；允许德语关键词；公司类型需要证据。

**常见错误：** 返回大量终端零售商品或中国供应商；凭空写联系人和订单金额；推断企业已表示采购意向。

**后续完整检索应取得的信息：** 企业官网、客户类型、德国业务和产品证据、商务联系页、匹配理由与未知采购状态。

### M04 材料替代与技术核查

我们生产商用咖啡设备，需要评估与饮用水接触的密封圈材料。使用条件是持续90°C热水，偶尔用碱性清洁液清洗。请检索硅胶与EPDM在这一场景下的技术资料，比较耐温、压缩永久变形、清洁剂兼容性和饮用水接触合规证据。优先材料厂商数据表和认证机构资料，不要因为材料名称相同就认定所有配方适用。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. silicone EPDM seal continuous 90 C hot water compression set technical datasheet
2. EPDM silicone rubber alkaline cleaner compatibility hot water seal manufacturer
3. silicone EPDM drinking water contact compound certification verification

**检查重点：** 既找性能数据也找合规证据；保留持续温度与清洗条件，识别浓度和压力未给出。

**常见错误：** 只比耐温上限；把常温兼容性直接套到90°C；承诺任意EPDM或硅胶都可饮用水接触。

**后续完整检索应取得的信息：** 具体牌号数据、测试工况、认证范围、尚缺清洁剂浓度/压力/寿命试验；不直接批准替代。

### M05 同行能力与网站证据

我们是一家中国纸质化妆品包装盒制造商，准备更新英文独立站。请研究服务欧美小品牌的同行工厂如何公开呈现低起订量、结构打样、FSC相关证据、印刷工艺和交付能力，并找出哪些页面信息能帮助采购商做初筛。把官网自述与可独立核验的证据分开，不要因为页面排名靠前就称其质量最好。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. cosmetic paper packaging box manufacturer low MOQ structural sample custom printing Europe USA
2. cosmetic packaging manufacturer FSC certificate verification official
3. custom cosmetic box factory capabilities sample lead time quality inspection case study

**检查重点：** 找到与包装盒相关的工厂页面和认证核验方向；目标是证据和内容缺口，不是空泛网站设计。

**常见错误：** 搜成塑料瓶制造商；把所有FSC标识都视为有效认证；声称抄页面就能提升AI引用。

**后续完整检索应取得的信息：** 同行页面样本、证据类型、可核验程度、建议补充的工厂事实和资料，未验证效果单列。

### M06 出口产品合规路径

我们是中国USB供电智能台灯制造商，产品含蓝牙模块，不附带交流电源适配器，拟销往法国和德国。请搜索截至2026年9月28日欧盟官方资料，梳理适用法规的核查路径、制造商和进口商责任、技术文件及测试证据要求，关注无线功能、电气与材料要求。不要把CE当成向欧盟统一机构购买的一张证书，也不要默认没有适配器就没有合规义务。

**参考搜索方向（人工草案）**

1. site:europa.eu Bluetooth USB lamp radio equipment manufacturer importer conformity technical documentation
2. site:europa.eu electrical electronic lighting equipment RoHS scope manufacturer obligations
3. site:europa.eu CE marking radio equipment declaration of conformity testing requirements

**检查重点：** 蓝牙特征进入检索；寻找适用范围和责任证据，不只是CE代办广告。

**常见错误：** 忽略无线模块；直接认定只需一张CE证书；在未核查技术参数时保证全部符合。

**后续完整检索应取得的信息：** 法规适用性核查表、所需产品参数、责任主体、测试和文件清单，标记待专业确认的范围。

### M07 海外展会与渠道预算

我们生产食品和化妆品纸包装，计划在2027年上半年参加一次欧洲B2B包装展。请寻找德国、法国或意大利的候选展会，核查2027年1月1日至6月30日的实际举办日期、地点、观众构成、参展商目录和公开参展费用。总预算2万欧元，费用不公开时列出询价项，不要沿用2026年的日期或把展位裸地费当总成本。以2026年9月28日能查到的资料为准。

**参考搜索方向（人工草案）**

1. packaging trade fair 2027 January June Germany France Italy food cosmetics official dates
2. packaging exhibition 2027 Europe official exhibitors visitor profile paper packaging
3. packaging trade fair 2027 exhibitor stand space fees services registration official

**检查重点：** 含未来活动年份与国家；先核查是否已公布再比成本；预算是总成本约束。

**常见错误：** 搜索2026或下半年展会当候选；把往届展商目录标为2027；仅用裸地费断言预算充足。

**后续完整检索应取得的信息：** 候选展会、已公布状态、2027日期证据、观众匹配、公开费项、差旅搭建等预算缺项。

### M08 模糊需求与前提识别

我们是做苹果配件的工厂，想找欧洲客户，也想知道要什么认证。这里的“苹果配件”可能指苹果品牌电子设备周边，也可能指水果苹果分选设备零件，目前还没确定业务线。请先列清需要确认的问题，再分别搜索两条业务线有哪些潜在B2B客户类型和合规核查入口。不要擅自选一种解释，不要捏造产品电压、无线功能或目标国家。资料截至2026年9月28日。

**参考搜索方向（人工草案）**

1. Europe Apple device accessories distributor wholesale compliance product category
2. Europe apple fruit sorting equipment spare parts distributor manufacturer
3. EU official product requirements electronics accessories machinery spare parts scope

**检查重点：** 在搜索集合中保留两条业务线；不把猜测变成用户事实。澄清问题属于未来完整Agent能力，本阶段只人工记录是否识别歧义。

**常见错误：** 只搜索iPhone充电器；未经说明默认德国或蓝牙产品；直接给唯一认证清单。

**后续完整检索应取得的信息：** 需要确认的产品类别、国家、用途、功能和供应链角色；分支客户类型与官方核查入口。
