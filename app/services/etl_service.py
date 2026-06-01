import os
import re
import uuid
import time
import json
import shutil
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from fastapi import UploadFile
from openai import AsyncOpenAI

from app.models.quotation import QuotationMain, QuotationMaterial, QuotationProcessFee
from app.core.config import settings

logger = logging.getLogger(__name__)

async_client = AsyncOpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url=settings.DEEPSEEK_BASE_URL
)

UPLOAD_DIR = "data/original_excels"
os.makedirs(UPLOAD_DIR, exist_ok=True)

_BRAIDING_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%?\s*编')

EXTRACTION_PROMPT = """你是一个制造业电缆报价单数据提取专家。
请从下面的【表头区数据】和【核心表格数据】中，提取指定的字段，并严格以 JSON 格式输出。

注意规则：
1. 绝对不能修改任何数值，保持原始浮点数精度！如果某个数值为空或解析不到，请使用 0.0 或 null。
2. 从详细规格中提取出所有胶料料号（常见前缀: EX, EE, D, V, C, PA），存入 material_codes 数组。
3. 将百分比转换为小数（如 2% 转换为 0.02）。如果本身是小数则保持不变。
4. 表头区"结构"列的右侧紧跟"编织率(%)"列，提取编织率百分比值，转换为小数（如 95 → 0.95、85% → 0.85），存入 braiding_rate。如果该列为空，再尝试从品名规格中提取（如 "95%编织"）。
5. 表头区"客户"列的右侧紧跟"地址"列（通常为"xx市"），存入 address。
6. 核心表格中每一行制程的规格列之后紧跟"物料编码"列，将物料编码提取到该行 material 的 material_code 字段。物料编码可能是空值或"新开发"字样，均如实提取。
7. 【非常重要】务必准确提取 "quotation_no" (编号)！在表头区，编号可能出现在 "编号:"、"编号：" 之后，或者孤立地出现在某一列（如 FHLR2GCB2G-50-003）。请仔细扫描【表头区数据】的所有列，不要遗漏。

【表头区数据】
{header_text}

【核心表格数据】
{material_text}

请严格输出如下 JSON 格式（不要输出 markdown 代码块标记，只输出合法 JSON 字符串）：
{{
    "quotation_no": "编号",
    "customer": "客户",
    "address": "地址",
    "date_val": "分析日期 (YYYY-MM-DD)",
    "structure": "结构",
    "product_spec": "品名规格",
    "braiding_rate": 浮点数 (编织率，小数形式，如 0.95),
    "material_codes": ["料号1", "料号2"],
    "materials": [
        {{
            "process_name": "制程名称",
            "spec_detail": "详细规格",
            "material_code": "物料编码（可能为空或"新开发"）",
            "unit_usage": 浮点数 (单位用量),
            "unit_price": 浮点数 (单价),
            "material_amount": 浮点数 (材料金额)
        }}
    ],
    "processes": [
        {{
            "process_name": "制程名称",
            "std_hours": 浮点数 (标准工时),
            "loss_hours": 浮点数 (损耗时间),
            "fixed_rate": 浮点数 (固定费用率),
            "fixed_cost": 浮点数 (固定费用),
            "startup_loss_wire": 浮点数 (开机损耗废线),
            "total_waste_glue": 浮点数 (每个制程总废胶),
            "amount": 浮点数 (金额),
            "subtotal_cost": 浮点数 (费用成本小计)
        }}
    ],
    "cost_summary": {{
        "total_material_cost_rmb_m": 浮点数 (材料成本 RMB/M),
        "total_material_cost_kg": 浮点数 (材料成本 Kg),
        "total_material_amount": 浮点数 (材料成本总金额),
        "total_process_cost": 浮点数 (费用总计),
        "ul_label_fee": 浮点数,
        "transport_fee": 浮点数,
        "package_fee": 浮点数,
        "scrap_rate": 浮点数,
        "startup_times": 整数 (订单开机次数),
        "delivery_fee": 浮点数,
        "customs_fee": 浮点数,
        "order_meters": 整数,
        "net_profit_rate": 浮点数,
        "vat_rate": 浮点数,
        "business_fee_rate": 浮点数,
        "monthly_interest_rate": 浮点数,
        "corp_tax_rate": 浮点数,
        "irradiation_core_count": 浮点数 (照射芯数),
        "irradiation_core_fee": 浮点数 (照射费用 RMB/M),
        "other_fee": 浮点数 (其他费用),
        "cost_rmb_m": 浮点数 (底部汇总的 成本 RMB/M),
        "price_with_profit": 浮点数 (取利售价),
        "price_without_profit": 浮点数 (不取利售价),
        "final_price": 浮点数 (最终售价)
    }}
}}"""

