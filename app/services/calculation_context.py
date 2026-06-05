from dataclasses import dataclass, field


@dataclass
class CalculationContext:
    """Tracks values produced during one calculation request.

    This is intentionally request-scoped and never persisted. It prevents old
    values imported from Excel templates from being treated as fresh results.
    """

    calculated_material_ids: set[int] = field(default_factory=set)
    calculated_process_ids: set[int] = field(default_factory=set)
    material_sources: dict[int, str] = field(default_factory=dict)
    process_sources: dict[int, str] = field(default_factory=dict)

    def mark_material(self, material_id: int | None, source: str):
        if material_id is None:
            return
        self.calculated_material_ids.add(int(material_id))
        self.material_sources[int(material_id)] = source

    def mark_process(self, process_id: int | None, source: str):
        if process_id is None:
            return
        self.calculated_process_ids.add(int(process_id))
        self.process_sources[int(process_id)] = source
