from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from app.models.quotation import QuotationMain
from app.services.calculation_context import CalculationContext
from app.services.conductor_calc_service import calculate_conductor_materials
from app.services.glue_calc_service import calculate_glue_materials
from app.services.price_summary_calc_service import calculate_price_summary


SkillHandler = Callable[[Session, QuotationMain, str, CalculationContext], dict]


@dataclass(frozen=True)
class CalculationSkill:
    id: str
    name: str
    phase: str
    order: int
    description: str
    capabilities: list[str] = field(default_factory=list)
    handler: SkillHandler | None = None

    def run(self, db: Session, quotation: QuotationMain, operator: str, ctx: CalculationContext) -> dict:
        if not self.handler:
            raise ValueError(f"计算 Skill {self.name} 未配置执行器")
        return self.handler(db, quotation, operator, ctx)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "phase": self.phase,
            "order": self.order,
            "description": self.description,
            "capabilities": self.capabilities,
        }


def _run_conductor_skill(db: Session, quotation: QuotationMain, operator: str, ctx: CalculationContext) -> dict:
    return calculate_conductor_materials(db, quotation, operator, ctx=ctx, commit=False)


def _run_glue_skill(db: Session, quotation: QuotationMain, operator: str, ctx: CalculationContext) -> dict:
    return calculate_glue_materials(db, quotation, operator, ctx=ctx, commit=False)


def _run_price_summary_skill(db: Session, quotation: QuotationMain, operator: str, ctx: CalculationContext) -> dict:
    return calculate_price_summary(db, quotation, operator, ctx=ctx, commit=False)


BUILTIN_CALCULATION_SKILLS = [
    CalculationSkill(
        id="conductor_material_and_process",
        name="导体/编织材料及制程费用",
        phase="material_process",
        order=10,
        description="计算导体、铜绞、导体绞合、编织类材料单价、材料金额和对应制程费用。",
        capabilities=[
            "铜价公式",
            "BC/TC 线径解析",
            "铜加工费最近线径复用",
            "导体/铜绞一对多制程匹配",
            "显式手填单价覆盖",
        ],
        handler=_run_conductor_skill,
    ),
    CalculationSkill(
        id="glue_external_and_process",
        name="胶料/外购材料及后续制程费用",
        phase="material_process",
        order=20,
        description="计算 PVC 母料、外购料、色母材料金额，以及绝缘、外被、倒线、集合等制程费用。",
        capabilities=[
            "PVC BOM 售价取价",
            "外购视图 v_qs_bzcb 取价",
            "星号料号模糊匹配并取最高价格",
            "显式手填单价兜底",
            "绝缘/外被/倒线/集合制程公式",
        ],
        handler=_run_glue_skill,
    ),
    CalculationSkill(
        id="price_summary",
        name="最终售价汇总",
        phase="summary",
        order=90,
        description="校验本次计算上下文，汇总材料成本、费用总计、成本、取利售价、不取利售价和最终售价。",
        capabilities=[
            "本次计算结果校验",
            "材料成本汇总",
            "费用总计汇总",
            "最终售价公式",
        ],
        handler=_run_price_summary_skill,
    ),
]


def list_calculation_skills() -> list[dict]:
    return [skill.to_dict() for skill in sorted(BUILTIN_CALCULATION_SKILLS, key=lambda item: item.order)]


def run_full_price_skills(db: Session, quotation: QuotationMain, operator: str) -> dict:
    ctx = CalculationContext()
    result = {"skills": list_calculation_skills()}
    for skill in sorted(BUILTIN_CALCULATION_SKILLS, key=lambda item: item.order):
        try:
            result[skill.id] = skill.run(db, quotation, operator, ctx)
        except ValueError as exc:
            db.rollback()
            raise ValueError(f"{skill.name}失败：{exc}") from exc
    result["conductor"] = result.get("conductor_material_and_process")
    result["glue"] = result.get("glue_external_and_process")
    result["price_summary"] = result.get("price_summary")
    db.commit()
    return result
