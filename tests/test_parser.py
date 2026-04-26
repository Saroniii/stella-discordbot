from utils.cli.parser import ParseError, ParsedCommand, parse_line
import random
import string


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


def test_parse_line_lowercases_command_name():
    parsed = parse_line("SeT join-roles 1 2")
    assert isinstance(parsed, ParsedCommand)
    assert parsed.name == "set"
    assert parsed.args == ["join-roles", "1", "2"]


def test_parse_line_comment_with_leading_whitespace():
    assert parse_line("   # ignored") is None


def test_parse_line_hash_inside_token_is_not_comment():
    parsed = parse_line('set welcome-message "hello #world"')
    assert isinstance(parsed, ParsedCommand)
    assert parsed.args == ["welcome-message", "hello #world"]


def test_parse_line_supports_escaped_quotes_and_backslashes():
    parsed = parse_line(r'set welcome-message "a\"b\\c"')
    assert isinstance(parsed, ParsedCommand)
    assert parsed.args == ["welcome-message", 'a"b\\c']


def test_parse_line_question_token():
    parsed = parse_line("?")
    assert isinstance(parsed, ParsedCommand)
    assert parsed.name == "?"
    assert parsed.args == []


def test_parse_line_fullwidth_question_token_normalized():
    parsed = parse_line("？")
    assert isinstance(parsed, ParsedCommand)
    assert parsed.name == "?"
    assert parsed.args == []


def test_parse_line_random_inputs_do_not_raise():
    random.seed(42)
    alphabet = string.ascii_letters + string.digits + string.punctuation + " \t"
    for _ in range(1000):
        size = random.randint(0, 120)
        line = "".join(random.choice(alphabet) for _ in range(size))
        parsed = parse_line(line)
        assert parsed is None or isinstance(parsed, (ParsedCommand, ParseError))
