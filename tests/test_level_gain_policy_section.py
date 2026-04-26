from __future__ import annotations

import pytest

from utils.cli.sections.base import SectionError
from utils.cli.sections.level_gain_policy import LevelGainPolicySection


def _section() -> LevelGainPolicySection:
    return LevelGainPolicySection()


def test_default_payload_has_only_immutable_rule():
    spec = _section()
    payload = spec.default_payload()
    assert len(payload["policies"]) == 1
    rule = payload["policies"][0]
    assert rule["id"] == 0
    assert rule["action"] == "gain"
    assert rule["channels"] == "any"
    assert rule["roles"] == "any"
    assert rule["method"] == "any"
    assert rule["gain_mode"] == "static"
    assert rule["gain_xp"] == 1


def test_validate_payload_rejects_missing_default_rule():
    spec = _section()
    with pytest.raises(SectionError, match="missing default rule"):
        spec.validate_payload({"policies": [{"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"}]})


def test_validate_payload_rejects_duplicate_ids():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    with pytest.raises(SectionError, match="duplicate id"):
        spec.validate_payload(payload)


def test_insert_before_shifts_non_zero_ids():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 2, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    updated = spec.insert_before(payload, before_id=2)
    assert [rule["id"] for rule in updated["policies"]] == [1, 2, 3, 0]
    assert updated["policies"][1]["name"] == ""


def test_insert_before_zero_appends_before_default():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    updated = spec.insert_before(payload, before_id=0)
    assert [rule["id"] for rule in updated["policies"]] == [1, 2, 0]


def test_move_rule_before_and_after():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 2, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 3, "name": "c", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    moved_before = spec.move_rule(payload, rule_id=3, mode="before", target_id=1)
    assert [rule["name"] for rule in moved_before["policies"][:-1]] == ["c", "a", "b"]

    moved_after = spec.move_rule(payload, rule_id=1, mode="after", target_id=3)
    assert [rule["name"] for rule in moved_after["policies"][:-1]] == ["b", "c", "a"]


def test_move_rule_top_and_bottom():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 2, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 3, "name": "c", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    moved_top = spec.move_rule(payload, rule_id=3, mode="top")
    assert [rule["name"] for rule in moved_top["policies"][:-1]] == ["c", "a", "b"]

    moved_bottom = spec.move_rule(payload, rule_id=1, mode="bottom")
    assert [rule["name"] for rule in moved_bottom["policies"][:-1]] == ["b", "c", "a"]


def test_move_rule_rejects_immutable_default_id():
    spec = _section()
    payload = spec.default_payload()
    with pytest.raises(SectionError, match="id=0 is immutable"):
        spec.move_rule(payload, rule_id=0, mode="top")


def test_reorder_ids_fills_gaps_preserving_order():
    spec = _section()
    payload = {
        "policies": [
            {"id": 10, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 20, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    updated = spec.reorder_ids(payload)
    assert [rule["id"] for rule in updated["policies"]] == [1, 2, 0]
    assert [rule["name"] for rule in updated["policies"][:-1]] == ["a", "b"]


def test_validate_set_with_context_requires_select():
    spec = _section()
    with pytest.raises(SectionError, match="select <id>"):
        spec.validate_set_with_context(spec.default_payload(), "action", ["gain"], selected_object=None)


def test_validate_set_with_context_updates_rule_fields():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    updated = spec.validate_set_with_context(payload, "channels", ["100", "200"], selected_object="1")
    assert updated["policies"][0]["channels"] == [100, 200]


def test_render_show_marks_rule_zero_immutable():
    spec = _section()
    rendered = spec.render_show(spec.default_payload(), spec.default_payload())
    assert "select 0" in rendered
    assert "# immutable default rule" in rendered
    assert "set action" not in rendered


def test_set_time_rejects_non_hhmm_format():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    with pytest.raises(SectionError, match="field=time reason=invalid format"):
        spec.validate_set_with_context(payload, "time", ["9:0", "18:00"], selected_object="1")


def test_set_time_rejects_out_of_range():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    with pytest.raises(SectionError, match="field=time reason=out of range"):
        spec.validate_set_with_context(payload, "time", ["24:00", "01:00"], selected_object="1")


def test_validate_payload_normalizes_hhmm_format():
    spec = _section()
    payload = {
        "policies": [
            {
                "id": 1,
                "name": "",
                "action": "gain",
                "channels": "any",
                "roles": "any",
                "method": "any",
                "time_start": "09:00",
                "time_end": "18:00",
            },
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    validated = spec.validate_payload(payload)
    assert validated["policies"][0]["time_start"] == "09:00"
    assert validated["policies"][0]["time_end"] == "18:00"


def test_select_target_with_payload_creates_missing_rule():
    spec = _section()
    payload = spec.default_payload()
    selected, updated = spec.select_target_with_payload(payload, "1")
    assert selected == "1"
    assert updated is not None
    assert [rule["id"] for rule in updated["policies"]] == [1, 0]


def test_select_target_with_payload_shifts_existing_rules():
    spec = _section()
    payload = {
        "policies": [
            {"id": 1, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 3, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
        ]
    }
    selected, updated = spec.select_target_with_payload(payload, "2")
    assert selected == "2"
    assert updated is not None
    assert [rule["id"] for rule in updated["policies"]] == [1, 2, 4, 0]
