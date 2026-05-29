-- 历史报价单智能查询系统 - 数据库设计
-- 生成日期: 2026-05-26

-- ====== 报价单主表：存储全局概览信息 ======

CREATE TABLE quotation_main (
	id SERIAL NOT NULL, 
	quotation_no VARCHAR, 
	customer VARCHAR, 
	analysis_date DATE, 
	structure VARCHAR, 
	product_spec VARCHAR, 
	original_file_path VARCHAR, 
	extracted_tags JSON, 
	PRIMARY KEY (id)
);
COMMENT ON COLUMN quotation_main.id IS '主键ID';
COMMENT ON COLUMN quotation_main.quotation_no IS '报价单编号 (如: FHLR2GCB2G-50-003)';
COMMENT ON COLUMN quotation_main.customer IS '客户名称/代号 (如: 6010634 800木轴)';
COMMENT ON COLUMN quotation_main.analysis_date IS '分析日期';
COMMENT ON COLUMN quotation_main.structure IS '结构 (如: 1596/0.20BC)';
COMMENT ON COLUMN quotation_main.product_spec IS '品名规格 (如: FHLR2GCB2G 50mm2 -40~180℃ 600V AC/1000V DC)';
COMMENT ON COLUMN quotation_main.original_file_path IS '原始Excel文件存储路径';
COMMENT ON COLUMN quotation_main.extracted_tags IS '提取的业务标签 (料号、线径等)，用于PG精确过滤';

-- ====== 材料成本明细表：对应原表上半部分的 '材料金额' 区域 ======

CREATE TABLE quotation_material (
	id SERIAL NOT NULL, 
	quotation_id INTEGER, 
	seq_no INTEGER, 
	process_name VARCHAR, 
	spec_detail VARCHAR, 
	unit_usage FLOAT, 
	unit_price FLOAT, 
	material_amount FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(quotation_id) REFERENCES quotation_main (id)
);
COMMENT ON COLUMN quotation_material.id IS '主键ID';
COMMENT ON COLUMN quotation_material.quotation_id IS '关联报价单主表ID';
COMMENT ON COLUMN quotation_material.seq_no IS '序号';
COMMENT ON COLUMN quotation_material.process_name IS '制程名称 (如: 导体绞合, 绝缘, 编织)';
COMMENT ON COLUMN quotation_material.spec_detail IS '详细规格 (如: 2368/0.194BC*1C)';
COMMENT ON COLUMN quotation_material.unit_usage IS '单位用量 (KG/100M)';
COMMENT ON COLUMN quotation_material.unit_price IS '单价 (RMB/KG)';
COMMENT ON COLUMN quotation_material.material_amount IS '材料金额 (RMB/100M)';

-- ====== 制程费用明细表：对应原表下半部分的 '费用成本小计' 区域 ======

CREATE TABLE quotation_process (
	id SERIAL NOT NULL, 
	quotation_id INTEGER, 
	process_name VARCHAR, 
	std_hours FLOAT, 
	loss_hours FLOAT, 
	fixed_rate FLOAT, 
	fixed_cost FLOAT, 
	startup_loss_wire FLOAT, 
	total_waste_glue FLOAT, 
	amount FLOAT, 
	subtotal_cost FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(quotation_id) REFERENCES quotation_main (id)
);
COMMENT ON COLUMN quotation_process.id IS '主键ID';
COMMENT ON COLUMN quotation_process.quotation_id IS '关联报价单主表ID';
COMMENT ON COLUMN quotation_process.process_name IS '制程名称 (如: 导体绞合)';
COMMENT ON COLUMN quotation_process.std_hours IS '标准工时 (一台机1KM开机时间)';
COMMENT ON COLUMN quotation_process.loss_hours IS '损耗时间 (1KM)';
COMMENT ON COLUMN quotation_process.fixed_rate IS '固定费用率';
COMMENT ON COLUMN quotation_process.fixed_cost IS '固定费用';
COMMENT ON COLUMN quotation_process.startup_loss_wire IS '开机损耗废线 (KG)';
COMMENT ON COLUMN quotation_process.total_waste_glue IS '每个制程总废胶 (KG)';
COMMENT ON COLUMN quotation_process.amount IS '费用金额';
COMMENT ON COLUMN quotation_process.subtotal_cost IS '费用成本小计';

-- ====== 全局费用汇总表：对应原表底部的所有杂费和最终报价 ======

CREATE TABLE quotation_cost_summary (
	id SERIAL NOT NULL, 
	quotation_id INTEGER, 
	total_material_cost_rmb_m FLOAT, 
	total_material_cost_kg FLOAT, 
	total_material_amount FLOAT, 
	total_process_cost FLOAT, 
	ul_label_fee FLOAT, 
	transport_fee FLOAT, 
	package_fee FLOAT, 
	scrap_rate FLOAT, 
	startup_times INTEGER, 
	delivery_fee FLOAT, 
	customs_fee FLOAT, 
	order_meters INTEGER, 
	net_profit_rate FLOAT, 
	vat_rate FLOAT, 
	business_fee_rate FLOAT, 
	monthly_interest_rate FLOAT, 
	corp_tax_rate FLOAT, 
	cost_rmb_m FLOAT, 
	price_with_profit FLOAT, 
	price_without_profit FLOAT, 
	final_price FLOAT, 
	PRIMARY KEY (id), 
	UNIQUE (quotation_id), 
	FOREIGN KEY(quotation_id) REFERENCES quotation_main (id)
);
COMMENT ON COLUMN quotation_cost_summary.id IS '主键ID';
COMMENT ON COLUMN quotation_cost_summary.quotation_id IS '关联报价单主表ID';
COMMENT ON COLUMN quotation_cost_summary.total_material_cost_rmb_m IS '材料成本 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.total_material_cost_kg IS '材料成本 (KG)';
COMMENT ON COLUMN quotation_cost_summary.total_material_amount IS '材料成本总金额';
COMMENT ON COLUMN quotation_cost_summary.total_process_cost IS '费用总计';
COMMENT ON COLUMN quotation_cost_summary.ul_label_fee IS 'UL标签费 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.transport_fee IS '运输费 (RMB/KG)';
COMMENT ON COLUMN quotation_cost_summary.package_fee IS '包装费 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.scrap_rate IS '废品损耗率';
COMMENT ON COLUMN quotation_cost_summary.startup_times IS '订单开机次数';
COMMENT ON COLUMN quotation_cost_summary.delivery_fee IS '送货费';
COMMENT ON COLUMN quotation_cost_summary.customs_fee IS '报关费 (RMB/次)';
COMMENT ON COLUMN quotation_cost_summary.order_meters IS '订单米数';
COMMENT ON COLUMN quotation_cost_summary.net_profit_rate IS '净利率';
COMMENT ON COLUMN quotation_cost_summary.vat_rate IS '增值税率';
COMMENT ON COLUMN quotation_cost_summary.business_fee_rate IS '营业费用率';
COMMENT ON COLUMN quotation_cost_summary.monthly_interest_rate IS '月结利息率';
COMMENT ON COLUMN quotation_cost_summary.corp_tax_rate IS '企业所得税税率';
COMMENT ON COLUMN quotation_cost_summary.cost_rmb_m IS '成本 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.price_with_profit IS '取利售价 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.price_without_profit IS '不取利售价 (RMB/M)';
COMMENT ON COLUMN quotation_cost_summary.final_price IS '最终售价 (RMB/M)';
