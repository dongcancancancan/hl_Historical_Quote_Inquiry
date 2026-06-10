export type ReviewStatus = "pending" | "quoted";
export type StatusTone = "info" | "success" | "warning" | "danger";

export interface QuoteItem {
  instance_id?: number | null;
  quotation_code: string;
  bpm_no?: string;
  customer_name?: string;
  package_method?: string;
  product_spec?: string;
  upload_user?: string;
  create_time?: string;
  quote_date?: string;
  review_status?: ReviewStatus;
  final_selling_price?: string;
}

export interface ReviewHistoryResponse {
  pending: QuoteItem[];
  quoted?: QuoteItem[];
}

export interface QuotationListResponse {
  items: QuoteItem[];
  bpm_no?: string;
  mapped_codes?: string[];
}

export interface BatchResult {
  updated?: number;
  calculated?: number;
  deleted?: number;
  skipped?: Array<{ quotation_code?: string; reason?: string }>;
}

export interface CopperScenarioBand {
  label: string;
  copper_min?: number;
  copper_max?: number;
  copper_price: string | number;
}

export interface CopperScenarioCell {
  label?: string;
  copper_price?: string | number;
  cost?: string;
  profit_selling_price?: string;
  non_profit_price?: string;
  final_selling_price?: string;
  material_cost?: string;
  total_fee?: string;
  error?: string;
}

export interface CopperScenarioItem {
  quotation_code: string;
  bpm_no?: string;
  customer_name?: string;
  product_spec?: string;
  review_status?: ReviewStatus;
  current_final_selling_price?: string;
  bands: CopperScenarioCell[];
  errors?: string[];
}

export interface CopperScenarioResponse {
  bpm_no: string;
  bands: CopperScenarioBand[];
  items: CopperScenarioItem[];
  mapped_codes?: string[];
}

export interface CopperFeeItem {
  id: number;
  copper_type: "BC" | "TC" | string;
  diameter: string;
  tin_price_basis?: string | null;
  processing_fee: string;
  minimum_fee?: string | null;
  remark?: string;
  enabled: boolean;
  creator?: string;
  create_time?: string | null;
  updater?: string;
  update_time?: string | null;
}

export interface CopperFeePayload {
  copper_type: string;
  diameter: string;
  tin_price_basis?: string | null;
  processing_fee: string;
  minimum_fee?: string | null;
  remark?: string;
  enabled: boolean;
}

export interface CopperFeeMatchResponse {
  matched: boolean;
  material_code: string;
  copper_type: string;
  diameter: string;
  tin_price_basis: string;
  fee?: CopperFeeItem | null;
}

export interface CopperFeeLog {
  id: number;
  fee_id: number;
  action: string;
  before_data?: Record<string, unknown> | null;
  after_data?: Record<string, unknown> | null;
  operator?: string;
  operate_time?: string | null;
}

export interface PvcMaterialPriceItem {
  id?: number | null;
  prd_no: string;
  name: string;
  unit: string;
  unit_price?: string | null;
  has_price?: boolean;
  used_count?: number;
  effective_date?: string | null;
  remark?: string;
  operator?: string;
  create_time?: string | null;
  update_time?: string | null;
}

export interface PvcMaterialPricePayload {
  prd_no: string;
  name: string;
  unit: string;
  unit_price: string;
  effective_date?: string | null;
  remark?: string;
}

export interface PvcMaterialPriceLog {
  id: number;
  material_price_id?: number | null;
  prd_no?: string;
  action: string;
  before_data?: Record<string, unknown> | null;
  after_data?: Record<string, unknown> | null;
  operator?: string;
  operate_time?: string | null;
}

export interface PvcBomMain {
  id: number;
  bom_no: string;
  name: string;
  total_weight?: string | null;
  total_amount?: string | null;
  cost?: string | null;
  process_fee?: string | null;
  package_fee?: string | null;
  sale_price?: string | null;
  operator?: string;
  modify_time?: string | null;
}

export interface PvcBomDetail {
  id: number;
  bom_no: string;
  parent_name?: string;
  material_no: string;
  material_name: string;
  unit: string;
  quantity?: string | null;
  unit_price?: string | null;
  amount?: string | null;
}

export interface PvcBomDetailResponse {
  main: PvcBomMain;
  details: PvcBomDetail[];
}

export interface CalcParams {
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
  update_time?: string;
}

export interface TraceItem {
  id: number;
  run_id?: number | null;
  bpm_instance_id?: number | null;
  calc_type?: string;
  field_name: string;
  display_label?: string;
  formula?: string;
  process_text?: string;
  result_value?: string;
}

export interface TraceGroup {
  title: string;
  rows: TraceItem[];
}

export interface SkillItem {
  id: string;
  name: string;
  phase: string;
  order: number;
  description: string;
  capabilities: string[];
}

export interface DiagnosisResult {
  mode?: string;
  quotation_code?: string;
  summary?: string;
  skills?: SkillItem[];
}

export interface RoutingDecision {
  route_type?: string;
  target_skill?: string;
  target_subtype?: string;
  mapping_mode?: string;
  confidence?: number | string;
  reason?: string;
  matched_material_ids?: number[];
  matched_process_ids?: number[];
  manual_review_required?: boolean;
}

export interface RoutePlanGroup {
  group_id: string;
  step_order: number;
  group_type: string;
  target_skill: string;
  match_status: string;
  manual_review_required: boolean;
  confidence: number | string;
  material_ids: number[];
  process_ids: number[];
  material_names: string[];
  process_names: string[];
  reason: string;
  rule_hits: string[];
}

export interface RoutePlanUnmatchedDetail {
  item_type: "material" | "process";
  item_id: number;
  item_name: string;
  status: string;
  suggested_skill?: string;
  manual_review_required: boolean;
  reason: string;
}

export interface RoutePlanResult {
  route_type: "route_plan";
  summary_status: string;
  manual_review_required: boolean;
  confidence: number | string;
  reason: string;
  quotation_code?: string;
  instance_id?: number | null;
  groups: RoutePlanGroup[];
  unmatched_material_ids: number[];
  unmatched_process_ids: number[];
  unmatched_details: RoutePlanUnmatchedDetail[];
  warnings: string[];
  meta?: Record<string, unknown>;
}

export interface RoutingTestPayload {
  route_scene?: string;
  trigger_source?: string;
  error_message?: string;
  focus_material_ids?: number[];
  focus_process_ids?: number[];
}

export interface RoutingTestResponse {
  routing_run_id: number;
  policy_id?: number | null;
  policy_name?: string;
  decision?: RoutingDecision;
  final_action?: string;
  final_skill?: string;
  adopt_status?: string;
  error_message?: string;
}

export interface RoutePlanTestResponse {
  routing_run_id: number;
  policy_id?: number | null;
  policy_name?: string;
  route_plan?: RoutePlanResult;
  final_action?: string;
  final_skill?: string;
  adopt_status?: string;
  error_message?: string;
}