_mat_code_pattern = re.compile(r'((?:EX|EE|PA|D|V|C)\d+[A-Z]*)', re.IGNORECASE)


@dataclass
class QuotationBlock:
    """Excel 中一个报价单的文本块"""
    sheet_name: str
    row_idx: int
    header_text: str
    table_text: str
    preview: str  # 编号预览，用于进度展示


def _parse_braiding_rate(product_spec: str) -> float:
    """从品名规格中提取编织率，返回小数形式（95% → 0.95）"""
    m = _BRAIDING_RE.search(product_spec or "")
    if not m:
        return 0.0
    val = float(m.group(1))
    return val / 100 if val > 1 else val


def _normalize_braiding_rate(val) -> float:
    """统一编织率为 0-1 小数：如果 >1 则视为百分比除以 100"""
    v = _safe_numeric(val, scale=4)
    if v > 1:
        v = round(v / 100, 4)
    return v


def _safe_numeric(val, scale: int = 4) -> float:
    if val is None:
        return 0.0
    try:
        return round(float(val), scale)
    except (ValueError, TypeError):
        return 0.0


def _row_values_xlsx(ws, row_idx: int) -> list[str]:
    """openpyxl: 取一行所有单元格的字符串值（1-indexed）"""
    return [str(c.value).strip() if c.value is not None else "" for c in ws[row_idx]]


def _row_values_xls(ws, row_idx: int) -> list[str]:
    """xlrd: 取一行所有单元格的字符串值（0-indexed）"""
    return [str(v).strip() for v in ws.row_values(row_idx)]


