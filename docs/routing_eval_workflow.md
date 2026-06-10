# 路由评估与 Prompt 调优流程

## 目标

- 当前阶段只验证 LLM 路由是否可靠。
- 不自动采用 LLM 结果。
- 不让 LLM 参与金额计算。
- 不修改 `full_price` 正式计算逻辑。

## 一、筛选真实样本

先导出候选样本清单：

```powershell
.\venv\Scripts\python.exe .\scripts\run_routing_eval.py discover --limit 50
```

导出的 CSV 重点看：

- `instance_id`
- `bpm_no`
- `quotation_code`
- `material_count`
- `process_count`
- `complexity_reason`

优先选择这些样本：

- `待审价`
- `未报价`
- `上下数量不一致`
- 包含 `集合 / 芯押 / 多铜绞 / 包带 / 倒线 / 编织 / 外被`
- 已出现过 `calculation_failure`

## 二、批量执行 Dry-Run

准备一个文本文件，例如 `tmp/instance_ids.txt`：

```text
33
17
27
32
20
```

执行批量路由评估：

```powershell
.\venv\Scripts\python.exe .\scripts\run_routing_eval.py run `
  --instance-file .\tmp\instance_ids.txt `
  --route-scene fallback_skill_route `
  --trigger-source manual_batch_eval `
  --error-message "复杂制程批量评估"
```

脚本会：

- 逐条调用 `route_calculation_skill`
- 写入 `quotation_routing_decision_run`
- 导出一份结果 CSV

## 三、业务标注建议

业务人员优先看这几个字段：

- `target_skill`
- `matched_material_ids`
- `matched_process_ids`
- `reason`
- `confidence`
- `manual_review_required`

建议标注三列：

- `is_correct`
- `expected_skill`
- `comment`

如果需要回写系统，可调用现有接口：

```text
PATCH /api/v1/etl/quotation/calculate/route-runs/{run_id}/review
```

## 四、Prompt 调优原则

第一轮样本跑完后，只允许调整：

```text
prompt_rules.business_rules
```

重点补规则：

- 集合如何取上游材料
- 多铜绞 / 多芯押如何判断
- 上下半部分不对应时如何匹配
- 哪些场景必须 `manual_review`
- 哪些制程优先复用 `glue_external_and_process`
- 哪些制程优先复用 `conductor_material_and_process`

## 五、复盘指标

每次调 prompt 后，复跑同一批样本，对比：

- `route_skill` 数量是否提升
- `manual_review` 是否合理下降
- 错误标注是否减少
- 高置信错误是否减少
- 低置信但人工判定正确的样本是否变少

## 六、当前边界

本阶段不要做以下事情：

- 不自动采用 LLM 路由结果
- 不让 LLM 计算金额
- 不修改 `full_price`
- 不新增表
- 不新增复杂前端
