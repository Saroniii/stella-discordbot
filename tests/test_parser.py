from utils.cli.parser import ParseError, ParsedCommand, parse_line


def test_parse_line_comment_and_empty():
    assert parse_line("   ") is None
    assert parse_line("# comment") is None


def test_parse_line_command_with_quotes():
    parsed = parse_line('set welcome-message "Hello World"')
    assert isinstance(parsed, ParsedCommand)
    assert parsed.name == "set"
    assert parsed.args == ["welcome-message", "Hello World"]


def test_parse_line_error():
    parsed = parse_line('set welcome-message "Hello')
    assert isinstance(parsed, ParseError)
