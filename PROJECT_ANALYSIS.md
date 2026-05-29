# 历史报价单查询系统 — 项目分析报告

## 1. 项目概览

本项目是一个面向**电缆制造业**的历史报价单智能查询系统，核心目标是：将业务员手工维护的 Excel 成本分析表，通过 **LLM 弹性解析** 实现结构化入库，并结合 **RAGFlow 检索引擎** 提供自然语言搜索，最终将匹配结果重新渲染为标准 Excel 报价单供下载。

- **项目名称**: Historical Quote Inquiry Service
- **技术栈**: Python 3.11 + FastAPI + PostgreSQL + SQLAlchemy 2.0 + RAGFlow + DeepSeek LLM + openpyxl
- **前端**: 单页 HTML (Tailwind CSS CDN)，无前端构建工具
- **开发周期**: 2026 年 5 月创建

---

## 2. 目录结构

```
hl_Historical_Quote_Inquiry/
├── app/                          # 应用主代码
│   ├── main.py                   # FastAPI 入口，路由注册，静态文件挂载
│   ├── database.py               # SQLAlchemy 引擎与会话工厂 (支持 SQLite/PostgreSQL)
│   ├── core/
│   │   └── config.py             # Pydantic Settings (DB / RAGFlow / DeepSeek 配置)
│   ├── models/
│   │   ├── __init__.py           # 模型导出
│   │   └── quotation.py         # ORM 模型 (4 张表)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── etl.py                # ETL 上传接口: POST /api/v1/etl/upload_excel
│   │   └── search.py            # 检索接口: GET /api/v1/search/search
│   └── services/
│       ├── __init__.py
│       ├── etl_service.py        # ETL 核心: 锚点遍历 + LLM 提取 + 入库 + RAGFlow 同步
│       ├── excel_service.py      # Excel 渲染: 模板填充 → BytesIO 流返回
│       └── ragflow_client.py    # RAGFlow 客户端: 文档同步 + 混合检索
├── static/
│   └── index.html                # 前端页面 (上传 + 检索双功能区)
├── template/
│   └── baojia_template.xlsx     # Excel 报价单模板
├── data/original_excels/         # 上传的原始 Excel 归档目录
├── init_db.sql                   # 生成的 DDL 建表语句
├── .env / .env.example           # 环境变量配置
├── requirements.txt              # Python 依赖
├── start_server.ps1              # PowerShell 一键启动脚本
│
├── 工具脚本 (开发/调试用):
├── generate_sql.py               # 从 SQLAlchemy 模型生成 DDL 并执行建表
├── update_db.py                  # 对已存在的 quotation_main 追加缺失列
├── fix_fk.py                     # 修复外键约束，添加 ON DELETE CASCADE
├── inspect_excel.py              # 打印 Excel 前 20 行内容，调试用
├── test_etl.py                   # 测试 ETL 锚点解析 (不依赖 DB/LLM)
├── test_llm.py                   # 测试 DeepSeek LLM 提取效果
├── create_dummy_template.py      # 生成空白测试模板
├── HOW_TO_RUN.md                 # 手动启动指南 (中文)
└── quote_db.sqlite               # SQLite 本地测试数据库
```

---

## 3. 系统架构

```
┌──────────────┐     ┌──────────────────────────────────┐
│   浏览器      │     │         FastAPI 后端               │
│ (index.html) │     │                                    │
│              │     │  ┌──────────┐  ┌────────────────┐  │
│  上传 Excel  │────▶│  │ ETL 路由 │─▶│  etl_service   │  │
│              │     │  └──────────┘  │  ┌──────────┐  │  │
│  语义搜索    │────▶│                │  │ LLM 提取  │  │  │
│              │     │  ┌──────────┐  │  │ (DeepSeek)│  │  │
│  下载 Excel  │◀────│  │ 检索路由 │  │  └──────────┘  │  │
│              │     │  └──────────┘  │  ┌──────────┐  │  │
└──────────────┘     │       │        │  │ PG 入库   │──┼──▶ PostgreSQL
                     │       │        │  └──────────┘  │  │
                     │       │        │  ┌──────────┐  │  │
                     │       ▼        │  │ RAGFlow   │──┼──▶ RAGFlow
                     │  ┌──────────┐  │  │ 同步+检索 │  │  │
                     │  │ excel_   │  │  └──────────┘  │  │
                     │  │ service  │  │                │  │
                     │  └──────────┘  └────────────────┘  │
                     └──────────────────────────────────┘
```

---

## 4. 数据库设计

### 4.1 表关系 (ER)

```
quotation_main (1) ──< (N) quotation_material    [材料成本明细]
     │
     ├──< (N) quotation_process                  [制程费用明细]
     │
     └── (1:1) quotation_cost_summary            [全局费用汇总]
```

所有子表均通过 `quotation_id` 外键关联主表，并设置了 `ON DELETE CASCADE`。

