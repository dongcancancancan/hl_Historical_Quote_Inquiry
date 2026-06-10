# Routing Eval Round2 Compare

## 样本范围

- Round2 样本数: 10
- 仅比较 `routing_issue` 样本，不纳入 `not_routing_issue`。

## 指标对比

- manual_review 数量: Round1=10 -> Round2=10
- route_skill 数量: Round1=0 -> Round2=0
- avg_confidence: Round1=0.00 -> Round2=0.00
- target_skill 分布: Round2={'manual_review': 10}
- reason 提到多阶段计算: Round1=0/10 -> Round2=7/10
- reason 提到映射规则(P结尾/配对/顺序/外购): Round2=8/10
- 含价格风险信号样本数: 8/10
- reason 明确识别价格缺失不是路由问题: Round2=0/8
- matched_material_ids / matched_process_ids 接近人工判断: Round1=0/10 -> Round2=0/10（当前两轮都未输出结构化匹配行，无法显示改善）

## 结论

- Round2 没有降低 manual_review，说明当前 schema 下模型仍无法把这些复杂样本收敛成单一 target_skill。
- Round2 的 reason 明显更接近业务标注，已经开始表达可能需要多阶段计算。
- Round2 已吸收对绞/P结尾/配对关系等业务规则，reason 不再只停留在复杂制程不明确的泛化描述。
- 对含价格风险信号的样本，Round2 仍未在 reason 中明确指出价格问题；这说明当前 Round2 样本虽然有价格风险，但模型仍优先把它理解成映射问题。
- 当前最值得继续验证的不是自动采用，而是是否需要把单一 target_skill升级成多阶段 route_plan；但这仍是观察项，不是当前开发项。

## 每条样本对比

| instance_id | Round1 | Round2 | 多阶段 | 映射规则 | 价格识别 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 20 | manual_review | manual_review | 是 | 是 | 否 | 21451011-1-韩国LG-釜山-卷装 |
| 21 | manual_review | manual_review | 是 | 是 | 否 | 21451013-韩国LG-釜山-卷装 |
| 22 | manual_review | manual_review | 是 | 是 | 否 | 21451014-韩国LG-釜山-卷装 |
| 23 | manual_review | manual_review | 是 | 是 | 否 | 21451016-韩国LG-釜山-卷装 |
| 24 | manual_review | manual_review | 否 | 是 | 否 | 21451015-韩国LG-釜山-卷装 |
| 25 | manual_review | manual_review | 是 | 是 | 否 | 21451017-韩国LG-釜山-卷装 |
| 26 | manual_review | manual_review | 是 | 是 | 否 | 2725016-韩国LG-釜山-卷装 |
| 27 | manual_review | manual_review | 否 | 否 | 否 | 2725015-韩国LG-釜山-卷装 |
| 28 | manual_review | manual_review | 否 | 是 | 否 | 2725017-韩国LG-釜山-卷装 |
| 29 | manual_review | manual_review | 是 | 否 | 否 | 2725018-韩国LG-釜山-卷装 |