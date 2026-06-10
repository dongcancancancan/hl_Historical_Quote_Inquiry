# Routing Route Plan Design

## 一、设计目标

本文件只做下一阶段方案设计，不实现代码、不修改现有 schema、不新增表。

设计背景：

- Round2 已经证明，复杂成本分析样本往往不是“单一 skill 可处理”的问题。
- 这些样本更接近“多阶段计算”场景，例如：
  - 先处理导体/编织相关材料和制程
  - 再处理胶料/外购/芯押/外被/集合等后续制程
- 因此，如果后续继续推进，建议设计 `route_plan`，而不是继续让模型在单一 `target_skill` 中硬选一个结果。

## 二、设计原则

- `route_plan` 仍然只做路由决策，不计算金额。
- 每个 `step` 仍由确定性 skill 执行。
- 低置信度必须人工确认。
- 未人工确认前，不落正式计算结果。
- `route_plan` 是下一阶段候选设计，不是当前实现项。

## 三、未来可能的输出结构

未来如果要支持多阶段路由，schema 可以考虑类似下面的结构：

```json
{
  "route_type": "route_plan",
  "manual_review_required": true,
  "confidence": 0.82,
  "reason": "该成本分析表需要先计算导体/编织，再计算胶料/后续制程",
  "steps": [
    {
      "step_order": 1,
      "target_skill": "conductor_material_and_process",
      "matched_material_ids": [1, 2],
      "matched_process_ids": [10, 11],
      "reason": "导体/铜绞阶段"
    },
    {
      "step_order": 2,
      "target_skill": "glue_external_and_process",
      "matched_material_ids": [3, 4, 5],
      "matched_process_ids": [12, 13],
      "reason": "芯押/外被/集合阶段"
    }
  ]
}
```

## 四、字段含义建议

- `route_type`
  - 当前可为 `route_plan`
  - 明确表示结果不是“单一 skill 路由”，而是“多阶段路由建议”
- `manual_review_required`
  - 复杂样本默认建议保留为 `true`
  - 只有在后续业务确认机制成熟后，才考虑部分降级
- `confidence`
  - 表示整个 `route_plan` 的总体可信度
  - 不代表每个 step 都可直接自动执行
- `reason`
  - 用来解释为什么需要多阶段处理
  - 例如导体/编织和胶料/后续制程同时存在
- `steps`
  - 路由建议的阶段清单
- `step_order`
  - 阶段顺序
  - 用于明确执行顺序和业务依赖关系
- `target_skill`
  - 当前阶段推荐的确定性 skill
- `matched_material_ids`
  - 当前阶段参与计算的材料行
- `matched_process_ids`
  - 当前阶段参与计算的制程行
- `reason`
  - 当前阶段为什么由该 skill 处理

## 五、执行边界

即使未来引入 `route_plan`，也应坚持以下边界：

- LLM 不参与金额计算。
- LLM 不输出公式、单价、金额。
- 每个 step 仍由现有确定性 skill 执行。
- 低置信度或复杂配对关系必须人工确认。
- 在人工确认前，不允许写入正式计算结果。

## 六、适用场景

`route_plan` 更适合处理如下场景：

- 一张成本分析表同时包含导体/编织和胶料/后续制程两个阶段。
- 上下材料/制程数量不一致，但并非简单错误，而是天然多阶段结构。
- 存在对绞、集合、多铜绞、多芯押等复杂配对关系。
- 单一 `target_skill` 无法覆盖业务真实处理顺序。

## 七、不在本阶段实现的内容

本阶段明确不做以下事情：

- 不修改当前 `decision_json` schema。
- 不新增 `route_plan` 表。
- 不实现 `route_plan` 执行引擎。
- 不让 `route_plan` 直接驱动正式计算。
- 不把 `route_plan` 自动落入 `full-price` 链路。

## 八、建议的下一步

如果业务确认值得继续，可以进入下一阶段的“方案评审”，而不是直接开发。评审重点应包括：

- `route_plan` schema 是否满足业务表达。
- 是否需要人工确认页面。
- 每个 `step` 如何与现有 skill 编排衔接。
- 哪些场景允许自动建议，哪些场景必须人工确认。
- 如何记录 `route_plan` 与最终计算执行结果的关联关系。

一句话总结：

> `route_plan` 的价值不在于让 LLM 直接算金额，而在于让 LLM 把复杂成本分析表拆成“可由确定性 skill 逐段执行”的结构化建议。
