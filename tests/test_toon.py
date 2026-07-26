"""Tests for src/toon.py — shared TOON encoder used by read_logs_sqlite and tasks__list."""
from src.toon import rows_to_toon, toon_cell


class TestToonCell:
    def test_none_becomes_empty_string(self):
        assert toon_cell(None) == ""

    def test_plain_value_passthrough(self):
        assert toon_cell("hello") == "hello"

    def test_int_stringified(self):
        assert toon_cell(42) == "42"

    def test_comma_gets_quoted(self):
        assert toon_cell("a,b") == '"a,b"'

    def test_newline_gets_quoted(self):
        assert toon_cell("a\nb") == '"a\nb"'

    def test_quote_gets_escaped_and_quoted(self):
        assert toon_cell('a"b') == '"a""b"'


class TestRowsToToon:
    def test_empty_list(self):
        assert rows_to_toon([]) == "rows[0]{}:"

    def test_single_row(self):
        result = rows_to_toon([{"id": 1, "name": "foo"}])
        assert result == "rows[1]{id,name}:\n  1,foo"

    def test_multiple_rows(self):
        result = rows_to_toon([{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}])
        assert result == "rows[2]{id,name}:\n  1,foo\n  2,bar"

    def test_uses_first_row_keys_as_header(self):
        result = rows_to_toon([{"a": 1, "b": 2}])
        assert "rows[1]{a,b}:" in result
