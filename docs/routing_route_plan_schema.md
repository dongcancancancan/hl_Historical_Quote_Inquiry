# Routing Route Plan Schema

## 一、目的

本文件定义下一阶段 `route_plan` 的正式输出结构，用于替代当前单一 `target_skill` 路由测试结果。

本设计只定义 schema，不实现代码，不接入正式计算，不改现有金额计算逻辑。

适用目标：

- 让 LLM 返回整张成本分析表的分组编排结果，而不是只返回一个 skill。
- 明确每组材料行、制程行与 skill 的对应关系。
- 明确哪些行已匹配、哪些未匹配、哪些必须人工复核。

## 二、设计原则

- `route_plan` 只做路由决策，不做金额计算。
- 每个分组最终仍由确定性 skill 执行。
- 路由结果默认是“建议”，不是正式计算结果。
- 低置信度、未匹配、歧义配对必须保留 `manual_review_required=true`。
- 当前阶段前端只做只读展示，不做人工拖拽确认。

## 三、顶层结构

```json
{
  "route_type": "route_plan",
  "summary_status": "partial_match",
  "manual_review_required": true,
  "confidence": 0.84,
  "reason": "该成本分析表包含导体阶段和胶料/后续制程阶段；部分分组可明确匹配，部分仍需人工确认。",
  "quotation_code": "FLRYBCY-050-001",
  "instance_id": 32,
  "groups": [],
  "unmatched_material_ids": [],
  "unmatched_process_ids": [],
  "unmatched_details": [],
  "warnings": [],
  "meta": {
    "candidate_skills": [
      "conductor_material_and_process",
      "glue_external_and_process",
      "price_summary"
    ],
    "rule_hits": [
      "same_process_order_match",
      "external_copper_skip_internal_process",
      "p_suffix_pairing"
    ]
  }
}
```

## 四、顶层字段定义

### 1. `route_type`

固定值：

- `route_plan`

表示当前结果是“多组编排结果”，不是“单一 skill 推荐”。

### 2. `summary_status`

建议枚举：

- `full_match`
- `partial_match`
- `manual_review_only`
- `reject`

定义如下：

- `full_match`
  - 已形成完整可读的分组结构，关键材料与制程都已归组。
- `partial_match`
  - 已形成部分可靠分组，但仍存在未匹配项或低置信度项。
- `manual_review_only`
  - 只能给出说明，无法形成可靠分组。
- `reject`
  - 输入明显不足、非路由问题、或结果不应被采用。

### 3. `manual_review_required`

布尔值。

建议规则：

- 只要存在任何歧义组、未匹配组、价格缺失问题、或置信度低于阈值，均返回 `true`。

### 4. `confidence`

整张成本分析表的总体可信度。

说明：

- 表示“整张表的 route_plan 质量”，不是单个分组的可信度。
- 前端展示时应保留两位小数。

### 5. `reason`

总体原因说明，要求面向业务人员可读。

要求：

- 先描述整体结论，再描述风险。
- 不要堆叠模型推理细节。
- 如果属于价格问题，必须明确写“价格缺失”，不要写成“制程不匹配”。

### 6. `quotation_code`

当前成本分析号。

### 7. `instance_id`

当前 BPM 实例 ID。

### 8. `groups`

分组结果数组，是本 schema 的核心字段。

每个 group 表示：

- 一组材料行
- 一组制程行
- 一个建议 skill
- 一个建议执行顺序

### 9. `unmatched_material_ids`

未可靠归组的材料行 ID 列表。

### 10. `unmatched_process_ids`

未可靠归组的制程行 ID 列表。

### 11. `unmatched_details`

对未匹配项进行结构化说明，便于前端直接展示。

### 12. `warnings`

风险提示数组。

建议示例：

- `价格缺失不是路由问题`
- `存在多阶段计算需求`
- `存在外购铜绞，需跳过内部铜绞制程`
- `对绞组未能唯一确定 P 结尾规格`

### 13. `meta`

调试与追溯信息，只在前端“查看原始 JSON”或开发排查时使用。

## 五、分组结构

### 1. 单组示例

