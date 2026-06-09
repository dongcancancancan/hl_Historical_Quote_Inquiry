import type {
  CalcParams,
  BatchResult,
  CopperFeeItem,
  CopperFeeLog,
  CopperFeeMatchResponse,
  CopperFeePayload,
  CopperScenarioResponse,
  DiagnosisResult,
  PvcMaterialPriceItem,
  PvcMaterialPriceLog,
  PvcMaterialPricePayload,
  PvcBomDetailResponse,
  PvcBomMain,
  QuotationListResponse,
  ReviewHistoryResponse,
  SkillItem,
  TraceItem,
} from "./types";

const API_ROOT = "/api/v1/etl";
const COPPER_FEES_ROOT = "/api/v1/copper-fees";
const PVC_MATERIAL_PRICES_ROOT = "/api/v1/pvc-material-prices";
const PVC_BOMS_ROOT = "/api/v1/pvc-boms";
const SESSION_KEYS = ["token", "tenantId", "tenantName", "userName", "displayName", "isAdmin", "role"];

export function authToken(): string {
  return sessionStorage.getItem("token") || "";
}

export function assertReviewerSession(): void {
  const token = authToken();
  const role = sessionStorage.getItem("role");
  if (!token) window.location.href = "/static/login.html";
  if (role !== "reviewer") window.location.href = "/static/index.html";
}

export function openInternalPage(url: string): void {
  const target = window.open("about:blank", "_blank");
  if (!target) {
    window.location.href = url;
    return;
  }
  try {
    SESSION_KEYS.forEach((key) => {
      const value = sessionStorage.getItem(key);
      if (value !== null) target.sessionStorage.setItem(key, value);
    });
    target.location.href = url;
  } catch {
    target.location.href = url;
  }
}

function headers(json = false): HeadersInit {
  const base: Record<string, string> = { Authorization: "Bearer " + authToken() };
  if (json) base["Content-Type"] = "application/json";
  return base;
}

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: any = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      if (!res.ok) throw new Error(text);
      throw new Error("服务返回格式异常");
    }
  }
  if (!res.ok) throw new Error(formatErrorDetail(data.detail || data.message || "请求失败"));
  return data as T;
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const row = item as Record<string, unknown>;
          const loc = Array.isArray(row.loc) ? row.loc.join(".") : "";
          const msg = row.msg || row.message || JSON.stringify(row);
          return loc ? `${loc}: ${msg}` : String(msg);
        }
        return String(item);
      })
      .join("；");
  }
  if (detail && typeof detail === "object") {
    const row = detail as Record<string, unknown>;
    return String(row.msg || row.message || JSON.stringify(row));
  }
  return String(detail || "请求失败");
}

export function selectedQuery(code: string, instanceId?: number | null): string {
  const params = new URLSearchParams({ code });
  if (instanceId) params.set("instance_id", String(instanceId));
  return params.toString();
}

export async function fetchReviewHistory(search = ""): Promise<ReviewHistoryResponse> {
  const params = search ? "?" + new URLSearchParams({ search }).toString() : "";
  const res = await fetch(`${API_ROOT}/review/history${params}`, { headers: headers() });
  return parseJson<ReviewHistoryResponse>(res);
}

export async function fetchQuotationsByBpm(bpmNo: string): Promise<QuotationListResponse> {
  const res = await fetch(`${API_ROOT}/quotations?${new URLSearchParams({ bpm_no: bpmNo }).toString()}`, {
    headers: headers(),
  });
  return parseJson<QuotationListResponse>(res);
}