### 4.2 quotation_main — 报价单主表

| 字段               | 类型     | 说明                                     |
|--------------------|----------|------------------------------------------|
| id                 | SERIAL   | 主键                                     |
| quotation_no       | VARCHAR  | 报价单编号 (如 `FHLR2GCB2G-50-003`)，唯一索引 |
| customer           | VARCHAR  | 客户名称/代号 (如 `6010634 800木轴`)        |
| analysis_date      | DATE     | 分析日期                                  |
| structure          | VARCHAR  | 结构 (如 `1596/0.20BC`)                   |
| product_spec       | VARCHAR  | 品名规格                                  |
| original_file_path | VARCHAR  | 原始 Excel 文件归档路径                   |
| extracted_tags     | JSON     | 提取的业务标签 (线径、料号等)              |

### 4.3 quotation_material — 材料成本明细

| 字段            | 类型     | 说明                    |
|-----------------|----------|-------------------------|
| id              | SERIAL   | 主键                    |
| quotation_id    | INTEGER  | FK → quotation_main    |
| seq_no          | INTEGER  | 序号                    |
| process_name    | VARCHAR  | 制程 (导体绞合/绝缘/编织) |
| spec_detail     | VARCHAR  | 详细规格                 |
| unit_usage      | FLOAT    | 单位用量 (KG/100M)      |
| unit_price      | FLOAT    | 单价 (RMB/KG)           |
| material_amount | FLOAT    | 材料金额 (RMB/100M)     |

### 4.4 quotation_process — 制程费用明细

| 字段              | 类型    | 说明                    |
|-------------------|---------|-------------------------|
| id                | SERIAL  | 主键                    |
| quotation_id      | INTEGER | FK → quotation_main    |
| process_name      | VARCHAR | 制程名称                 |
| std_hours         | FLOAT   | 标准工时                 |
| loss_hours        | FLOAT   | 损耗时间                 |
| fixed_rate        | FLOAT   | 固定费用率               |
| fixed_cost        | FLOAT   | 固定费用                 |
| startup_loss_wire | FLOAT   | 开机损耗废线             |
| total_waste_glue  | FLOAT   | 总废胶 (KG)             |
| amount            | FLOAT   | 金额                    |
| subtotal_cost     | FLOAT   | 费用成本小计             |

### 4.5 quotation_cost_summary — 费用汇总表

包含三组数据：**汇总成本** (材料成本、费用总计)、**杂费** (UL 标签费、运输费、包装费、废品率、报关费等)、**利润与税率** (净利率、增值税率、企业所得税率等)，以及 **最终定价** (成本 RMB/M、取利售价、不取利售价、最终售价)。

---

## 5. 核心业务流程

### 5.1 ETL 入库流程 (`POST /api/v1/etl/upload_excel`)

```
上传 Excel
  │
  ├─ 1. 物理归档 → 保存到 data/original_excels/<uuid>.xlsx
  │
  ├─ 2. 遍历每个 Sheet 的每一行，寻找锚点 "成本分析表"
  │      (支持带空格的变体如 "成 本 分 析 表")
  │
  ├─ 3. 找到锚点后，规则切块:
  │      - 表头区: 锚点下方 4 行 (客户、日期、结构、品名规格等)
  │      - 核心表格区: 表头下方直到下一个锚点或连续空行
  │
  ├─ 4. 调用 DeepSeek LLM (deepseek-chat, temperature=0) 弹性提取
  │      - 输入: 切块出的 header_text + table_text
  │      - 输出: 结构化 JSON (quotation_no, customer, materials[], processes[], cost_summary{})
  │      - LLM 做归一化: 制程名统一、料号提取 (EX/D 开头)、线径提取、百分比转小数
  │
  ├─ 5. 写入 PostgreSQL (级联覆盖: 若 quotation_no 已存在则先删除旧记录)
  │      - quotation_main → quotation_material[] → quotation_process[] → quotation_cost_summary
  │
  └─ 6. 生成文本摘要 → 同步到 RAGFlow 知识库
         (自动清理旧文档，绕过 API 405 bug 通过 requests.delete)
```

### 5.2 语义检索流程 (`GET /api/v1/search/search`)

```
用户输入自然语言查询 (如 "6010634客户 50平方 EX9201804胶料")
  │
  ├─ 1. 可选精确过滤: customer_filter 拼接到 query 中
  │      (因 RAGFlow metadata 写入存在兼容问题，采用文本拼接绕过)
  │
  ├─ 2. 调用 RAGFlow retrieve (混合检索, similarity_threshold=0.2)
  │
  ├─ 3. 解析命中结果，提取 quotation_id:
  │      - 优先从 chunk 正文 [Metadata] 块正则提取
  │      - 备用从 document_name (quote_xxx.txt) 提取
  │      - 最终 fallback 用 "编号：" 正则
  │
  ├─ 4. 根据 quotation_id 查询 PostgreSQL 获取完整结构化数据
  │      (先按主键 ID 查，再按 quotation_no 查)
  │
  └─ 5. 加载模板 template/baojia_template.xlsx
         → 填充数据 → 返回 StreamingResponse (.xlsx 文件流)
```

