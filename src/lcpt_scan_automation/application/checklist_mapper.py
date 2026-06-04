from pathlib import Path
from typing import Optional

import yaml

from ..domain.enums import CoverSheetAction

# Key: (task_type, CoverSheetAction)  →  Value: expected checklist item name in CP Suite
_MappingKey = tuple[str, CoverSheetAction]


class ChecklistMapper:
    """Loads the checklist mapping YAML and resolves cover-sheet actions to
    CP Suite checklist item names for a given task type."""

    def __init__(self, config_path: str | Path) -> None:
        self._mappings: dict[_MappingKey, str] = {}
        self._load(Path(config_path))

    def _load(self, path: Path) -> None:
        if not path.exists():
            return  # tolerate missing file; all lookups will return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in raw.get("mappings", []):
            try:
                action = CoverSheetAction(entry["action"])
            except (KeyError, ValueError):
                continue
            task_type = entry.get("task_type", "")
            item_name = entry.get("checklist_item_name", "")
            if task_type and item_name:
                self._mappings[(task_type, action)] = item_name

    def get_checklist_item_name(
        self,
        task_type: str,
        action: CoverSheetAction,
    ) -> Optional[str]:
        """Return the expected CP Suite checklist item name, or None if unmapped."""
        return self._mappings.get((task_type, action))

    def all_mappings(self) -> dict[_MappingKey, str]:
        return dict(self._mappings)
