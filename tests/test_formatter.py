from __future__ import annotations

from utils.cli.formatter import (
    CliNode,
    indent_cli_lines,
    payload_to_set_lines,
    quote_value,
    render_config_pair,
    render_cli_tree,
    section_to_enter_path,
    serialize_atom,
)


def test_quote_value_escapes_special_chars():
    assert quote_value('a"b\\c') == '"a\\"b\\\\c"'


def test_serialize_atom_bool_and_string():
    assert serialize_atom(True) == "enable"
    assert serialize_atom(False) == "disable"
    assert serialize_atom("plain-token") == "plain-token"
    assert serialize_atom("with space") == '"with space"'


def test_payload_to_set_lines_handles_nested_and_list():
    lines = payload_to_set_lines(
        {
            "join_roles": [1, 2],
            "welcome_message": "hi there",
            "root_connection": {"send_crashlog_root": False},
        }
    )
    assert "set join-roles 1 2" in lines
    assert 'set welcome-message "hi there"' in lines
    assert "set root-connection.send-crashlog-root disable" in lines


def test_section_to_enter_path_for_nested_sections():
    assert section_to_enter_path("guild-log/message-log") == ["guild-log", "message-log"]
    assert section_to_enter_path("control-plane/root-connection") == ["control-plane", "root-connection"]
    assert section_to_enter_path("root-enforce-override/control-plane/tick") == [
        "root-enforce-override",
        "control-plane",
        "tick",
    ]


def test_indent_cli_lines_with_nested_enter_and_select():
    lines = indent_cli_lines(
        [
            "enter root-enforce-override",
            "select 123",
            "set max-tick-limit 9000",
            "enter control-plane",
            "enter tick",
            "# no settings",
        ]
    )
    assert lines[0] == "enter root-enforce-override"
    assert lines[1] == "  select 123"
    assert lines[2] == "    set max-tick-limit 9000"
    assert lines[3] == "    enter control-plane"
    assert lines[4] == "    enter tick"
    assert lines[5] == "      # no settings"


def test_render_config_pair_outputs_now_and_deploy_blocks():
    rendered = render_config_pair(
        "welcome",
        {"join_roles": [1, 2]},
        {"join_roles": [3]},
        enter_path=["welcome"],
    )
    assert rendered.startswith("now-config:")
    assert "\ndeploy-config:\n" in rendered
    assert "enter welcome" in rendered
    assert "set join-roles 1 2" in rendered
    assert "set join-roles 3" in rendered


def test_render_cli_tree_preserves_sibling_hierarchy():
    lines = render_cli_tree(
        [
            CliNode(
                kind="enter",
                text="enter sticky-message",
                children=[
                    CliNode(
                        kind="select",
                        text="select 1",
                        children=[
                            CliNode(kind="enter", text="enter channels", children=[CliNode(kind="select", text="select 1")]),
                            CliNode(kind="enter", text="enter embed"),
                        ],
                    )
                ],
            )
        ]
    )
    assert lines == [
        "enter sticky-message",
        "  select 1",
        "    enter channels",
        "      select 1",
        "      leave",
        "    enter embed",
        "    leave",
        "  leave",
    ]
