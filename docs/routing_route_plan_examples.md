# Routing Route Plan Examples

## 一、目的

本文件提供 `route_plan` 的 3 套标准返回示例，用于：

- 对齐后端接口结构
- 对齐前端只读展示逻辑
- 对齐业务对“完全匹配 / 部分匹配 / 全人工复核”的理解

本文件只定义返回示例，不实现接口代码。

---

## 二、示例一：完全匹配

适用场景：

- 铜绞和外被两段都存在明确的材料/制程对应关系
- 不存在未匹配项
- 当前仅用于“结构化路由测试展示”，不自动计算

```json
{
  "route_type": "route_plan",
  "summary_status": "full_match",
  "manual_review_required": false,
  "confidence": 0.91,
  "reason": "该成本分析表可拆分为导体阶段、胶料/后续制程阶段和最终售价汇总阶段，前两组存在明确材料与制程对应关系。",
  "quotation_code": "FLRYBCY-050-001",
  "instance_id": 32,
  "groups": [
    {
      "group_id": "grp_1",
      "step_order": 1,
      "group_type": "conductor_stage",
      "target_skill": "conductor_material_and_process",
      "match_status": "matched",
      "manual_review_required": false,
      "confidence": 0.95,
      "material_ids": [961],
      "process_ids": [1017],
      "material_names": ["铜绞"],
      "process_names": ["铜绞"],
      "reason": "铜绞材料与铜绞制程明确对应，应先计算导体/编织阶段。",
      "rule_hits": ["same_process_order_match"]
    },
    {
      "group_id": "grp_2",
      "step_order": 2,
      "group_type": "glue_stage",
      "target_skill": "glue_external_and_process",
      "match_status": "matched",
      "manual_review_required": false,
      "confidence": 0.94,
      "material_ids": [962],
      "process_ids": [1018],
      "material_names": ["外被"],
      "process_names": ["外被"],
      "reason": "外被材料与外被制程明确对应，应在导体阶段后进入胶料/后续制程阶段。",
      "rule_hits": ["same_process_order_match", "multi_stage_possible"]
    },
    {
      "group_id": "grp_3",
      "step_order": 3,
      "group_type": "price_summary_stage",
      "target_skill": "price_summary",
      "match_status": "matched",
      "manual_review_required": false,
      "confidence": 0.9,
      "material_ids": [],
      "process_ids": [],
      "material_names": [],
      "process_names": [],
      "reason": "导体阶段和胶料阶段完成后，可进入最终售价汇总。",
      "rule_hits": ["multi_stage_possible"]
    }
  ],
  "unmatched_material_ids": [],
  "unmatched_process_ids": [],
  "unmatched_details": [],
  "warnings": [
    "当前结果仅用于 route_plan 展示，不会自动触发正式计算"
  ],
  "meta": {
    "candidate_skills": [
      "conductor_material_and_process",
      "glue_external_and_process",
      "price_summary"
    ],
    "rule_hits": [
      "same_process_order_match",
      "multi_stage_possible"
    ]
  }
}
```

前端应展示为：

- 已识别完整 route_plan
- 共 3 个阶段
- 无未匹配材料/制程
- 可读结论：先导体，再胶料/外被，最后售价汇总

---

## 三、示例二：部分匹配

适用场景：

- 多铜绞、多芯押存在明显的阶段归属
- 但对绞、集合等后续制程无法完全唯一配对
- 当前可以给出部分 group，但仍需人工复核

