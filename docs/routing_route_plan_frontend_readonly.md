# Routing Route Plan Frontend Readonly Spec

## 一、目标

本文件定义 `route_plan` 第一版前端展示方案。

约束如下：

- 只读展示，不支持编辑。
- 不做人工拖拽确认。
- 不自动采用结果。
- 不接正式计算。
- 仅替换当前 `Skill 路由测试` 弹窗中的结果展示部分。

## 二、展示目标

前端要解决的核心问题不是“把原始 JSON 打出来”，而是让业务一眼看懂：

- 这张成本分析表拆成了几组
- 每组建议交给哪个 skill
- 哪些材料/制程已经对应上
- 哪些没对应上
- 是否必须人工复核

## 三、弹窗结构

推荐结构如下：

1. 总体结论区
2. 分组结果区
3. 未匹配项区
4. 风险提示区
5. 原始 JSON 区

## 四、总体结论区

### 1. 展示内容

显示 3 类关键信息：

- 总体状态
- 总体置信度
- 是否需要人工复核

### 2. 建议文案

根据 `summary_status` 显示：

- `full_match`
  - `已识别完整 route_plan，可按阶段理解本次路由结果`
- `partial_match`
  - `已识别部分 route_plan，但仍有未匹配项，建议人工复核`
- `manual_review_only`
  - `当前无法形成可靠分组，仅可作为人工复核辅助`
- `reject`
  - `当前结果不可用，不建议采用`

### 3. 建议字段

- `总体状态`
- `总体置信度`
- `是否需人工复核`
- `总体说明`

## 五、分组结果区

### 1. 展示形式

建议使用卡片列表，每组一张卡片。

每张卡片对应一个 `group`。

### 2. 每张卡片字段

- `阶段顺序`
- `阶段类型`
- `建议 skill`
- `匹配状态`
- `组内置信度`
- `材料行`
- `制程行`
- `说明`
- `命中规则`

### 3. 字段映射

- `阶段顺序` <- `step_order`
- `阶段类型` <- `group_type`
- `建议 skill` <- `target_skill`
- `匹配状态` <- `match_status`
- `组内置信度` <- `confidence`
- `材料行` <- `material_ids + material_names`
- `制程行` <- `process_ids + process_names`
- `说明` <- `reason`
- `命中规则` <- `rule_hits`

### 4. 文案要求

不要再出现“主推荐 skill”这种文案。

统一改成：

- `当前已识别阶段`
- `建议由该 skill 处理`
- `该组材料与制程已明确对应`
- `该组仍需人工确认`

### 5. skill 中文映射

前端建议固定转换：

- `conductor_material_and_process`
  - `导体/编织材料及制程费用`
- `glue_external_and_process`
  - `胶料/外购材料及后续制程费用`
- `price_summary`
  - `最终售价汇总`

## 六、未匹配项区

### 1. 展示目标

业务最关心的是：

- 哪条材料没匹配上
- 哪条制程没匹配上
- 它大概属于哪个 skill
- 为什么没匹配上

### 2. 展示形式

建议拆成两个子区块：

- 未匹配材料
- 未匹配制程

### 3. 每项字段

- `行 ID`
- `名称`
- `建议 skill`
- `是否需人工复核`
- `原因`

### 4. 数据来源

来自：

- `unmatched_material_ids`
- `unmatched_process_ids`
- `unmatched_details`

前端优先使用 `unmatched_details`，不要只显示孤零零的 ID。

## 七、风险提示区

### 1. 展示目标

显示当前样本的重要风险，不要藏在长段 reason 里。

### 2. 数据来源

来自顶层 `warnings`。

### 3. 推荐展示样式

使用 warning tag 或 alert 列表展示。

示例：

- `价格缺失不是路由问题`
- `存在多阶段计算需求`
- `存在外购铜绞，需跳过内部铜绞制程`
- `对绞组未能唯一确定 P 结尾规格`

## 八、原始 JSON 区

### 1. 第一版建议

默认折叠。

只在业务或开发需要核对时展开。

### 2. 作用

- 调试
- 复制结果
- 比对后端返回字段

### 3. 交互

保留 2 个最小操作：

- `查看原始 JSON`
- `复制 JSON`

## 九、前端最小字段清单

第一版前端只读展示必须支持以下字段：

### 顶层

- `route_type`
- `summary_status`
- `manual_review_required`
- `confidence`
- `reason`
- `quotation_code`
- `instance_id`
- `warnings`

### 分组

- `group_id`
- `step_order`
- `group_type`
- `target_skill`
- `match_status`
- `manual_review_required`
- `confidence`
- `material_ids`
- `process_ids`
- `material_names`
- `process_names`
- `reason`
- `rule_hits`

### 未匹配

- `unmatched_material_ids`
- `unmatched_process_ids`
- `unmatched_details`

## 十、状态与颜色建议

### 顶层状态

- `full_match`
  - 绿色
- `partial_match`
  - 橙色
- `manual_review_only`
  - 黄色
- `reject`
  - 红色

### 组状态

- `matched`
  - 绿色
- `partially_matched`
  - 橙色
- `ambiguous`
  - 黄色
- `unmatched`
  - 红色

## 十一、第一版不做的内容

明确不做：

- 不支持人工编辑 group
- 不支持拖拽材料或制程
- 不支持手工改 skill
- 不支持确认后触发正式计算
- 不支持保存 route_plan 到新的确认表

## 十二、推荐交互流程

用户点击：

- `Skill 路由测试`

后续流程：

1. 提交当前单据和可选错误上下文
2. 后端返回 `route_plan`
3. 弹窗展示总体结论
4. 弹窗展示分组与未匹配项
5. 用户只读查看，不做修改

## 十三、推荐结论

第一版前端的目标，不是“让业务操作 route_plan”，而是“让业务看懂 route_plan”。

一句话总结：

> 前端第一版应把 `route_plan` 结果翻译成“几组、每组哪个 skill、哪些已匹配、哪些未匹配、哪里需要人工复核”的只读视图，而不是继续显示一段很长的推理说明。
