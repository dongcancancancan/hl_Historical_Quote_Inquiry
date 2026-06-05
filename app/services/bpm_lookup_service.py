import re

from sqlalchemy import bindparam, or_, text
from sqlalchemy.orm import Session

from app.core.config import settings

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def normalize_bpm_no(value: str | None) -> str:
    return (value or "").strip().upper()


def _quote_identifier(value: str) -> str:
    return "[" + value.replace("]", "]]") + "]"


def _bpm_view_name() -> str:
    return f"{_quote_identifier(settings.DB_NAME)}.[dbo].[BPM_B015_List]"


def get_quotation_codes_by_bpm(db: Session, bpm_no: str | None) -> list[str]:
    bpm_no = normalize_bpm_no(bpm_no)
    if not bpm_no:
        return []
    rows = db.execute(text(f"""
        SELECT DISTINCT LTRIM(RTRIM([成本分析号])) AS quotation_code
        FROM {_bpm_view_name()}
        WHERE UPPER(LTRIM(RTRIM([流水号]))) = :bpm_no
          AND [成本分析号] IS NOT NULL
    """), {"bpm_no": bpm_no}).mappings().all()
    return _unique_texts(row["quotation_code"] for row in rows)


def get_bpm_flows_by_quotation_codes(db: Session, quotation_codes: list[str]) -> dict[str, list[str]]:
    original_codes = _unique_texts(quotation_codes)
    if not original_codes:
        return {}
    key_to_originals: dict[str, list[str]] = {}
    for original_code in original_codes:
        for lookup_key in quotation_code_lookup_keys(original_code):
            key_to_originals.setdefault(lookup_key, []).append(original_code)
    rows = _fetch_bpm_rows_by_lookup_keys(db, list(key_to_originals.keys()))
    bpm_map: dict[str, list[str]] = {}
    for row in rows:
        code = str(row["quotation_code"] or "").strip()
        flow = str(row["bpm_no"] or "").strip()
        if not code or not flow:
            continue
        for lookup_key in quotation_code_lookup_keys(code):
            for original_code in key_to_originals.get(lookup_key, []):
                bpm_map.setdefault(original_code, [])
                if flow not in bpm_map[original_code]:
                    bpm_map[original_code].append(flow)
    return bpm_map


def resolve_bpm_no(bpm_map: dict[str, list[str]], quotation_code: str | None, fallback: str | None = "") -> str:
    code = str(quotation_code or "").strip()
    flows = bpm_map.get(code, [])
    return ", ".join(flows) if flows else (fallback or "").strip()


def quotation_code_lookup_keys(quotation_code: str | None) -> list[str]:
    code = str(quotation_code or "").strip()
    keys = [code] if code else []
    base_code = strip_added_chinese_suffix(code)
    if base_code and base_code != code:
        keys.append(base_code)
    return _unique_texts(keys)


def strip_added_chinese_suffix(quotation_code: str | None) -> str:
    code = str(quotation_code or "").strip()
    match = _CJK_RE.search(code)
    if not match:
        return code
    return re.sub(r"[^A-Za-z0-9]+$", "", code[:match.start()])


def build_quotation_code_filter(column, quotation_codes: list[str]):
    codes = _unique_texts(
        lookup_key
        for code in quotation_codes
        for lookup_key in quotation_code_lookup_keys(code)
    )
    if not codes:
        return column.in_([])
    clauses = [column.in_(codes)]
    for code in codes:
        clauses.append(column.like(f"{_escape_like(code)}-%", escape="\\"))
    return or_(*clauses)


def _fetch_bpm_rows_by_lookup_keys(db: Session, lookup_codes: list[str]) -> list[dict]:
    lookup_codes = _unique_texts(lookup_codes)
    if not lookup_codes:
        return []

    exact_stmt = text(f"""
        SELECT
            LTRIM(RTRIM([成本分析号])) AS quotation_code,
            LTRIM(RTRIM([流水号])) AS bpm_no
        FROM {_bpm_view_name()}
        WHERE LTRIM(RTRIM([成本分析号])) IN :quotation_codes
          AND [成本分析号] IS NOT NULL
          AND [流水号] IS NOT NULL
    """).bindparams(bindparam("quotation_codes", expanding=True))

    rows = [dict(row) for row in db.execute(exact_stmt, {"quotation_codes": lookup_codes}).mappings().all()]

    # BPM sometimes appends notes like （旧）/（新） to the cost analysis number.
    # Match those rows when the first extra character is not part of the core code.
    seen = {(row["quotation_code"], row["bpm_no"]) for row in rows}
    for chunk in _chunks([code for code in lookup_codes if len(code) >= 4], 200):
        params = {f"code_{idx}": code for idx, code in enumerate(chunk)}
        values_sql = ", ".join(f"(:code_{idx})" for idx in range(len(chunk)))
        suffix_stmt = text(f"""
            WITH lookup_keys(lookup_key) AS (
                SELECT lookup_key FROM (VALUES {values_sql}) AS v(lookup_key)
            )
            SELECT DISTINCT
                LTRIM(RTRIM(bpm.[成本分析号])) AS quotation_code,
                LTRIM(RTRIM(bpm.[流水号])) AS bpm_no
            FROM {_bpm_view_name()} bpm
            JOIN lookup_keys lk
              ON LTRIM(RTRIM(bpm.[成本分析号])) LIKE lk.lookup_key + '[^A-Za-z0-9]%'
            WHERE bpm.[成本分析号] IS NOT NULL
              AND bpm.[流水号] IS NOT NULL
        """)
        for row in db.execute(suffix_stmt, params).mappings().all():
            item = dict(row)
            key = (item["quotation_code"], item["bpm_no"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return rows


def _unique_texts(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text_value = str(value or "").strip()
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        result.append(text_value)
    return result


def _escape_like(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )


def _chunks(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start:start + size]
