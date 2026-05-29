# 历史报价单查询系统 (后端服务)

本项目是历史报价单查询系统的核心后端服务，主要负责：
1. **ETL 管道**：解析 Excel 报价单，提取结构化数据入库 (PostgreSQL)。
2. **知识库同步**：生成文本摘要，调用 RAGFlow API 将数据同步至检索引擎。
3. **混合检索编排**：接收前端/用户的查询请求，调用 RAGFlow 进行语义/元数据检索，命中后返回对应的报价单 ID。
4. **Excel 渲染**：基于原始模板，将数据库中的结构化数据重新渲染为标准 Excel 文件供下载。

## 技术栈
- 框架: FastAPI
- 数据库 ORM: SQLAlchemy 2.0
- 数据库: PostgreSQL
- Excel 处理: openpyxl
- 检索引擎: RAGFlow

## 快速开始

1. 创建虚拟环境并安装依赖：
   ```bash
   py -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. 复制环境变量配置：
   ```bash
   cp .env.example .env
   ```
   修改 `.env` 中的数据库连接与 RAGFlow 配置。

3. 生成测试模板（可选）：
   ```bash
   python create_dummy_template.py
   ```

4. 启动服务：
   ```bash
   uvicorn app.main:app --reload
   ```

4. 访问 API 文档：
   http://127.0.0.1:8000/docs