```json
{
  "route_type": "route_plan",
  "summary_status": "partial_match",
  "manual_review_required": true,
  "confidence": 0.78,
  "reason": "该成本分析表已识别出导体阶段和胶料阶段的主要分组，但对绞与集合相关制程仍存在未完成配对，建议人工复核。",
  "quotation_code": "FLR9YBCY-050-001",
  "instance_id": 33,
  "groups": [
    {
      "group_id": "grp_1",
      "step_order": 1,
      "group_type": "conductor_stage",
      "target_skill": "conductor_material_and_process",
      "match_status": "matched",
      "manual_review_required": false,
      "confidence": 0.92,
      "material_ids": [961, 963],
      "process_ids": [1017, 1019],
      "material_names": ["铜绞A", "铜绞B"],
      "process_names": ["铜绞A", "铜绞B"],
      "reason": "多个铜绞材料与多个铜绞制程可按顺序一一对应，应先处理导体阶段。",
      "rule_hits": ["same_process_order_match"]
    },
    {
      "group_id": "grp_2",
      "step_order": 2,
      "group_type": "glue_stage",
      "target_skill": "glue_external_and_process",
      "match_status": "partially_matched",
      "manual_review_required": true,
      "confidence": 0.7,
      "material_ids": [964, 965],
      "process_ids": [1020],
      "material_names": ["芯押A", "芯押B"],
      "process_names": ["芯押"],
      "reason": "芯押组存在明显胶料阶段特征，但当前只能确定部分对应关系；对绞和集合仍需依赖人工确认。",
      "rule_hits": ["same_process_order_match", "p_suffix_pairing", "multi_stage_possible"]
    }
  ],
  "unmatched_material_ids": [966],
  "unmatched_process_ids": [1021, 1022],
  "unmatched_details": [
    {
      "item_type": "material",
      "item_id": 966,
      "item_name": "芯押P",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "该材料疑似参与对绞阶段，但当前无法唯一确定其对应的下游制程。"
    },
    {
      "item_type": "process",
      "item_id": 1021,
      "item_name": "对绞",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "对绞需优先匹配 P 结尾规格的铜绞和芯押，但当前配对关系不唯一。"
    },
    {
      "item_type": "process",
      "item_id": 1022,
      "item_name": "集合",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "集合依赖上游多芯押/多铜绞分组完成后才能确定。"
    }
  ],
  "warnings": [
    "存在多阶段计算需求",
    "对绞组未能唯一确定 P 结尾规格",
    "当前结果仅适合人工复核辅助"
  ],
  "meta": {
    "candidate_skills": [
      "conductor_material_and_process",
      "glue_external_and_process",
      "price_summary"
    ],
    "rule_hits": [
      "same_process_order_match",
      "p_suffix_pairing",
      "multi_stage_possible"
    ]
  }
}
```

前端应展示为：

- 已识别部分 route_plan
- 导体阶段明确
- 胶料阶段部分明确
- 对绞、集合未匹配，必须人工复核

---

## 四、示例三：全人工复核

适用场景：

- 价格缺失或关键上下文缺失
- 当前不应强行给出可靠编排结果
- 可以给出“为何不能路由”的结构化说明

```json
{
  "route_type": "route_plan",
  "summary_status": "manual_review_only",
  "manual_review_required": true,
  "confidence": 0.18,
  "reason": "当前成本分析表存在铜价为空和外购价格源缺失，属于价格问题，不应强行输出可执行 route_plan，建议先补全价格数据后再复核。",
  "quotation_code": "21451013",
  "instance_id": 21,
  "groups": [],
  "unmatched_material_ids": [970, 971],
  "unmatched_process_ids": [1030, 1031],
  "unmatched_details": [
    {
      "item_type": "material",
      "item_id": 970,
      "item_name": "PVC 外被",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "外购/PVC 价格源缺失，当前无法判断应进入后续制程计算还是先补价格。"
    },
    {
      "item_type": "material",
      "item_id": 971,
      "item_name": "铜绞",
      "status": "unmatched",
      "suggested_skill": "conductor_material_and_process",
      "manual_review_required": true,
      "reason": "铜价为空，当前无法形成可靠导体阶段判断。"
    },
    {
      "item_type": "process",
      "item_id": 1030,
      "item_name": "铜绞",
      "status": "unmatched",
      "suggested_skill": "conductor_material_and_process",
      "manual_review_required": true,
      "reason": "上游价格基础缺失，当前不应继续做导体阶段路由。"
    },
    {
      "item_type": "process",
      "item_id": 1031,
      "item_name": "外被",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "材料价格缺失优先于制程路由，应先补充价格数据。"
    }
  ],
  "warnings": [
    "价格缺失不是路由问题",
    "铜价为空",
    "外购/PVC 价格源缺失",
    "当前结果不可直接采用"
  ],
  "meta": {
    "candidate_skills": [
      "conductor_material_and_process",
      "glue_external_and_process",
      "price_summary"
    ],
    "rule_hits": [
      "price_missing_not_routing_issue"
    ]
  }
}
```

前端应展示为：

- 当前无法形成可靠 route_plan
- 根因是价格缺失
- 不要把该样本误解释成“制程不匹配”

---

## 五、接口建议

### 1. 路由测试接口

- `POST /quotation/calculate/route-test-plan`

返回：

- 单条 `route_plan`

### 2. 历史查询接口

- `GET /quotation/calculate/route-plans`

返回：

- 多条历史 `route_plan` 摘要

### 3. 单条详情接口

- `GET /quotation/calculate/route-plans/{run_id}`

返回：

- 完整 `route_plan`

## 六、推荐结论

这 3 套示例应覆盖前端第一版只读展示的主要状态：

- 完全匹配
- 部分匹配
- 全人工复核

一句话总结：

> 如果前端能把这 3 套状态清楚展示出来，业务就能真正看懂“每个上下对应以及不对应的制程 skill 对应关系”，而不是只看到一个模糊的 `target_skill`。