export async function fetchPreview(code: string, instanceId?: number | null): Promise<string> {
  const res = await fetch(`${API_ROOT}/quotation/preview?${selectedQuery(code, instanceId)}`, {
    headers: headers(),
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(formatErrorDetail(data.detail || "预览加载失败"));
  }
  return res.text();
}

export async function fetchCalcParams(code: string, instanceId?: number | null): Promise<CalcParams> {
  const res = await fetch(`${API_ROOT}/quotation/calc-params?${selectedQuery(code, instanceId)}`, {
    headers: headers(),
  });
  return parseJson<CalcParams>(res);
}

export async function saveCalcParams(
  code: string,
  instanceId: number | null | undefined,
  data: CalcParams,
): Promise<CalcParams> {
  const res = await fetch(`${API_ROOT}/quotation/calc-params?${selectedQuery(code, instanceId)}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  return parseJson<CalcParams>(res);
}

export async function batchSaveCalcParams(data: {
  quotation_codes?: string[];
  instance_ids?: number[];
  copper_price: string | number | null;
  copper_rod_process_fee: string | number | null;
  vat_rate: string | number | null;
  transport_fee?: string | number | null;
  other_fee?: string | number | null;
  net_profit_rate?: string | number | null;
  customs_fee?: string | number | null;
  order_meterage?: string | number | null;
  operating_expense_rate?: string | number | null;
  monthly_interest?: string | number | null;
  corporate_tax_rate?: string | number | null;
  calculate_after_save: boolean;
}): Promise<BatchResult> {
  const res = await fetch(`${API_ROOT}/quotation/calc-params/batch`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  return parseJson<BatchResult>(res);
}

export async function batchDeleteQuotations(data: {
  quotation_codes?: string[];
  instance_ids?: number[];
}): Promise<BatchResult> {
  const res = await fetch(`${API_ROOT}/quotations/batch`, {
    method: "DELETE",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  return parseJson<BatchResult>(res);
}

export async function calculateCopperScenarios(bpmNo: string): Promise<CopperScenarioResponse> {
  const res = await fetch(`${API_ROOT}/quotation/copper-scenarios`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ bpm_no: bpmNo }),
  });
  return parseJson<CopperScenarioResponse>(res);
}

export async function fetchCopperFees(filters: {
  copper_type?: string;
  keyword?: string;
  include_disabled?: boolean;
}): Promise<CopperFeeItem[]> {
  const params = new URLSearchParams({
    copper_type: filters.copper_type || "",
    keyword: filters.keyword || "",
    include_disabled: String(!!filters.include_disabled),
  });
  const res = await fetch(`${COPPER_FEES_ROOT}?${params.toString()}`, { headers: headers() });
  const data = await parseJson<{ items: CopperFeeItem[] }>(res);
  return data.items || [];
}

export async function saveCopperFee(id: number | null, data: CopperFeePayload): Promise<CopperFeeItem> {
  const res = await fetch(`${COPPER_FEES_ROOT}${id ? `/${id}` : ""}`, {
    method: id ? "PATCH" : "POST",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  const payload = await parseJson<{ item: CopperFeeItem }>(res);
  return payload.item;
}

export async function disableCopperFee(id: number): Promise<void> {
  const res = await fetch(`${COPPER_FEES_ROOT}/${id}`, { method: "DELETE", headers: headers() });
  await parseJson<{ ok: boolean }>(res);
}

export async function matchCopperFee(materialCode: string): Promise<CopperFeeMatchResponse> {
  const params = new URLSearchParams({ material_code: materialCode });
  const res = await fetch(`${COPPER_FEES_ROOT}/match?${params.toString()}`, { headers: headers() });
  return parseJson<CopperFeeMatchResponse>(res);
}

export async function fetchCopperFeeLogs(id: number): Promise<CopperFeeLog[]> {
  const res = await fetch(`${COPPER_FEES_ROOT}/${id}/logs`, { headers: headers() });
  const data = await parseJson<{ items: CopperFeeLog[] }>(res);
  return data.items || [];
}

export async function importCopperFeeExcel(file: File): Promise<{ created: number; updated: number }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${COPPER_FEES_ROOT}/import/excel`, {
    method: "POST",
    headers: headers(),
    body: form,
  });
  return parseJson<{ created: number; updated: number }>(res);
}

export async function fetchPvcMaterialPrices(keyword = ""): Promise<PvcMaterialPriceItem[]> {
  const res = await fetch(`${PVC_MATERIAL_PRICES_ROOT}?${new URLSearchParams({ keyword }).toString()}`, {
    headers: headers(),
  });
  const data = await parseJson<{ items: PvcMaterialPriceItem[] }>(res);
  return data.items || [];
}

export async function savePvcMaterialPrice(
  id: number | null | undefined,
  data: PvcMaterialPricePayload,
): Promise<PvcMaterialPriceItem> {
  const res = await fetch(`${PVC_MATERIAL_PRICES_ROOT}${id ? `/${id}` : ""}`, {
    method: id ? "PATCH" : "POST",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  const payload = await parseJson<{ item: PvcMaterialPriceItem }>(res);
  return payload.item;
}

export async function fetchPvcMaterialPriceLogs(prdNo = ""): Promise<PvcMaterialPriceLog[]> {
  const params = prdNo ? "?" + new URLSearchParams({ prd_no: prdNo }).toString() : "";
  const res = await fetch(`${PVC_MATERIAL_PRICES_ROOT}/logs${params}`, { headers: headers() });
  const data = await parseJson<{ items: PvcMaterialPriceLog[] }>(res);
  return data.items || [];
}

export async function importPvcMaterialPriceExcel(
  file: File,
): Promise<{ created: number; updated: number; skipped: number; errors?: string[] }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${PVC_MATERIAL_PRICES_ROOT}/import/excel`, {
    method: "POST",
    headers: headers(),
    body: form,
  });
  return parseJson<{ created: number; updated: number; skipped: number; errors?: string[] }>(res);
}

export async function fetchPvcBoms(keyword = ""): Promise<PvcBomMain[]> {
  const res = await fetch(`${PVC_BOMS_ROOT}?${new URLSearchParams({ keyword }).toString()}`, { headers: headers() });
  const data = await parseJson<{ items: PvcBomMain[] }>(res);
  return data.items || [];
}

export async function fetchPvcBomDetail(bomNo: string): Promise<PvcBomDetailResponse> {
  const res = await fetch(`${PVC_BOMS_ROOT}/${encodeURIComponent(bomNo)}`, { headers: headers() });
  return parseJson<PvcBomDetailResponse>(res);
}

export async function savePvcBomFees(
  bomNo: string,
  data: { process_fee: string; package_fee: string },
): Promise<PvcBomMain> {
  const res = await fetch(`${PVC_BOMS_ROOT}/${encodeURIComponent(bomNo)}/fees`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(data),
  });
  const payload = await parseJson<{ main: PvcBomMain }>(res);
  return payload.main;
}

export async function calculatePvcBom(bomNo: string): Promise<PvcBomDetailResponse> {
  const res = await fetch(`${PVC_BOMS_ROOT}/${encodeURIComponent(bomNo)}/calculate`, {
    method: "POST",
    headers: headers(),
  });
  return parseJson<PvcBomDetailResponse>(res);
}

export async function calculate(
  code: string,
  instanceId: number | null | undefined,
  type: "conductor" | "glue" | "full-price",
): Promise<Record<string, any>> {
  const res = await fetch(`${API_ROOT}/quotation/calculate/${type}?${selectedQuery(code, instanceId)}`, {
    method: "POST",
    headers: headers(),
  });
  return parseJson<Record<string, any>>(res);
}

export async function fetchTraces(
  code: string,
  instanceId: number | null | undefined,
  type: "conductor" | "glue" | "price-summary",
): Promise<TraceItem[]> {
  const res = await fetch(`${API_ROOT}/quotation/calculate/${type}/traces?${selectedQuery(code, instanceId)}`, {
    headers: headers(),
  });
  const data = await parseJson<{ items: TraceItem[] }>(res);
  return data.items || [];
}

export async function fetchSkills(): Promise<SkillItem[]> {
  const res = await fetch(`${API_ROOT}/quotation/calculate/skills`, { headers: headers() });
  const data = await parseJson<{ items: SkillItem[] }>(res);
  return data.items || [];
}

export async function diagnose(
  code: string,
  instanceId: number | null | undefined,
  errorMessage = "",
): Promise<DiagnosisResult> {
  const res = await fetch(`${API_ROOT}/quotation/calculate/diagnose?${selectedQuery(code, instanceId)}`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ error_message: errorMessage }),
  });
  return parseJson<DiagnosisResult>(res);
}

export async function updateQuotation(
  code: string,
  instanceId: number | null | undefined,
  changes: Array<{ entity: string; id: number; field: string; value: string }>,
): Promise<{ quotation_code: string; updated_fields: number }> {
  const res = await fetch(`${API_ROOT}/quotation?${selectedQuery(code, instanceId)}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify({ changes }),
  });
  return parseJson<{ quotation_code: string; updated_fields: number }>(res);
}

