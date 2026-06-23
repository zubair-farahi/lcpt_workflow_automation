"""Unit tests for ChecklistMapper."""

from pathlib import Path

import pytest
import yaml

from lcpt_scan_automation.application.checklist_mapper import ChecklistMapper
from lcpt_scan_automation.domain.enums import CoverSheetAction


@pytest.fixture
def mapping_yaml(tmp_path: Path) -> Path:
    data = {
        "mappings": [
            {
                "task_type": "TYPE_A",
                "action": "PROCESS_THROUGH_STATE_AGENCY",
                "checklist_item_name": "Process through state agency",
            },
            {
                "task_type": "TYPE_A",
                "action": "RECEIVE_CREDENTIALS",
                "checklist_item_name": "Receive credentials",
            },
            {
                "task_type": "TYPE_B",
                "action": "SEND_CREDENTIALS",
                "checklist_item_name": "Mark complete",
            },
        ]
    }
    path = tmp_path / "checklist_mapping.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


class TestChecklistMapper:
    def test_known_mapping_returns_item_name(self, mapping_yaml: Path):
        mapper = ChecklistMapper(mapping_yaml)
        result = mapper.get_checklist_item_name("TYPE_A", CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY)
        assert result == "Process through state agency"

    def test_second_task_type_mapping(self, mapping_yaml: Path):
        mapper = ChecklistMapper(mapping_yaml)
        result = mapper.get_checklist_item_name("TYPE_B", CoverSheetAction.SEND_CREDENTIALS)
        assert result == "Mark complete"

    def test_unknown_task_type_returns_none(self, mapping_yaml: Path):
        mapper = ChecklistMapper(mapping_yaml)
        result = mapper.get_checklist_item_name("UNKNOWN_TYPE", CoverSheetAction.SEND_CREDENTIALS)
        assert result is None

    def test_unknown_action_for_known_type_returns_none(self, mapping_yaml: Path):
        mapper = ChecklistMapper(mapping_yaml)
        result = mapper.get_checklist_item_name("TYPE_A", CoverSheetAction.SEND_CREDENTIALS)
        assert result is None  # TYPE_A has no SEND_CREDENTIALS mapping in this fixture

    def test_missing_file_does_not_raise(self, tmp_path: Path):
        mapper = ChecklistMapper(tmp_path / "nonexistent.yaml")
        assert mapper.get_checklist_item_name("TYPE_A", CoverSheetAction.SEND_CREDENTIALS) is None
