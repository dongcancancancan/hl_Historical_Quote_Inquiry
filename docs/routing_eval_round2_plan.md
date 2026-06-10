# Routing Eval Round2 Plan

## 范围

- 本轮只评估 `routing_issue` / `mixed_issue`。
- 不纳入 `not_routing_issue`。
- 只调整 `prompt_rules.business_rules`。
- 不改代码。
- 不接自动采用。
- 不改 router 输出 schema。

## Round2 评估指标

- `manual_review` 是否合理下降。
- 高置信错误是否减少。
- 是否能识别价格缺失不是路由问题。
- 是否能在 `reason` 中指出多阶段 skill 需求。
- `matched_material_ids / matched_process_ids` 是否更接近人工判断。

## 观察项

- 多 skill / 分段路由只是 Round2 的观察项，不是开发项。
- 现在不要做 route_plan，不要改 decision_json schema，不要新增表，不要接自动计算。