export async function clearUnitPrices(code: string, instanceId?: number | null): Promise<{
  cleared: number;
  cleared_materials?: number;
  cleared_processes?: number;
  cleared_traces?: number;
}> {
  const res = await fetch(`${API_ROOT}/quotation/unit-prices/clear?${selectedQuery(code, instanceId)}`, {
    method: "PATCH",
    headers: headers(),
  });
  return parseJson<{
    cleared: number;
    cleared_materials?: number;
    cleared_processes?: number;
    cleared_traces?: number;
  }>(res);
}

export async function markQuoted(
  code: string,
  instanceId?: number | null,
): Promise<{ review_status: string; snapshot_id?: number; calculation_run_id?: number }> {
  const res = await fetch(`${API_ROOT}/quotation/review-status?${selectedQuery(code, instanceId)}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify({ status: "quoted" }),
  });
  return parseJson<{ review_status: string; snapshot_id?: number; calculation_run_id?: number }>(res);
}

export async function exportExcel(code: string, instanceId?: number | null): Promise<void> {
  const res = await fetch(`${API_ROOT}/quotation/export?${selectedQuery(code, instanceId)}`, {
    headers: headers(),
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(formatErrorDetail(data.detail || "导出失败"));
  }
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${code}.xlsx`;
  link.click();
  URL.revokeObjectURL(url);
}
