from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CliNode:
    kind: str
    text: str
    children: list["CliNode"] = field(default_factory=list)


def quote_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def maybe_quote_string(value: str) -> str:
    if value == "":
        return quote_value(value)
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/:")
    if all(ch in safe for ch in value):
        return value
    return quote_value(value)


def serialize_atom(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "enable" if value else "disable"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return maybe_quote_string(value)
    return maybe_quote_string(str(value))


def to_cli_key(key: str) -> str:
    return key.replace("_", "-")


def payload_to_set_lines(payload: dict[str, Any], prefix: str = "set") -> list[str]:
    lines: list[str] = []
    for raw_key, raw_value in payload.items():
        key = to_cli_key(raw_key)
        if raw_value is None:
            continue
        if isinstance(raw_value, list):
            if not raw_value:
                continue
            tokens = [item for item in (serialize_atom(item) for item in raw_value) if item is not None]
            if not tokens:
                continue
            lines.append(f"{prefix} {key} {' '.join(tokens)}")
            continue
        if isinstance(raw_value, dict):
            if not raw_value:
                continue
            for inner_key, inner_value in raw_value.items():
                if inner_value is None:
                    continue
                atom = serialize_atom(inner_value)
                if atom is None:
                    continue
                lines.append(f"{prefix} {key}.{to_cli_key(inner_key)} {atom}")
            continue
        atom = serialize_atom(raw_value)
        if atom is None:
            continue
        lines.append(f"{prefix} {key} {atom}")
    return lines


def render_section_block(section: str, payload: dict[str, Any] | None) -> list[str]:
    lines = render_enter_lines(section)
    if not payload:
        lines.append("# no settings")
        return lines
    set_lines = payload_to_set_lines(payload)
    if not set_lines:
        lines.append("# no settings")
        return lines
    lines.extend(set_lines)
    return lines


def render_enter_lines(section: str) -> list[str]:
    return [f"enter {section}"]


def render_enter_lines_for_path(path: list[str]) -> list[str]:
    return [f"enter {item}" for item in path]


def section_to_enter_path(section: str) -> list[str]:
    if "/" not in section:
        return [section]
    if section.startswith("root-enforce-override/"):
        _, remainder = section.split("/", 1)
        return ["root-enforce-override", *remainder.split("/")]
    if section.startswith("root-defaults/"):
        _, remainder = section.split("/", 1)
        return ["root-defaults", *remainder.split("/")]
    if section.startswith("root-enforce/"):
        _, remainder = section.split("/", 1)
        return ["root-enforce", *remainder.split("/")]
    if section.startswith("control-plane/"):
        _, child = section.split("/", 1)
        return ["control-plane", child]
    if section.startswith("tenant-connection/"):
        _, child = section.split("/", 1)
        return ["tenant-connection", child]
    if section.startswith("guild-log/"):
        _, child = section.split("/", 1)
        return ["guild-log", child]
    return [section]


def render_config_pair(
    section: str,
    now_payload: dict[str, Any] | None,
    deploy_payload: dict[str, Any] | None,
    enter_path: list[str] | None = None,
) -> str:
    path = enter_path if enter_path else [section]
    return render_config_pair_from_nodes(
        build_enter_tree(path, now_payload),
        build_enter_tree(path, deploy_payload),
    )


def build_enter_tree(path: list[str], payload: dict[str, Any] | None) -> CliNode:
    if not path:
        raise ValueError("path must not be empty")
    root = CliNode(kind="enter", text=f"enter {path[0]}")
    cursor = root
    for token in path[1:]:
        child = CliNode(kind="enter", text=f"enter {token}")
        cursor.children.append(child)
        cursor = child
    if not payload:
        cursor.children.append(CliNode(kind="comment", text="# no settings"))
        return root
    set_lines = payload_to_set_lines(payload)
    if not set_lines:
        cursor.children.append(CliNode(kind="comment", text="# no settings"))
        return root
    cursor.children.extend(CliNode(kind="set", text=line) for line in set_lines)
    return root


def no_settings_node() -> CliNode:
    return CliNode(kind="comment", text="# no settings")


def empty_select_node(target: str = "<id>", children: list[CliNode] | None = None) -> CliNode:
    return CliNode(kind="select", text=f"select {target}", children=children or [no_settings_node()])


def render_config_pair_from_builders(
    now_config: dict[str, Any] | None,
    deploy_config: dict[str, Any] | None,
    build_node: Any,
) -> str:
    return render_config_pair_from_nodes(build_node(now_config), build_node(deploy_config))


def build_sections_tree(root_name: str, sections: dict[str, dict[str, Any]] | None) -> CliNode:
    root = CliNode(kind="enter", text=f"enter {root_name}")
    if not sections:
        root.children.append(CliNode(kind="comment", text="# no settings"))
        return root

    tree: dict[str, Any] = {}
    for section_key, fields in sorted(sections.items()):
        if not isinstance(fields, dict):
            continue
        node = tree
        for token in str(section_key).split("/"):
            if not token:
                continue
            node = node.setdefault(token, {})
        node.setdefault("_fields", {}).update(fields)

    def walk(node: dict[str, Any], parent: CliNode) -> None:
        for child in sorted(key for key in node.keys() if key != "_fields"):
            child_node = CliNode(kind="enter", text=f"enter {child}")
            parent.children.append(child_node)
            walk(node[child], child_node)
        fields = node.get("_fields")
        if not isinstance(fields, dict):
            return
        for key, value in sorted(fields.items()):
            atom = serialize_atom(value)
            if atom is None:
                continue
            parent.children.append(CliNode(kind="set", text=f"set {to_cli_key(str(key))} {atom}"))

    walk(tree, root)
    if not root.children:
        root.children.append(CliNode(kind="comment", text="# no settings"))
    return root


def render_config_pair_from_nodes(now_node: CliNode, deploy_node: CliNode | None = None) -> str:
    lines = ["now-config:"]
    lines.extend(render_cli_tree([now_node]))
    lines.append("deploy-config:")
    if deploy_node is None:
        deploy_node = build_enter_tree([now_node.text.removeprefix("enter ")], None)
    lines.extend(render_cli_tree([deploy_node]))
    return "\n".join(lines)


def indent_cli_lines(lines: list[str], indent_unit: str = "  ") -> list[str]:
    indented: list[str] = []
    enter_depth = 0
    select_active = False
    select_depth = 0
    last_kind: str | None = None
    last_depth = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            indented.append("")
            last_kind = None
            continue
        if line.startswith("enter "):
            depth = enter_depth + (1 if select_active else 0)
            indented.append(f"{indent_unit * depth}{line}")
            enter_depth += 1
            select_active = False
            last_kind = "enter"
            last_depth = depth
            continue
        if line.startswith("select "):
            depth = enter_depth
            if last_kind == "enter":
                depth = last_depth + 1
            indented.append(f"{indent_unit * depth}{line}")
            select_active = True
            select_depth = depth
            last_kind = "select"
            last_depth = depth
            continue
        if line.startswith("set "):
            depth = (select_depth + 1) if select_active else enter_depth
            indented.append(f"{indent_unit * depth}{line}")
            last_kind = "set"
            last_depth = depth
            continue
        if line.startswith("#"):
            depth = (select_depth + 1) if select_active else enter_depth
            indented.append(f"{indent_unit * depth}{line}")
            last_kind = "comment"
            last_depth = depth
            continue
        indented.append(f"{indent_unit * enter_depth}{line}")
        last_kind = "other"
        last_depth = enter_depth
    return indented


def render_cli_tree(nodes: list[CliNode], indent_unit: str = "  ", include_closing_leave: bool = True) -> list[str]:
    rendered: list[str] = []

    def walk(node: CliNode, depth: int) -> None:
        rendered.append(f"{indent_unit * depth}{node.text}")
        for child in node.children:
            walk(child, depth + 1)
        if include_closing_leave and node.kind in {"enter", "select"} and node.children:
            rendered.append(f"{indent_unit * (depth + 1)}leave")

    for node in nodes:
        walk(node, 0)
    return rendered