def scan_quotations(file_path: str) -> list[QuotationBlock]:
    """阶段1：扫描 Excel(.xlsx/.xls)，定位所有 成本分析表 锚点并提取文本块"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(file_path)
        sheet_names = wb.sheet_names()
        row_values = lambda ws, r: _row_values_xls(ws, r - 1)  # xlrd 0-indexed
        max_row = lambda ws: ws.nrows
        is_xls = True
    else:
        wb = load_workbook(file_path, data_only=True)
        sheet_names = wb.sheetnames
        row_values = _row_values_xlsx
        max_row = lambda ws: ws.max_row
        is_xls = False

    blocks = []

    for sheet_name in sheet_names:
        ws = wb.sheet_by_name(sheet_name) if is_xls else wb[sheet_name]

        for row_idx in range(1, max_row(ws) + 1):
            vals = row_values(ws, row_idx)
            first_cell = vals[0] if vals else ""
            clean_first = first_cell.replace(" ", "")

            if "成本分析表" in clean_first:
                # 提取表头区（下 4 行）
                header_rows = []
                for i in range(1, 5):
                    header_rows.append(" | ".join(row_values(ws, row_idx + i)))
                header_text = "\n".join(header_rows)

                # 提取核心表格数据区
                table_rows = []
                mat_row = row_idx + 6
                empty_count = 0
                while mat_row <= max_row(ws):
                    row_vals = row_values(ws, mat_row)
                    cell_a = row_vals[0].replace(" ", "") if row_vals else ""

                    if "成本分析表" in cell_a:
                        break

                    if not any(row_vals):
                        empty_count += 1
                        if empty_count > 3:
                            break
                    else:
                        empty_count = 0

                    table_rows.append(" | ".join(row_vals))
                    mat_row += 1
                    if mat_row - row_idx > 80:
                        break
                table_text = "\n".join(table_rows)

                preview = header_rows[0][:60] if header_rows else f"Row {row_idx}"

                blocks.append(QuotationBlock(
                    sheet_name=sheet_name,
                    row_idx=row_idx,
                    header_text=header_text,
                    table_text=table_text,
                    preview=preview,
                ))

    if not is_xls:
        wb.close()
    return blocks


async def _extract_one_async(block: QuotationBlock, semaphore: asyncio.Semaphore) -> dict:
    """对单个报价单文本块调用 LLM 提取，受 semaphore 限制并发数"""
    prompt = EXTRACTION_PROMPT.format(
        header_text=block.header_text,
        material_text=block.table_text,
    )

    async with semaphore:
        try:
            response = await async_client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                extra_body={"thinking": {"type": "disabled"}},
            )
            result_str = response.choices[0].message.content
            return json.loads(result_str)
        except Exception as e:
            logger.error(f"LLM extraction failed for block at row {block.row_idx}: {e}")
            raise


def _enrich_extracted(data: dict, product_spec: str) -> None:
    """正则兜底补充：material_codes 和 braiding_rate"""
    # 编织率兜底
    if _safe_numeric(data.get("braiding_rate"), scale=4) == 0.0:
        data["braiding_rate"] = _parse_braiding_rate(product_spec)

    # 材料料号兜底：从规格中扫描
    existing_codes = [c.upper() for c in data.get("material_codes", [])]
    for mat in data.get("materials", []):
        spec = mat.get("spec_detail", "")
        if spec:
            for code in _mat_code_pattern.findall(str(spec)):
                if code.upper() not in existing_codes:
                    existing_codes.append(code.upper())
                    data.setdefault("material_codes", []).append(code.upper())

        # 物料编码兜底：如果 LLM 没提取到 material_code，从规格中正则提取
        if not mat.get("material_code"):
            found = _mat_code_pattern.findall(str(mat.get("spec_detail", "")))
            if found:
                mat["material_code"] = found[0]


def _quotation_exists(db: Session, quotation_code: str, tenant_id: str) -> bool:
    """检查同租户下成本分析号是否已存在"""
    return db.query(QuotationMain).filter(
        QuotationMain.quotation_code == quotation_code,
        QuotationMain.tenant_id == tenant_id,
    ).first() is not None


def _write_one_quotation(
    db: Session,
    data: dict,
    tenant_id: str,
    username: str,
    saved_path: str,
    display_name: str = "",
) -> str:
    """将单条 LLM 提取结果写入数据库，返回 quotation_code。
    如果成本分析号已存在则跳过，返回空字符串。"""
    creator_name = display_name or username
    quotation_code = data.get("quotation_no") or f"AUTO-{uuid.uuid4().hex[:6]}"
    product_spec = data.get("product_spec") or ""

    # 唯一性校验：同租户下已存在则跳过
    if _quotation_exists(db, quotation_code, tenant_id):
        logger.info(f"[{quotation_code}] 成本分析号已存在，跳过")
        return ""

    _enrich_extracted(data, product_spec)

    cost_data = data.get("cost_summary", {})
    braiding_rate = _normalize_braiding_rate(data.get("braiding_rate"))

    # 日期处理
    date_val = data.get("date_val")
    try:
        parsed_date = datetime.strptime(date_val, "%Y-%m-%d").date() if date_val else datetime.now().date()
    except Exception:
        parsed_date = datetime.now().date()

    # extracted_tags
    material_codes = list(data.get("material_codes", []))
    extracted_tags = json.dumps({
        "material_codes": material_codes,
        "customer_keyword": (data.get("customer") or "").split()[0] if data.get("customer") else None,
    }, ensure_ascii=False)

    new_main = QuotationMain(
        tenant_id=tenant_id,
        quotation_code=quotation_code,
        customer_name=data.get("customer") or "",
        customer_address=data.get("address") or "",
        analysis_date=parsed_date,
        structure=data.get("structure") or "",
        product_spec=product_spec,
        original_file_path=saved_path,
        extracted_tags=extracted_tags,
        unit_usage_sum=None,
        material_amount_sum=_safe_numeric(cost_data.get("total_material_amount")),
        material_cost=_safe_numeric(cost_data.get("total_material_cost_rmb_m")),
        total_fee=_safe_numeric(cost_data.get("total_process_cost")),
        ul_label_fee=_safe_numeric(cost_data.get("ul_label_fee")),
        transport_fee=_safe_numeric(cost_data.get("transport_fee")),
        packing_fee=_safe_numeric(cost_data.get("package_fee")),
        waste_loss_rate=_safe_numeric(cost_data.get("scrap_rate")),
        order_startup_times=_safe_numeric(cost_data.get("startup_times"), scale=0),
        other_fee=_safe_numeric(cost_data.get("other_fee")),
        delivery_fee=_safe_numeric(cost_data.get("delivery_fee")),
        irradiation_core_count=_safe_numeric(cost_data.get("irradiation_core_count")),
        irradiation_core_fee=_safe_numeric(cost_data.get("irradiation_core_fee")),
        customs_fee=_safe_numeric(cost_data.get("customs_fee")),
        order_meterage=_safe_numeric(cost_data.get("order_meters")),
        net_profit_rate=_safe_numeric(cost_data.get("net_profit_rate")),
        vat_rate=_safe_numeric(cost_data.get("vat_rate")),
        operating_expense_rate=_safe_numeric(cost_data.get("business_fee_rate")),
        monthly_interest=_safe_numeric(cost_data.get("monthly_interest_rate")),
        corporate_tax_rate=_safe_numeric(cost_data.get("corp_tax_rate")),
        cost=_safe_numeric(cost_data.get("cost_rmb_m")),
        profit_selling_price=_safe_numeric(cost_data.get("price_with_profit")),
        non_profit_price=_safe_numeric(cost_data.get("price_without_profit")),
        final_selling_price=_safe_numeric(cost_data.get("final_price")),
        braiding_rate=braiding_rate,
        creator=creator_name,
        deleted=False,
    )
    db.add(new_main)
    db.flush()

    for mat in data.get("materials", []):
        raw_code = mat.get("material_code")
        material_code = str(raw_code).strip() if raw_code and str(raw_code).strip().lower() != "null" else ""
        db.add(QuotationMaterial(
            tenant_id=tenant_id,
            quotation_main_id=new_main.id,
            process_name=mat.get("process_name") or "",
            spec_detail=mat.get("spec_detail") or "",
            unit_usage=_safe_numeric(mat.get("unit_usage")),
            unit_price=_safe_numeric(mat.get("unit_price")),
            material_amount=_safe_numeric(mat.get("material_amount")),
            process_code=material_code,
            creator=creator_name,
            deleted=False,
        ))

    for proc in data.get("processes", []):
        db.add(QuotationProcessFee(
            tenant_id=tenant_id,
            quotation_main_id=new_main.id,
            process_name=proc.get("process_name") or "",
            std_hours=_safe_numeric(proc.get("std_hours")),
            loss_hours=_safe_numeric(proc.get("loss_hours")),
            fixed_rate=_safe_numeric(proc.get("fixed_rate")),
            fixed_fee=_safe_numeric(proc.get("fixed_cost")),
            startup_loss_wire=_safe_numeric(proc.get("startup_loss_wire")),
            total_waste_glue=_safe_numeric(proc.get("total_waste_glue")),
            amount=_safe_numeric(proc.get("amount")),
            subtotal_fee=_safe_numeric(proc.get("subtotal_cost")),
            creator=creator_name,
            deleted=False,
        ))

    return quotation_code


async def process_excel_streaming(
    blocks: list[QuotationBlock],
    saved_path: str,
    db: Session,
    tenant_id: str,
    username: str,
    display_name: str = "",
):
    """阶段2+3：并行调用 LLM 提取，实时产出 SSE 进度事件，最后写库（含去重）"""
    total = len(blocks)

    if total == 0:
        yield {"event": "complete", "processed": 0, "errors": 0}
        return

    # --- 阶段2: 并行 LLM 提取 ---
    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
    results: list[dict] = []
    tasks: list[asyncio.Task] = []

    async def process_one(index: int, block: QuotationBlock):
        t0 = time.time()
        try:
            data = await _extract_one_async(block, semaphore)
            t_llm = time.time() - t0
            code = data.get("quotation_no") or "?"
            logger.info(f"[{code}] LLM 完成, 耗时 {t_llm:.1f}s")
            return {
                "index": index,
                "data": data,
                "error": None,
                "quotation_code": code,
                "llm_time": round(t_llm, 1),
            }
        except asyncio.CancelledError:
            logger.info(f"[Block {index}] LLM 任务被取消（客户端断开）")
            raise
        except Exception as e:
            t_llm = time.time() - t0
            logger.error(f"[Block {index}] LLM 失败 ({t_llm:.1f}s): {e}")
            return {
                "index": index,
                "data": None,
                "error": str(e),
                "quotation_code": block.preview[:50],
                "llm_time": round(t_llm, 1),
            }

    try:
        tasks = [asyncio.create_task(process_one(i, block)) for i, block in enumerate(blocks)]

        completed = 0
        errors = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1
            if result["error"]:
                errors += 1
            yield {
                "event": "progress",
                "current": completed,
                "total": total,
                "quotation_code": result["quotation_code"],
                "llm_time": result["llm_time"],
                "error": result["error"],
            }

        # --- 阶段3: 去重 + 写入数据库 ---
        results.sort(key=lambda r: r["index"])

        # 批次内去重：同一个成本分析号在一个 Excel 中出现多次，只保留第一个
        seen_codes: set[str] = set()
        dup_within_batch = 0
        deduped_results = []
        for r in results:
            if r["data"] is None:
                deduped_results.append(r)
                continue
            code = (r.get("quotation_code") or "").strip()
            if code and code in seen_codes:
                dup_within_batch += 1
                logger.warning(f"[{code}] 批次内重复，跳过")
                r["dup_skipped"] = True
                yield {
                    "event": "skipped",
                    "quotation_code": code,
                    "reason": "batch_dup",
                    "message": f"批次内重复：成本分析号 {code} 在本文件中已出现，保留第一个",
                }
            else:
                if code:
                    seen_codes.add(code)
                deduped_results.append(r)

        db_ready = sum(1 for r in deduped_results if r["data"] is not None and not r.get("dup_skipped"))
        yield {
            "event": "db_write",
            "message": f"LLM 解析完成，正在写入数据库 (有效 {db_ready}/{total}，批次内重复 {dup_within_batch})...",
        }

        t_db_start = time.time()
        db_success = 0
        db_duplicates = 0

        for r in deduped_results:
            if r["data"] is None or r.get("dup_skipped"):
                continue
            try:
                code = _write_one_quotation(db, r["data"], tenant_id, username, saved_path, display_name)
                if code:
                    db_success += 1
                    logger.info(f"[{code}] DB 写入完成")
                else:
                    db_duplicates += 1
                    logger.info(f"[{r['quotation_code']}] DB 中已存在，跳过")
                    yield {
                        "event": "skipped",
                        "quotation_code": r["quotation_code"],
                        "reason": "db_dup",
                        "message": f"历史重复：成本分析号 {r['quotation_code']} 已在数据库中，跳过",
                    }
            except Exception as e:
                logger.error(f"[{r['quotation_code']}] DB 写入失败: {e}")
                db.rollback()

        db.commit()
        t_db = time.time() - t_db_start

        total_skipped = dup_within_batch + db_duplicates
        logger.info(
            f"全部完成: LLM={completed}个 DB写入={db_success}个 "
            f"跳过={total_skipped}个(批内重复{dup_within_batch}/DB已存在{db_duplicates}) "
            f"DB耗时={t_db:.1f}s"
        )
        yield {
            "event": "complete",
            "processed": db_success,
            "total": total,
            "llm_errors": errors,
            "dup_within_batch": dup_within_batch,
            "db_duplicates": db_duplicates,
            "db_time": round(t_db, 1),
        }

    except asyncio.CancelledError:
        logger.warning("ETL stream cancelled, cleaning up pending tasks")
        raise
    finally:
        cancelled_count = 0
        for t in tasks:
            if not t.done():
                t.cancel()
                cancelled_count += 1
        if cancelled_count > 0:
            logger.warning(f"Cancelled {cancelled_count} pending LLM tasks due to disconnect")


def get_upload_history(db: Session, tenant_id: str, creator_name: str, is_admin: bool = False, limit: int = 500):
    """查询报价单明细（按日期分组），管理员可查看所有"""
    from sqlalchemy import func, Date

    filters = [
        QuotationMain.tenant_id == tenant_id,
        QuotationMain.deleted == False,
    ]
    if not is_admin:
        filters.append(QuotationMain.creator == creator_name)

    rows = (
        db.query(
            QuotationMain.quotation_code,
            QuotationMain.customer_name,
            QuotationMain.product_spec,
            QuotationMain.creator,
            func.cast(QuotationMain.create_time, Date).label("create_date"),
            QuotationMain.create_time,
            QuotationMain.original_file_path,
        )
        .filter(*filters)
        .order_by(QuotationMain.create_time.desc())
        .limit(limit)
        .all()
    )

    # 按日期分组
    from collections import OrderedDict
    groups = OrderedDict()
    for code, customer, spec, creator, create_date, create_time, path in rows:
        date_key = str(create_date) if create_date else "未知日期"
        if date_key not in groups:
            groups[date_key] = []
        groups[date_key].append({
            "quotation_code": code,
            "customer_name": customer or "",
            "product_spec": spec or "",
            "upload_user": creator or "",
            "create_time": create_time.isoformat() if create_time else None,
            "filename": os.path.basename(path) if path else "",
        })

    return [
        {"date": date, "items": items}
        for date, items in groups.items()
    ]


def delete_quotation(db: Session, quotation_code: str, tenant_id: str, creator_name: str, is_admin: bool = False) -> bool:
    """删除指定成本分析号（管理员可删任意，普通用户仅可删自己），返回是否成功"""
    filters = [
        QuotationMain.quotation_code == quotation_code,
        QuotationMain.tenant_id == tenant_id,
        QuotationMain.deleted == False,
    ]
    if not is_admin:
        filters.append(QuotationMain.creator == creator_name)

    q = db.query(QuotationMain).filter(*filters).first()
    if not q:
        return False
    db.delete(q)
    db.commit()
    logger.info(f"[{quotation_code}] 已由 {creator_name} 删除 (admin={is_admin})")
    return True


# 保留同步版本，兼容旧的调用方式
def process_and_store_excel(file: UploadFile, db: Session, tenant_id: str, username: str) -> dict:
    """同步版：保存文件 + 扫描 + 串行 LLM + 写入（兼容旧接口）"""
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    saved_path = os.path.join(UPLOAD_DIR, unique_filename)

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    logger.info(f"Saved original excel to {saved_path}")

    blocks = scan_quotations(saved_path)
    processed_count = 0
    seen_codes: set[str] = set()

    for block in blocks:
        try:
            t0 = time.time()
            data = asyncio.run(_extract_one_async(
                block, asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
            ))
            t_llm = time.time() - t0

            # 批次内去重
            code = data.get("quotation_no") or f"AUTO-{uuid.uuid4().hex[:6]}"
            if code in seen_codes:
                logger.warning(f"[{code}] 批次内重复，跳过")
                continue
            seen_codes.add(code)

            t1 = time.time()
            quotation_code = _write_one_quotation(db, data, tenant_id, username, saved_path)
            t_db = time.time() - t1

            if quotation_code:
                logger.info(f"[{quotation_code}] 耗时: LLM={t_llm:.1f}s  DB={t_db:.1f}s")
                db.commit()
                processed_count += 1
            else:
                logger.info(f"[{code}] 已存在，跳过")

        except Exception as e:
            db.rollback()
            import traceback
            logger.error(f"Error parsing quotation at row {block.row_idx}: {str(e)}\n{traceback.format_exc()}")

    return {"status": "success", "processed_quotations": processed_count, "saved_file": saved_path}
