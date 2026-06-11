# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Backend
uvicorn app.main:app --reload                          # Start dev server
python -m py_compile app/services/xxx.py               # Quick syntax check (no test suite)

# Frontend (Vue 3)
cd frontend/review-v2 && npm run dev                   # Dev server (127.0.0.1:5173)
cd frontend/review-v2 && npm run check                 # Type-check only
cd frontend/review-v2 && npm run build                 # Full build → ../../static/review-v2/

# One-shot: type-check + build
npm --prefix frontend/review-v2 run build
```

No test suite exists. Validate changes by `py_compile` for backend and `vue-tsc --noEmit` for frontend.

## Architecture Overview

**Stack**: FastAPI (Python) + Vue 3/Element Plus (TypeScript) + SQL Server (pyodbc/SQLAlchemy) + DeepSeek LLM (Volcengine ARK)

**Two user roles**: 工程部 (engineering, uploads Excel) and 审价科 (reviewers, adjust prices & quote).

### Core Data Flow

```
工程部 uploads Excel (.xls/.xlsx)
  → ETL pipeline (etl_service.py): openpyxl scan → LLM extraction → DB write
  → Three tables: quotation_main, quotation_material, quotation_process_fee

审价科 reviews in workbench (App.vue)
  → iframe preview rendered server-side from DB (excel_preview_service.py)
  → Edit unit prices / review params → trigger calculation skills

Calculation skills (calculation_skill_engine.py):
  → conductor_calc_service.py  — 导体/编织单价 & 制程费用
  → glue_calc_service.py       — 胶料/外购物料 & 绝缘/外被/包带/倒线/对绞/集合
  → price_summary_calc_service.py — 最终售价汇总
  → Each is a registered Skill with phase & order; execute sequentially
```

### Key Architectural Decisions

- **Excel formulas are NOT evaluated**: openpyxl reads cached values (`data_only=True`). All recalculation logic is reimplemented in Python calculation services. Plans to use LibreOffice headless for formula evaluation are under discussion.
- **LLM parses Excel structure**: During ETL, raw cell text is sent to DeepSeek which returns JSON with structured fields (materials, processes, summaries). Python then patches header/cost-summary fields directly from Excel cells.
- **BPM instance model**: `quotation_bpm_instance` links a cost analysis sheet to a BPM workflow. Same sheet can be reused across multiple BPM flows. Review params (copper price, fees, rates) are per-instance.
- **VAT rate split**: `conductor_vat_rate` (copper formula only, stored in `quotation_calc_params.vat_rate`) vs general `vat_rate` (price summary, stored in `quotation_main.vat_rate`). They are independent.
- **`data-*` attributes** on preview HTML cells (`data-entity`, `data-id`, `data-field`) enable iframe editing and calculation trace tooltips via embedded JS.

### Database

- **SQL Server** (not PostgreSQL — README is outdated). Connection via `mssql+pyodbc`.
- **dbo schema views**: `BPM_B015_List` maps BPM flow numbers to cost analysis codes. `Sys_BPMUser` for auth. `v_qs_bzcb` / `v_qs_PVCBOM` for external material prices.
- **Schema migrations**: `schema_ensure_service.py` runs at startup, adds missing columns and adjusts precision (e.g., `monthly_interest` upgraded to `NUMERIC(18,10)`).
- **Soft delete**: `deleted` boolean column on main/material/process/instance tables.

### Frontend Build Output

Vue build outputs to `static/review-v2/` (git-tracked). Each page has its own `.html` entry + shared `assets/` chunks. The `Cache-Control: no-store` middleware on `/static/review-v2/*` prevents stale assets after deploy.

### Backend Key Modules

| Module | Purpose |
|--------|---------|
| `etl_service.py` | Excel upload → LLM extract → DB write (largest file) |
| `excel_preview_service.py` | Server-side HTML rendering of cost sheets for iframe preview |
| `bpm_instance_service.py` | BPM instance CRUD, review params sync, quoting workflow |
| `calc_param_service.py` | Calc params persistence, vat rate normalization (multiplier ↔ rate) |
| `schema_ensure_service.py` | Startup DB schema migrations |
| `batch_quote_export_service.py` | Multi-quotation export using 通用报价单格式.xlsx template |
| `calculation_skill_engine.py` | Skill registry + execution ordering |
| `calculation_context.py` | Per-request tracking of calculated material/process IDs |

### Frontend Pages

- `App.vue` — 审价科 workbench (main page)
- `BatchApp.vue` — Batch review params by BPM number
- `components/QuoteList.vue` — Left sidebar with BPM-grouped collapsible list
- `api.ts` — All API calls, session token management
- `types.ts` — Shared TypeScript interfaces


## Windows PowerShell 中文测试注意事项

在 PowerShell 中不要直接通过 here-string 管道传中文到 Python，例如：

```powershell
@'
text = "芯押"
'@ | py -



## 项目业务边界

- ERP 数据只通过视图读取，不写入、不同步回 ERP；平台只写自己的业务表、缓存表、快照表。
- 审价参数以 BPM 实例维度为准；同一成本分析表可被多个 BPM 流程复用，不能互相覆盖参数。
- 增值税率分两类：`增值税率（铜杆）` 必须大于 0；报价区的 `增值税率` 可为 0。
- 已报价数据以报价快照为准，后续上传或重新计算不得影响已报价快照。
- 计算逻辑优先放在确定性 skill/服务代码中，LLM 只做辅助诊断或路由建议，不能作为最终计算依据。