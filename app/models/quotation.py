from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class QuotationMain(Base):
    """报价单主表（含费用汇总字段，quotation_cost_summary 已弃用）"""
    __tablename__ = "quotation_main"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), index=True, comment="租户ID")
    quotation_code = Column(String(100), index=True, comment="报价单编号 (如: FHLR2GCB2G-50-003)")
    customer_name = Column(String(200), comment="客户名称 (如: 800木轴 500 米)")
    customer_code = Column(String(100), comment="客户代号 (如: 6010634)")
    customer_address = Column(String(500), comment="客户地址")
    analysis_date = Column(Date, index=True, comment="分析日期")
    structure = Column(String(200), comment="结构 (如: 2368/0.20BC)")
    product_spec = Column(String(500), index=True, comment="品名规格")
    quotation_name = Column(String(100), comment="报价单名称")
    original_file_path = Column(String(500), comment="原始Excel文件存储路径")
    extracted_tags = Column(String, comment="提取的业务标签JSON (料号、线径等)")
    remark = Column(String(500), comment="备注")

    # 材料成本汇总
    copper_price = Column(Numeric(18, 4), comment="铜价")
    unit_usage_sum = Column(Numeric(18, 4), comment="单位用量合计")
    material_amount_sum = Column(Numeric(18, 4), comment="材料金额合计")
    material_cost = Column(Numeric(18, 4), comment="材料成本")

    # 杂费
    ul_label_fee = Column(Numeric(18, 4), comment="UL标签费 (RMB/M)")
    transport_fee = Column(Numeric(18, 4), comment="运输费 (RMB/KG)")
    packing_fee = Column(Numeric(18, 4), comment="包装费 (RMB/M)")
    waste_loss_rate = Column(Numeric(18, 4), comment="废品损耗率")
    order_startup_times = Column(Numeric(10, 0), comment="订单开机次数")
    total_fee = Column(Numeric(18, 4), comment="费用总计")
    other_fee = Column(Numeric(18, 4), comment="其他费用")
    delivery_fee = Column(Numeric(18, 4), comment="送货费")
    irradiation_core_count = Column(Numeric(10, 4), comment="辐照芯数")
    braiding_rate = Column(Numeric(18, 4), comment="编织率")

    # 利润与税率
    net_profit_rate = Column(Numeric(18, 4), comment="净利率")
    customs_fee = Column(Numeric(18, 4), comment="报关费 (RMB/次)")
    vat_rate = Column(Numeric(18, 4), comment="增值税率")
    order_meterage = Column(Numeric(18, 4), comment="订单米数")
    operating_expense_rate = Column(Numeric(18, 4), comment="营业费用率")
    monthly_interest = Column(Numeric(18, 4), comment="月结利息率")
    corporate_tax_rate = Column(Numeric(18, 4), comment="企业所得税税率")

    # 最终售价
    cost = Column(Numeric(18, 4), comment="成本 (RMB/M)")
    profit_selling_price = Column(Numeric(18, 4), comment="取利售价 (RMB/M)")
    non_profit_price = Column(Numeric(18, 4), comment="不取利售价 (RMB/M)")
    final_selling_price = Column(Numeric(18, 4), comment="最终售价 (RMB/M)")

    # 审计字段
    creator = Column(String(64), comment="创建人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    updater = Column(String(64), comment="更新人")
    update_time = Column(DateTime, nullable=False, server_default=func.now(), comment="更新时间")
    deleted = Column(Boolean, nullable=False, default=False, comment="软删除标记")

    # 关联子表
    materials = relationship("QuotationMaterial", back_populates="main", cascade="all, delete-orphan")
    processes = relationship("QuotationProcessFee", back_populates="main", cascade="all, delete-orphan")


class QuotationMaterial(Base):
    """材料成本明细表：对应原表上半部分的 '材料金额' 区域"""
    __tablename__ = "quotation_material"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), index=True, comment="租户ID")
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), index=True, comment="关联报价单主表ID")

    seq_no = Column(Integer, comment="序号")
    process_name = Column(String(100), comment="制程名称 (如: 导体绞合, 绝缘, 编织)")
    spec_detail = Column(String(200), comment="详细规格 (如: 2368/0.194BC*1C)")
    unit_usage = Column(Numeric(18, 4), comment="单位用量 (KG/100M)")
    unit_price = Column(Numeric(18, 4), comment="单价 (RMB/KG)")
    material_amount = Column(Numeric(18, 4), comment="材料金额 (RMB/100M)")
    process_code = Column(String(100), comment="制程代码")
    remark = Column(String(64), comment="备注")

    creator = Column(String(64), comment="创建人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    updater = Column(String(64), comment="更新人")
    update_time = Column(DateTime, nullable=False, server_default=func.now(), comment="更新时间")
    deleted = Column(Boolean, nullable=False, default=False, comment="软删除标记")

    main = relationship("QuotationMain", back_populates="materials")


class QuotationProcessFee(Base):
    """制程费用明细表：对应原表下半部分的 '费用成本小计' 区域"""
    __tablename__ = "quotation_process_fee"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), index=True, comment="租户ID")
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), index=True, comment="关联报价单主表ID")

    process_name = Column(String(100), comment="制程名称 (如: 导体绞合)")
    std_hours = Column(Numeric(18, 4), comment="标准工时 (一台机1KM开机时间)")
    loss_hours = Column(Numeric(18, 4), comment="损耗时间 (1KM)")
    fixed_rate = Column(Numeric(18, 4), comment="固定费用率")
    fixed_fee = Column(Numeric(18, 4), comment="固定费用")
    startup_loss_wire = Column(Numeric(18, 4), comment="开机损耗废线 (KG)")
    total_waste_glue = Column(Numeric(18, 4), comment="每个制程总废胶 (KG)")
    amount = Column(Numeric(18, 4), comment="费用金额")
    subtotal_fee = Column(Numeric(18, 4), comment="费用成本小计")
    process_code = Column(String(100), comment="制程代码")
    remark = Column(String(64), comment="备注")

    creator = Column(String(64), comment="创建人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    updater = Column(String(64), comment="更新人")
    update_time = Column(DateTime, nullable=False, server_default=func.now(), comment="更新时间")
    deleted = Column(Boolean, nullable=False, default=False, comment="软删除标记")

    main = relationship("QuotationMain", back_populates="processes")


class QuotationCostSummary(Base):
    """[已弃用] 全局费用汇总表 — 费用字段已合并到 quotation_main，此表不再写入"""
    __tablename__ = "quotation_cost_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_id = Column(Integer, ForeignKey("quotation_main.id"), index=True, comment="关联报价单主表ID")

    total_material_cost_rmb_m = Column(Numeric(18, 8), comment="材料成本 (RMB/M)")
    total_material_cost_kg = Column(Numeric(18, 8), comment="材料成本 (KG)")
    total_material_amount = Column(Numeric(18, 8), comment="材料成本总金额")
    total_process_cost = Column(Numeric(18, 8), comment="费用总计")
    ul_label_fee = Column(Numeric(18, 8), comment="UL标签费 (RMB/M)")
    transport_fee = Column(Numeric(18, 8), comment="运输费 (RMB/KG)")
    package_fee = Column(Numeric(18, 8), comment="包装费 (RMB/M)")
    scrap_rate = Column(Numeric(18, 8), comment="废品损耗率")
    startup_times = Column(Integer, comment="订单开机次数")
    delivery_fee = Column(Numeric(18, 8), comment="送货费")
    customs_fee = Column(Numeric(18, 8), comment="报关费 (RMB/次)")
    order_meters = Column(Numeric(10, 8), comment="订单米数")
    net_profit_rate = Column(Numeric(18, 8), comment="净利率")
    vat_rate = Column(Numeric(18, 8), comment="增值税率")
    business_fee_rate = Column(Numeric(18, 8), comment="营业费用率")
    monthly_interest_rate = Column(Numeric(18, 8), comment="月结利息率")
    corp_tax_rate = Column(Numeric(18, 8), comment="企业所得税税率")
    cost_rmb_m = Column(Numeric(18, 8), comment="成本 (RMB/M)")
    price_with_profit = Column(Numeric(18, 8), comment="取利售价 (RMB/M)")
    price_without_profit = Column(Numeric(18, 8), comment="不取利售价 (RMB/M)")
    final_price = Column(Numeric(18, 8), comment="最终售价 (RMB/M)")
