from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import xlrd
import xlwt
from sqlalchemy.orm import Session

from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.bpm_instance_service import REVIEW_PARAM_FIELDS
from app.services.calc_param_service import DEFAULT_COPPER_ROD_PROCESS_FEE, DEFAULT_VAT_RATE
from app.services.copper_scenario_service import _calculate_one_band
from app.services.glue_calc_service import _is_jacket_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERAL_TEMPLATE_PATH = PROJECT_ROOT / "通用报价单格式.xls"
GENERAL_TEMPLATE_SHEET = "2000"

TEMPLATE_BANDS = [
    {"label": "90001-92000", "header": "含税铜价\n90001-92000", "copper_price": Decimal("92000")},
    {"label": "92001-94000", "header": "含税铜价\n92001-94000", "copper_price": Decimal("94000")},
    {"label": "94001-96000", "header": "含税铜价\n94001-96000", "copper_price": Decimal("96000")},
    {"label": "96001-98000", "header": "含税铜价\n96001-98000", "copper_price": Decimal("98000")},
    {"label": "98001-100000", "header": "含税铜价\n98001-100000", "copper_price": Decimal("100000")},
    {"label": "100001-102000", "header": "含税铜价\n100001-102000", "copper_price": Decimal("102000")},
    {"label": "102001-104000", "header": "含税铜价\n102001-104000", "copper_price": Decimal("104000")},
    {"label": "104001-106000", "header": "含税铜价\n104001-106000", "copper_price": Decimal("106000")},
    {"label": "106001-108000", "header": "含税铜价\n106001-108000", "copper_price": Decimal("108000")},
    {"label": "108001-110000", "header": "含税铜价\n108001-110000", "copper_price": Decimal("110000")},
]


def render_general_batch_quote_excel(
    db: Session,
    instance_ids: list[int],
    operator: str,
) -> tuple[BytesIO, str]:
    ordered_ids = _normalize_instance_ids(instance_ids)
    if not ordered_ids:
        raise ValueError("请先选择需要导出报价单的 BPM 实例")

    rows = (
        db.query(QuotationBpmInstance, QuotationMain)
        .join(QuotationMain, QuotationMain.id == QuotationBpmInstance.quotation_main_id)
        .filter(
            QuotationBpmInstance.id.in_(ordered_ids),
            QuotationBpmInstance.deleted == False,
            QuotationMain.deleted == False,
        )
        .all()
    )
    row_map = {instance.id: (instance, quotation) for instance, quotation in rows}
    missing_ids = [item_id for item_id in ordered_ids if item_id not in row_map]
    if missing_ids:
        raise ValueError(f"以下 BPM 实例不存在或已删除：{', '.join(str(item) for item in missing_ids)}")

    export_rows = []
    bpm_values: list[str] = []
    customer_names: list[str] = []
    customer_addresses: list[str] = []
    for item_id in ordered_ids:
        instance, quotation = row_map[item_id]
        current_final = instance.final_selling_price if instance.final_selling_price is not None else quotation.final_selling_price
        if current_final in (None, ""):
            raise ValueError(
                f"{quotation.quotation_code or item_id} 尚未生成当前最终售价，请先在批量页执行“保存参数并一键计算”"
            )
        export_rows.append(_build_export_row(db, quotation, instance))
        if instance.bpm_no:
            bpm_values.append(instance.bpm_no)
        if quotation.customer_name:
            customer_names.append(quotation.customer_name)
        if quotation.customer_address:
            customer_addresses.append(quotation.customer_address)

    template_meta = _load_template_meta()
    buffer = _render_workbook(
        template_meta=template_meta,
        rows=export_rows,
        bpm_no=_shared_text(bpm_values),
        customer_name=_shared_text(customer_names),
        customer_address=_shared_text(customer_addresses),
        operator=operator,
    )
    bpm_for_name = _shared_text(bpm_values) or datetime.now().strftime("%Y%m%d%H%M%S")
    return buffer, f"{bpm_for_name}-报价单.xls"


