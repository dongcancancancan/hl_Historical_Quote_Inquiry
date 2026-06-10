from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class QuotationRoutingPolicy(Base):
    __tablename__ = "quotation_routing_policy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True, comment="租户ID")
    policy_name = Column(String(200), nullable=False, comment="策略名称")
    status = Column(String(20), nullable=False, default="draft", index=True, comment="状态 draft/published/disabled")
    enabled = Column(Boolean, nullable=False, default=False, index=True, comment="是否启用")
    prompt_rules = Column(Text, nullable=False, comment="路由提示词规则JSON")
    confidence_threshold = Column(Numeric(18, 4), comment="自动采用阈值")
    version_no = Column(Integer, nullable=False, default=1, comment="当前版本号")
    llm_model = Column(String(100), comment="路由使用的模型")
    route_scope = Column(String(100), index=True, comment="路由作用域")
    remark = Column(String(500), comment="备注")
    creator = Column(String(64), comment="创建人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    updater = Column(String(64), comment="更新人")
    update_time = Column(DateTime, nullable=False, server_default=func.now(), comment="更新时间")
    deleted = Column(Boolean, nullable=False, default=False, index=True, comment="软删除标记")


class QuotationRoutingDecisionRun(Base):
    __tablename__ = "quotation_routing_decision_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, index=True, comment="关联成本分析表ID")
    bpm_instance_id = Column(Integer, ForeignKey("quotation_bpm_instance.id"), index=True, comment="关联BPM实例ID")
    calculation_run_id = Column(Integer, ForeignKey("quotation_calculation_run.id"), index=True, comment="关联计算运行ID")
    quotation_code = Column(String(100), nullable=False, index=True, comment="成本分析号")
    bpm_no = Column(String(100), index=True, comment="BPM流程号")
    tenant_id = Column(String(50), index=True, comment="租户ID")
    policy_id = Column(Integer, ForeignKey("quotation_routing_policy.id"), index=True, comment="采用的路由策略ID")
    route_scene = Column(String(50), nullable=False, index=True, comment="路由场景")
    trigger_source = Column(String(50), comment="触发来源")
    input_snapshot = Column(Text, nullable=False, comment="路由输入快照JSON")
    candidate_skills = Column(Text, comment="候选skill列表JSON")
    llm_model = Column(String(100), comment="本次调用模型")
    llm_prompt_text = Column(Text, comment="本次发送prompt")
    llm_response_text = Column(Text, comment="LLM原始响应")
    decision_json = Column(Text, comment="结构化路由结果JSON")
    confidence = Column(Numeric(18, 4), comment="本次决策置信度")
    final_action = Column(String(50), nullable=False, comment="最终动作")
    final_skill = Column(String(100), index=True, comment="最终skill")
    adopt_status = Column(String(20), nullable=False, default="pending", index=True, comment="采用状态 pending/adopted/rejected")
    error_message = Column(String(1000), comment="错误信息")
    operator = Column(String(64), nullable=False, comment="操作人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), index=True, comment="创建时间")
