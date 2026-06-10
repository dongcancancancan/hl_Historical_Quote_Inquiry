# Routing Eval Artifacts

## 一、说明

本文件用于说明当前路由评估阶段生成的文档和样本文件，区分哪些适合长期保留，哪些属于临时评估产物。

## 二、建议长期保留的文档

以下文件建议保留在仓库中，用于沉淀阶段性结论、方案设计和评估流程：

- `docs/routing_eval_stage_conclusion.md`
- `docs/routing_route_plan_design.md`
- `docs/routing_eval_workflow.md`
- `docs/routing_eval_round1_conclusion.md`
- `docs/routing_eval_round2_compare.md`

这些文件的价值在于：

- 记录当前阶段能力边界
- 记录 Round1 / Round2 的核心结论
- 为下一阶段是否进入 `route_plan` 设计提供背景材料

## 三、临时评估样本与导出文件

以下文件属于评估过程中的样本、导出结果、实例清单和中间对比结果：

- `tmp/routing_eval_*.csv`
- `tmp/routing_eval_*.txt`
- `tmp/routing_eval_*.md`

这些文件通常包括：

- 候选样本清单
- instance_id 列表
- Round1 / Round2 结果导出
- 人工标注模板
- 对比明细 CSV

## 四、保留建议

对 `tmp/` 下的评估产物，建议按下面方式处理：

- 如果只是本地评估和临时复盘使用，可以不提交到生产仓库。
- 如果需要保留业务样本证据、人工标注依据或阶段性复盘记录，可以选择性归档。
- 如果要长期留证，建议在后续建立专门的评估样本目录或外部样本仓，而不是持续把样本堆在 `tmp/` 下。

## 五、提交建议

建议默认提交到仓库的文件：

- `docs/routing_eval_stage_conclusion.md`
- `docs/routing_route_plan_design.md`
- `docs/routing_eval_workflow.md`
- `docs/routing_eval_round1_conclusion.md`
- `docs/routing_eval_round2_compare.md`

建议默认不提交或按需提交的文件：

- `tmp/routing_eval_*.csv`
- `tmp/routing_eval_*.txt`
- `tmp/routing_eval_*.md`

## 六、当前结论

当前阶段更适合把 `docs/` 下的文件当作正式沉淀，把 `tmp/` 下的文件当作评估证据和临时工作区。

一句话提醒：

> `tmp/` 不建议作为生产仓库中的长期样本存储位置，除非你明确希望把这一轮评估样本作为证据长期保留。