def _build_export_row(db: Session, quotation: QuotationMain, instance: QuotationBpmInstance) -> dict:
    band_results = _calculate_template_bands(db, quotation, instance)
    jacket_row = _find_jacket_material(quotation)
    return {
        "quotation_code": quotation.quotation_code or "",
        "product_spec": quotation.product_spec or "",
        "product_name": _build_product_name(quotation, jacket_row),
        "band_prices": [item["final_selling_price"] for item in band_results],
    }


def _calculate_template_bands(db: Session, quotation: QuotationMain, instance: QuotationBpmInstance) -> list[dict]:
    params = (
        db.query(QuotationCalcParam)
        .filter(QuotationCalcParam.quotation_main_id == quotation.id)
        .first()
    )
    simulated_params = SimpleNamespace(
        copper_rod_process_fee=instance.copper_rod_process_fee
        or (params.copper_rod_process_fee if params else DEFAULT_COPPER_ROD_PROCESS_FEE),
        vat_rate=instance.vat_rate or (params.vat_rate if params else DEFAULT_VAT_RATE),
    )
    original_values = _snapshot_review_values(quotation)
    try:
        _apply_instance_review_values(quotation, instance)
        results = []
        for band in TEMPLATE_BANDS:
            result = _calculate_one_band(db, quotation, simulated_params, band["copper_price"])
            if not result.get("final_selling_price"):
                raise ValueError(f"{quotation.quotation_code or quotation.id} 在铜段 {band['label']} 未生成最终售价")
            results.append(result)
        return results
    finally:
        _restore_review_values(quotation, original_values)


def _snapshot_review_values(quotation: QuotationMain) -> dict:
    values = {field: getattr(quotation, field, None) for field in REVIEW_PARAM_FIELDS}
    values["vat_rate"] = getattr(quotation, "vat_rate", None)
    return values


def _apply_instance_review_values(quotation: QuotationMain, instance: QuotationBpmInstance) -> None:
    for field in REVIEW_PARAM_FIELDS:
        value = getattr(instance, field, None)
        if value is not None:
            setattr(quotation, field, value)
    if instance.vat_rate is not None:
        quotation.vat_rate = instance.vat_rate


def _restore_review_values(quotation: QuotationMain, values: dict) -> None:
    for field, value in values.items():
        setattr(quotation, field, value)


def _find_jacket_material(quotation: QuotationMain):
    for item in quotation.materials:
        if not item.deleted and _is_jacket_row(item):
            return item
    return None


def _build_product_name(quotation: QuotationMain, jacket_row) -> str:
    parts = []
    if quotation.structure:
        parts.append(str(quotation.structure).strip())
    material_name = _outer_material_name(jacket_row)
    if material_name:
        parts.append(material_name)
    od_or_id = _extract_od_or_id(quotation, jacket_row)
    if od_or_id:
        parts.append(od_or_id)
    return " ".join(part for part in parts if part).strip() or (quotation.product_spec or quotation.quotation_code or "")


def _outer_material_name(jacket_row) -> str:
    if not jacket_row:
        return ""
    code = str(getattr(jacket_row, "process_code", "") or "").strip().upper()
    if code.startswith("EX"):
        return "XLPE外被"
    if code.startswith("EE"):
        return "TPE外被"
    if code.startswith("C"):
        return "低毒PVC外被"
    name = str(getattr(jacket_row, "process_name", "") or "").strip()
    return name or "外被"


def _extract_od_or_id(quotation: QuotationMain, jacket_row) -> str:
    candidates = [
        getattr(jacket_row, "spec_detail", "") if jacket_row else "",
        getattr(jacket_row, "process_name", "") if jacket_row else "",
        quotation.product_spec or "",
        quotation.structure or "",
        quotation.remark or "",
    ]
    pattern = re.compile(r"\b(OD|ID)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*mm\b", re.IGNORECASE)
    for text in candidates:
        match = pattern.search(str(text or ""))
        if match:
            return f"{match.group(1).upper()}：{match.group(2)} mm"
    return ""


