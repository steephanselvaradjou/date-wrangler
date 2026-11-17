"""Every date the token reader hands the scanner goes under a rule's name.

The scanner orders the reader's dates against the rules' matches by RULES, so a name that
is no rule cannot be ordered. A reader that *declined* went out under its function's name
-- "days_month" -- and beside a rule's match at the same place, parse raised KeyError.
Hypothesis found it in " '02 TO1MAR"; it had been there since day runs were read.
"""

from __future__ import annotations

from datetime import date

from date_wrangler import WranglerConfig, diagnose, parse, tokens
from date_wrangler.config import FiscalCalendar
from date_wrangler.rules import RULES
from date_wrangler.wrangler import _ORDER

READERS = (
    tokens._OPENING_WITH_A_NUMBER
    + tokens._OPENING_WITH_A_MONTH
    + tokens._OPENING_WITH_A_YEAR_WORD
    + tokens._OPENING_WITH_BETWEEN
)


def test_every_reader_reads_for_a_rule_the_scanner_can_order():
    names = {rule.name for rule in RULES}
    for reader in READERS:
        assert tokens.rule_of(reader) in names, reader.__name__
        assert tokens.rule_of(reader) in _ORDER, reader.__name__


#: Phrases each token reader reads: runs and lists of days either way round, days run
#: together, the year first, a month and its year, a month alone, a fiscal year's month,
#: "between", and declines.
_READER_PHRASES = [
    "15-20 March 2024", "March 15-20, 2024", "15, 16 and 20 March 2024", "March 1, 5 and 9",
    "15MAR2024", "15-Mar-2024", "2024 March 15", "2024 March 15th-20th", "15 March 2024",
    "Mar-15-2024", "March 15, 2024", "Mar 15 14:30:00", "January first, 2024", "March 2024",
    "Mar-24", "March", "FY25 June", "between 15 and 20 March 2024", "15-1 March 2024",
    "between the 15th and the 20th of March",
]


def test_every_name_a_reader_hands_the_scanner_can_be_ordered():
    """Its dates' names too, not only its declines': each must be a rule in RULES."""
    cfg = WranglerConfig()
    seen = set()
    for text in _READER_PHRASES:
        for found in tokens.read(text, cfg):
            seen.add(found[0])
            assert found[0] in _ORDER, (text, found[0])
    assert {"day_span", "day_list", "dashed_day", "month_year", "month"} <= seen


def test_a_declined_day_run_beside_a_rule_does_not_raise():
    cfg = WranglerConfig(fiscal=FiscalCalendar(1), bare_period_basis="fiscal",
                         two_digit_pivot=0, strictness="strict")
    for text in [" '02 TO1MAR", "'02 TO1MAR", " '02 to1mar"]:
        parse(text, today=date(2000, 1, 1), config=cfg)  # raised KeyError: 'days_month'


def test_a_declined_day_run_is_reported_under_its_rule():
    """The reason it was declined still reaches the caller, named for the rule."""
    found, diags = diagnose("15-1 March 2024", today=date(2026, 11, 16))
    assert not found
    assert diags and {d.rule for d in diags} <= {rule.name for rule in RULES}
