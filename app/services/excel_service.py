import os
from typing import Optional
from io import BytesIO
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from app.models.quotation import QuotationMain, QuotationMaterial
import logging

logger = logging.getLogger(__name__)


def render_quotation_excel(db: Session, quotation_id: int) -> Optional[BytesIO]:
    """根据 quotation_id 从数据库获取结构化数据，使用模板渲染 Excel 报价单"""
    main = db.query(QuotationMain).filter(QuotationMain.id == quotation_id).first()
    if not main:
        logger.warning(f"Quotation {quotation_id} not found in DB.")
        return None

    materials = db.query(QuotationMaterial).filter(
        QuotationMaterial.quotation_main_id == quotation_id
    ).order_by(QuotationMaterial.seq_no).all()

    # 费用汇总直接从 main 表读取 (quotation_cost_summary 已弃用)

    template_path = "template/baojia_template.xlsx"
    if not os.path.exists(template_path):
        logger.error(f"Template not found at {template_path}")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "报价单"
    else:
        wb = load_workbook(template_path)
        ws = wb.active

    # 填充主表信息 (使用新字段名)
    ws.cell(row=1, column=8, value=f"编号: {main.quotation_code}")
    ws.cell(row=2, column=2, value=main.customer_name)
    if main.analysis_date:
        ws.cell(row=2, column=5, value=main.analysis_date.strftime("%Y.%m.%d"))
    ws.cell(row=3, column=2, value=main.structure)
    ws.cell(row=3, column=5, value=main.product_spec)

    # 填充材料成本明细
    start_row = 6
    for idx, mat in enumerate(materials):
        row = start_row + idx
        ws.cell(row=row, column=1, value=mat.seq_no)
        ws.cell(row=row, column=2, value=mat.process_name)
        ws.cell(row=row, column=3, value=mat.spec_detail)
        ws.cell(row=row, column=7, value=float(mat.unit_usage or 0))
        ws.cell(row=row, column=8, value=float(mat.unit_price or 0))
        ws.cell(row=row, column=9, value=float(mat.material_amount or 0))

    # 填充费用汇总 (直接从 main 表读取)
    cost_row = start_row + len(materials) + 2
    ws.cell(row=cost_row, column=1, value="材料成本小计")
    ws.cell(row=cost_row, column=2, value=float(main.material_amount_sum or 0))
    ws.cell(row=cost_row + 1, column=1, value="最终售价")
    ws.cell(row=cost_row + 1, column=2, value=float(main.final_selling_price or 0))

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