def _load_template_meta() -> dict:
    if not GENERAL_TEMPLATE_PATH.exists():
        raise ValueError(f"未找到报价单模板：{GENERAL_TEMPLATE_PATH}")
    book = xlrd.open_workbook(str(GENERAL_TEMPLATE_PATH), formatting_info=True)
    sheet = book.sheet_by_name(GENERAL_TEMPLATE_SHEET)
    headers = []
    for idx, band in enumerate(TEMPLATE_BANDS, start=3):
        value = str(sheet.cell_value(12, idx) or "").strip()
        headers.append(value or band["header"])
    footer_notes = []
    for row_idx in range(18, 26):
        value = str(sheet.cell_value(row_idx, 0) or "").strip()
        if value:
            footer_notes.append(value)
    return {
        "sheet_name": GENERAL_TEMPLATE_SHEET,
        "copper_headers": headers,
        "footer_notes": footer_notes,
        "col_widths": [sheet.colinfo_map.get(col).width if sheet.colinfo_map.get(col) else None for col in range(13)],
        "row_heights": {row + 1: sheet.rowinfo_map.get(row).height for row in range(29) if sheet.rowinfo_map.get(row)},
    }


def _render_workbook(
    template_meta: dict,
    rows: list[dict],
    bpm_no: str,
    customer_name: str,
    customer_address: str,
    operator: str,
) -> BytesIO:
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet(template_meta["sheet_name"], cell_overwrite_ok=True)
    _apply_sheet_dimensions(sheet, template_meta)
    styles = _build_styles()

    for row_idx in range(1, 6):
        _set_row_height(sheet, template_meta, row_idx, row_idx)
        sheet.write_merge(row_idx - 1, row_idx - 1, 0, 12, "", styles["empty"])

    _set_row_height(sheet, template_meta, 6, 6)
    sheet.write_merge(5, 5, 0, 12, "报价单", styles["title"])

    _set_row_height(sheet, template_meta, 7, 7)
    sheet.write(6, 0, "客户名称：", styles["label_left"])
    sheet.write_merge(6, 6, 1, 12, customer_name or "", styles["value_left"])

    _set_row_height(sheet, template_meta, 8, 8)
    sheet.write(7, 0, "客户地址：", styles["label_left"])
    sheet.write_merge(7, 7, 1, 12, customer_address or "", styles["value_left"])

    _set_row_height(sheet, template_meta, 9, 9)
    sheet.write(8, 0, "TEL：", styles["label_left"])
    sheet.write(8, 1, "", styles["value_left"])
    sheet.write(8, 2, "FAX：", styles["label_left"])
    sheet.write_merge(8, 8, 3, 12, "", styles["value_left"])

    _set_row_height(sheet, template_meta, 10, 10)
    sheet.write(9, 0, "ATTN:", styles["label_left"])
    sheet.write(9, 1, "", styles["value_left"])
    sheet.write(9, 2, "FROM:", styles["label_left"])
    sheet.write_merge(9, 9, 3, 12, operator or "", styles["value_left"])

    _set_row_height(sheet, template_meta, 11, 11)
    sheet.write(10, 0, "报价编号：", styles["label_left"])
    sheet.write_merge(10, 10, 1, 7, bpm_no or "", styles["value_left"])
    sheet.write_merge(10, 10, 8, 12, f"日期：{datetime.now():%Y-%m-%d}", styles["value_center"])

    _set_row_height(sheet, template_meta, 12, 12)
    sheet.write_merge(11, 12, 0, 0, "品名", styles["header"])
    sheet.write_merge(11, 12, 1, 1, "规格", styles["header"])
    sheet.write_merge(11, 12, 2, 2, "成本分析号", styles["header"])
    sheet.write(11, 3, "", styles["header"])
    sheet.write_merge(11, 11, 4, 12, "含税13%单价", styles["header"])

    _set_row_height(sheet, template_meta, 13, 13)
    sheet.write(12, 3, template_meta["copper_headers"][0], styles["header_wrap"])
    for index, header in enumerate(template_meta["copper_headers"][1:], start=4):
        sheet.write(12, index, header, styles["header_wrap"])

    data_start_row = 14
    detail_count = max(len(rows), 4)
    for offset in range(detail_count):
        row_no = data_start_row + offset
        _set_row_height(sheet, template_meta, row_no, 14 if offset == 0 else 15)
        row = rows[offset] if offset < len(rows) else None
        _write_detail_row(sheet, row_no - 1, row, styles)

    footer_row = data_start_row + detail_count
    _set_row_height(sheet, template_meta, footer_row, 18)
    sheet.write(footer_row - 1, 0, "备注：", styles["footer"])
    sheet.write_merge(footer_row - 1, footer_row - 1, 9, 12, "", styles["footer_box"])

    for idx, note in enumerate(template_meta["footer_notes"], start=1):
        row_no = footer_row + idx
        _set_row_height(sheet, template_meta, row_no, 19)
        sheet.write_merge(row_no - 1, row_no - 1, 0, 12, note, styles["footer_note"])

    sign_row = footer_row + len(template_meta["footer_notes"]) + 3
    _set_row_height(sheet, template_meta, sign_row, 29)
    sheet.write_merge(sign_row - 1, sign_row - 1, 0, 12, "客户回签 / 核准", styles["sign"])

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _apply_sheet_dimensions(sheet, template_meta: dict) -> None:
    for index, width in enumerate(template_meta.get("col_widths") or []):
        sheet.col(index).width = width or 256 * 12


