import sys
from app.services.etl_service import extract_with_llm

header_text = """
编号:	FHLR2GCB2G-50-003
客户：	6010634 800木轴 500 米		分析日期：	2026.4.22
结构：	1596/0.20BC			品名规格：	FHLR2GCB2G 50mm2 编织85% -40~180℃ 600V AC/1000V DC
"""

material_text = """
1	导体绞合	1596/0.196BC*1C			43.525	91.9991	4004.28
2	绝缘	车内高压线硅胶 D1772700J 11.9mm			6.010	28.35	170.40
4	编织	24/8/0.20TC			6.801	96.8991	658.97
4	绕包	AL-MYLAR 0.035*30			0.364	23.5	8.56
5	护套	车内高压线硅胶 D1772700H 15.5mm			7.974	28.35	226.08
"""

res = extract_with_llm(header_text, material_text)
import json
print(json.dumps(res, indent=2, ensure_ascii=False))