### 5.3 Excel 渲染流程

基于 `template/baojia_template.xlsx` 模板，填充：
- **主表信息**: H1 编号、B2 客户、D2/E2 分析日期、B3 结构、D3/E3 品名规格
- **制程明细**: 从第 6 行开始逐行填充序号/制程/规格/用量/单价/金额
- **费用汇总**: 材料成本小计 + 最终售价

---

## 6. 关键技术细节与已知问题

### 6.1 LLM 提取策略
- 使用 DeepSeek API (`deepseek-chat` 模型)，`temperature=0.0` 确保输出稳定
- 通过 `response_format={"type": "json_object"}` 强制 JSON 输出
- Prompt 包含详细的字段映射规则 (制程名归一化、料号正则提取、百分比转换)
- LLM 调用失败时返回空 `{}`，不会中断整体流程 (该报价单被跳过并 rollback)

### 6.2 RAGFlow 兼容性处理
代码中存在多处对 RAGFlow SDK bug 的 workaround：
- **元数据写入**: 将 metadata 直接追加到文档文本末尾的 `[Metadata]` 块中，绕过 `doc.update()` 的 405 错误
- **文档删除**: 使用原生 `requests.delete` 替代 SDK 的 `dataset.delete_documents()`，绕过 HTTP 方法不匹配问题
- **检索元数据提取**: 从 chunk 正文用正则提取 quotation_no，而非依赖 RAGFlow 原生 metadata 字段

### 6.3 数据库
- 代码自动建表 (`Base.metadata.create_all`)，生产环境建议改用 Alembic 迁移
- 支持 SQLite (本地测试) 和 PostgreSQL (生产)，`database.py` 自动判断
- 外键已配置 `ON DELETE CASCADE`，删除主表记录时自动级联删除子表

### 6.4 Excel 解析
- 基于锚点 "成本分析表" 进行多报价单拆分，支持一个 Excel 多个 Sheet、每个 Sheet 多个报价单
- 连续 3 行空行或遇到下一个锚点即截断当前报价单
- 最多截取 80 行防止无限循环

---

## 7. API 接口

| 方法 | 路径                          | 说明                                   |
|------|-------------------------------|----------------------------------------|
| GET  | `/`                           | 返回前端页面 (static/index.html)        |
| POST | `/api/v1/etl/upload_excel`    | 上传 Excel 进行 ETL 清洗入库            |
| GET  | `/api/v1/search/search`       | 语义搜索报价单，返回渲染后的 Excel 文件流 |
| GET  | `/docs`                       | FastAPI 自动生成的 Swagger 文档         |

---

## 8. 依赖清单

| 包名                | 版本    | 用途                    |
|---------------------|---------|-------------------------|
| fastapi             | 0.110.0 | Web 框架               |
| uvicorn             | 0.29.0  | ASGI 服务器             |
| sqlalchemy          | 2.0.29  | ORM                    |
| psycopg2-binary     | 2.9.9   | PostgreSQL 驱动         |
| openpyxl            | 3.1.2   | Excel 读写              |
| pandas              | 2.2.1   | 数据处理 (间接使用)      |
| requests            | 2.31.0  | HTTP 客户端 (RAGFlow)   |
| python-dotenv       | 1.0.1   | .env 文件加载           |
| pydantic            | 2.6.4   | 数据校验                |
| pydantic-settings   | 2.2.1   | 配置管理                |
| python-multipart    | 0.0.9   | 文件上传支持            |

另外运行时依赖 `ragflow-sdk` (RAGFlow Python SDK) 和 `openai` (用于调用 DeepSeek API，兼容 OpenAI SDK 格式)。

---

## 9. 项目评价

### 优点
- **LLM + 规则双引擎**: 用 LLM 处理 Excel 布局多变的问题，用锚点规则做切块定位，两者互补
- **完整闭环**: 上传 → 解析 → 入库 → 知识库同步 → 语义检索 → 模板渲染 → 下载，全链路打通
- **工程容错性好**: LLM 失败不阻塞流程、RAGFlow 多处兼容 workaround、支持覆盖式重复上传

### 可改进点
- `excel_service.py` 中引用了 `QuotationCost` 模型但实际模型名是 `QuotationCostSummary`，存在潜在 bug
- RAGFlow 的兼容 hack 较多，如果 RAGFlow SDK 升级可能需要清理这些 workaround
- 生产环境建议用 Alembic 替代 `Base.metadata.create_all` 做数据库迁移
- 模板位置硬编码 (`template/baojia_template.xlsx`)，如果模板变更需要同时改代码
- 前端 blob 下载方式对大文件不够友好，缺少进度反馈