def _write_detail_row(sheet, row_idx: int, row: dict | None, styles: dict) -> None:
    values = ["", "", "", *[""] * 10]
    if row:
        values[0] = row["product_name"]
        values[1] = row["product_spec"]
        values[2] = row["quotation_code"]
        for idx, price in enumerate(row["band_prices"], start=3):
            values[idx] = price
    for col_idx, value in enumerate(values):
        style = styles["detail_left"] if col_idx in {0, 1, 2} else styles["detail_center"]
        if col_idx in range(3, 13):
            style = styles["detail_numeric"]
            sheet.write(row_idx, col_idx, _excel_number(value), style)
            continue
        sheet.write(row_idx, col_idx, value, style)


def _set_row_height(sheet, template_meta: dict, target_row_no: int, template_row_no: int) -> None:
    height = (template_meta.get("row_heights") or {}).get(template_row_no)
    if height:
        sheet.row(target_row_no - 1).height = height
        sheet.row(target_row_no - 1).height_mismatch = True


def _build_styles() -> dict:
    base_font = "font: name Arial, height 220;"
    title_font = "font: name Arial, bold on, height 320;"
    header_font = "font: name Arial, bold on, height 220;"
    border_all = "borders: left thin, right thin, top thin, bottom thin;"
    align_center = "align: horiz center, vert center;"
    align_left = "align: horiz left, vert center;"
    wrap_center = "align: horiz center, vert center, wrap on;"
    wrap_left = "align: horiz left, vert center, wrap on;"
    return {
        "empty": xlwt.easyxf(base_font),
        "title": xlwt.easyxf(
            f"{title_font} {border_all} {align_center}"
        ),
        "label_left": xlwt.easyxf(f"{base_font} {border_all} {align_left}"),
        "value_left": xlwt.easyxf(f"{base_font} {border_all} {align_left}"),
        "value_center": xlwt.easyxf(f"{base_font} {border_all} {align_center}"),
        "header": xlwt.easyxf(f"{header_font} {border_all} {align_center}"),
        "header_wrap": xlwt.easyxf(f"{header_font} {border_all} {wrap_center}"),
        "detail_left": xlwt.easyxf(f"{base_font} {border_all} {wrap_left}"),
        "detail_center": xlwt.easyxf(f"{base_font} {border_all} {align_center}"),
        "detail_numeric": xlwt.easyxf(
            f"{base_font} {border_all} {align_center}",
            num_format_str="0.0000",
        ),
        "footer": xlwt.easyxf(f"{base_font} {align_left}"),
        "footer_box": xlwt.easyxf(f"{base_font} {border_all} {align_left}"),
        "footer_note": xlwt.easyxf(f"{base_font} {align_left}"),
        "sign": xlwt.easyxf(f"{header_font} {border_all} {align_center}"),
    }


def _normalize_instance_ids(instance_ids: list[int]) -> list[int]:
    ordered = []
    seen = set()
    for value in instance_ids or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _shared_text(values: list[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value or "").strip()]
    if not cleaned:
        return ""
    unique = []
    seen = set()
    for item in cleaned:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique[0] if len(unique) == 1 else " / ".join(unique)


def _excel_number(value):
    if value in (None, ""):
        return ""
    try:
        return float(Decimal(str(value)))
    except Exception:
        return value
