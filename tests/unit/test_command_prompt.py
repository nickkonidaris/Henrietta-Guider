import pytest

from henrietta_guider.tui.command_prompt import (
    ClearStamps,
    ParseError,
    SetPa,
    SetStamp,
    ShowHelp,
    parse_command,
)


@pytest.mark.unit
class TestCommandParser:
    def test_set_stamp_basic(self):
        r = parse_command("1 100 200 300 400")
        assert isinstance(r, SetStamp)
        assert (r.n, r.x_min, r.y_lo, r.x_max, r.y_hi) == (1, 100, 200, 300, 400)

    def test_set_stamp_commas_ok(self):
        r = parse_command("3, 10, 20, 30, 40")
        assert isinstance(r, SetStamp)
        assert r.n == 3

    def test_help(self):
        assert isinstance(parse_command("?"), ShowHelp)

    def test_empty_is_error(self):
        assert isinstance(parse_command(""), ParseError)
        assert isinstance(parse_command("   "), ParseError)

    def test_clear_all(self):
        r = parse_command("clear")
        assert isinstance(r, ClearStamps)
        assert r.n is None

    def test_clear_one(self):
        r = parse_command("clear 2")
        assert isinstance(r, ClearStamps)
        assert r.n == 2

    def test_clear_bad_id(self):
        assert isinstance(parse_command("clear 9"), ParseError)

    def test_clear_bad_arg(self):
        assert isinstance(parse_command("clear foo"), ParseError)

    def test_pa_basic(self):
        r = parse_command("pa 35")
        assert isinstance(r, SetPa)
        assert r.deg == pytest.approx(35.0)

    def test_pa_negative(self):
        r = parse_command("pa -12.5")
        assert isinstance(r, SetPa)
        assert r.deg == pytest.approx(-12.5)

    def test_pa_no_arg(self):
        assert isinstance(parse_command("pa"), ParseError)

    def test_pa_bad_arg(self):
        assert isinstance(parse_command("pa abc"), ParseError)

    def test_invalid_id(self):
        r = parse_command("9 1 2 3 4")
        assert isinstance(r, ParseError)

    def test_invalid_count(self):
        assert isinstance(parse_command("1 2 3"), ParseError)

    def test_x_min_must_be_less_than_x_max(self):
        assert isinstance(parse_command("1 100 200 100 400"), ParseError)
        assert isinstance(parse_command("1 100 200 99 400"), ParseError)

    def test_y_lo_must_be_less_than_y_hi(self):
        assert isinstance(parse_command("1 100 400 200 400"), ParseError)
        assert isinstance(parse_command("1 100 400 200 399"), ParseError)