```json
{
  "group_id": "grp_1",
  "step_order": 1,
  "group_type": "conductor_stage",
  "target_skill": "conductor_material_and_process",
  "match_status": "matched",
  "manual_review_required": false,
  "confidence": 0.93,
  "material_ids": [961],
  "process_ids": [1017],
  "material_names": ["铜绞"],
  "process_names": ["铜绞"],
  "reason": "铜绞材料行与铜绞制程行名称及顺序一致，可交由导体/编织材料及制程费用 skill 处理。",
  "rule_hits": [
    "same_process_order_match"
  ]
}
```

### 2. 分组字段定义

#### `group_id`

当前分组唯一标识，用于前端展示与后续人工确认。

#### `step_order`

建议执行顺序。

推荐顺序：

1. `conductor_material_and_process`
2. `glue_external_and_process`
3. `price_summary`

说明：

- 这里表达的是“建议执行顺序”，不是业务主从关系。

#### `group_type`

建议枚举：

- `conductor_stage`
- `glue_stage`
- `price_summary_stage`
- `mixed_stage`
- `unknown_stage`

#### `target_skill`

当前分组建议交由哪个 skill 处理。

当前建议值：

- `conductor_material_and_process`
- `glue_external_and_process`
- `price_summary`

#### `match_status`

建议枚举：

- `matched`
- `partially_matched`
- `ambiguous`
- `unmatched`

定义如下：

- `matched`
  - 材料与制程可明确归组。
- `partially_matched`
  - 有明显倾向，但仍有部分待确认。
- `ambiguous`
  - 存在多个候选归组，无法唯一确定。
- `unmatched`
  - 当前分组无法成立。

#### `manual_review_required`

当前组是否必须人工确认。

#### `confidence`

当前组自身的置信度。

#### `material_ids`

当前组包含的材料行 ID 列表。

#### `process_ids`

当前组包含的制程行 ID 列表。

#### `material_names`

当前组材料行名称列表，供前端展示。

#### `process_names`

当前组制程行名称列表，供前端展示。

#### `reason`

当前组的解释说明。

要求：

- 用业务语言解释为什么由该 skill 处理。
- 如果只是“当前已识别阶段”，要明确写清，不要写成“其他阶段不需要”。

#### `rule_hits`

命中的业务规则列表。

当前建议规则名：

- `same_process_order_match`
- `external_copper_skip_internal_process`
- `p_suffix_pairing`
- `price_missing_not_routing_issue`
- `multi_stage_possible`

## 六、未匹配项结构

### 1. 单项示例

```json
{
  "item_type": "material",
  "item_id": 970,
  "item_name": "芯押",
  "status": "unmatched",
  "suggested_skill": "glue_external_and_process",
  "manual_review_required": true,
  "reason": "存在多个芯押制程候选，当前无法唯一确定对应关系。"
}
```

### 2. 字段定义

- `item_type`
  - `material` 或 `process`
- `item_id`
  - 材料行或制程行 ID
- `item_name`
  - 材料名或制程名
- `status`
  - 当前建议固定为 `unmatched`
- `suggested_skill`
  - 若能判断其大致归属，给出候选 skill
- `manual_review_required`
  - 当前建议固定为 `true`
- `reason`
  - 为什么未匹配成功

## 七、业务规则落点

以下规则应体现在 `groups / unmatched_details / reason / rule_hits` 中，而不是只藏在长文本解释里：

- 同类制程按顺序对位
- 外购铜绞不参与下方铜绞制程
- 对绞相关优先取 P 结尾规格的铜绞和芯押
- 价格缺失不是路由问题
- 一张成本分析表可能同时需要多个 skill 阶段

## 八、边界说明

本 schema 当前不负责：

- 自动调用确定性 skill
- 自动落正式金额结果
- 生成人工确认页面状态
- 替代当前正式 `full-price` 计算链

本 schema 当前只负责：

- 结构化表达“每个上下对应以及不对应的制程 skill 对应关系”
- 让前端能清晰展示分组与未匹配项
- 为下一阶段人工确认或 route_plan 执行设计打基础

## 九、推荐结论

当前业务更适合 `route_plan`，而不是单一 `target_skill`。

一句话总结：

> `route_plan` 的核心价值，不是让 LLM 选一个最主要的 skill，而是让 LLM 把整张成本分析表拆成“每组由哪个 skill 处理、哪些行已匹配、哪些行未匹配”的结构化编排结果。
