"""Reading dates in steps: split the text into pieces, name each piece, build dates from them.

The dates written with month names -- days, months and the years beside them -- are read
here; :func:`~date_wrangler.wrangler._scan` takes turns with the regular expressions for
every other rule. Each of those used to be one regular expression that had to spell out
every way of writing every piece -- a full stop after "Sept", an apostrophe before "18", a
comma before the year -- and a spelling added to one rule was missing from the next. Here a
piece is recognised once, by looking the word up, and a date is a short sequence of pieces
in which the joining words are optional.

    "01may 2018"  ->  01 | may | 2018  ->  DAY(1) MONTH(5) YEAR(2018)  ->  1 May 2018
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable

from .config import MonthNumber, WranglerConfig
from .rules import _fy_label_of_span, _not_one_fiscal_year, _pivot_year, parse_year_token
from .spec import Kind, Spec
from .types import Basis
from .vocab import MONTH_DISPLAY, MONTHS, ORDINALS

__all__ = ["Declined", "Reader", "Token", "read", "tokenize"]


class Token:
    """One word, number or mark, and where it sits in the text.

    A plain class rather than a frozen dataclass: a text has a token per word, and a frozen
    dataclass's constructor costs several times as much, which showed in the profile.
    """

    __slots__ = ("end", "low", "start", "text", "tight")

    def __init__(self, text: str, start: int, end: int, tight: bool) -> None:
        self.text = text
        self.low = text.lower()
        self.start = start
        self.end = end
        #: Touches the token before it, with no space between: the "june" of "13june2018".
        self.tight = tight


#: A number with its ordinal suffix ("1st", unless letters run on -- but a month name may:
#: "15thMarch"), a number, a word, or one mark. The suffix is never optional in the first
#: branch: an optional one let the number give back a digit, and "15MAR2024" came out as 1,
#: 5, MAR. REGRESSION: "15thMarch 2024" was "15" and "thMarch", and nothing was read.
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_LEX = re.compile(
    rf"\d+(?:st|nd|rd|th)(?:(?![a-z])|(?=(?:{_MONTH_ALT})(?![a-z])))|\d+|[a-z]+|\S",
    re.IGNORECASE,
)


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    last = -1
    for m in _LEX.finditer(text):
        start, end = m.span()
        word = m.group()
        # A month name run into the FY or CY after it: "MarchFY24". REGRESSION: it was one
        # word, "marchfy", and nothing was read. Split here, word by word, rather than in
        # the pattern, which would try every month name at the start of every word.
        if len(word) > 4 and word[-2:].lower() in ("fy", "cy") and word[:-2].lower() in MONTHS:
            out.append(Token(word[:-2], start, end - 2, start == last))
            out.append(Token(word[-2:], end - 2, end, True))
        else:
            out.append(Token(word, start, end, start == last))
        last = end
    return out


#: Every date of this family names a month -- "15 March", "Mar-24", "FY25 June", and the
#: "MAR" of "15MAR2024", where digits touch it -- so text without one holds none, and is
#: not split into tokens at all. Most questions with a date in them have no month name:
#: "Q1 2024", "from 2012 to 2013".
_MAY_HOLD = re.compile(
    rf"(?:(?<![a-z])|(?<=\d(?:st|nd|rd|th)))(?:{_MONTH_ALT})(?:(?![a-z])|(?=[fc]y(?![a-z])))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step 2: what each piece is
# ---------------------------------------------------------------------------

_DAY = re.compile(r"(\d{1,2})(st|nd|rd|th)?")
#: What joins one date to the next: a run, a list or a comparison.
_DATE_JOINS = frozenset(
    {"-", "to", "till", "until", "through", "thru", "and", "&", "or", "vs", "versus"}
)
_WORD_ORDINALS = {w: n for w, n in ORDINALS.items() if w.isalpha() or "-" in w}


def _day(t: Token) -> int | None:
    m = _DAY.fullmatch(t.low)
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 31 else None


def _year4(t: Token) -> int | None:
    return int(t.low) if len(t.low) == 4 and t.low.isdigit() else None


def _two_digits(t: Token) -> int | None:
    return int(t.low) if len(t.low) == 2 and t.low.isdigit() else None


_FULL_MONTH_NAMES = frozenset(name.lower() for name in MONTH_DISPLAY)


class Declined(ValueError):
    """Read, and refused with a reason: "June 2024-25" on a calendar basis. Carries the
    token the words end before, so they are not read again as something else."""

    def __init__(self, reason: str, end: int) -> None:
        super().__init__(reason)
        self.end = end


# ---------------------------------------------------------------------------
# Step 3: dates from pieces
# ---------------------------------------------------------------------------


class _Pieces:
    """The tokens, with the small questions every rule asks of them."""

    def __init__(self, toks: list[Token]) -> None:
        self.toks = toks
        self.n = len(toks)
        # What each token is, looked up once: every reader asks, often of the same token.
        self.months = [MONTHS.get(t.low) for t in toks]
        self.days = [_day(t) if t.low[0].isdigit() else None for t in toks]

    def at(self, i: int) -> Token | None:
        return self.toks[i] if 0 <= i < self.n else None

    def month(self, i: int) -> int | None:
        return self.months[i] if 0 <= i < self.n else None

    def day(self, i: int) -> int | None:
        return self.days[i] if 0 <= i < self.n else None

    def is_(self, i: int, *words: str, tight: bool | None = None) -> bool:
        t = self.at(i)
        return t is not None and t.low in words and (tight is None or t.tight == tight)

    def year_after(
        self, i: int, cfg: WranglerConfig, quarter_check: bool = False
    ) -> tuple[int | None, Basis | None, int | None, int] | None:
        """The year written after a month or a day, starting at token ``i``: ", 2013",
        "of 2024", "'24", "' 2018", "FY25", "CY2024", "last year", "2024-25". Returns the
        year, the basis it states, a year offset, and where it ends.

        A comma only before a year written as one -- "March, next year" is two things --
        and after a comma never a year that a quarter follows: in "March, FY24 Q3" the
        FY24 is Q3's. ``quarter_check`` asks the same without a comma.
        """
        mark, digits = self.at(i), self.at(i + 1)
        if (
            mark is not None and mark.tight and mark.text in "-/."
            and digits is not None and digits.tight and _year4(digits) is not None
            and digits.low[:2] in ("19", "20") and not self.date_goes_on(i + 2)
        ):
            # "15 Mar-2024", "Mar 15/2024": a mark touching both. Spaced, a dash is a span --
            # "15 Mar - 2024" -- as after any label. REGRESSION: the span was read here too,
            # from 15 March to the end of 2024.
            return int(digits.low), None, None, i + 2
        k = i
        comma = self.is_(k, ",")
        if comma:
            k += 1
        quarter_check = quarter_check or comma
        if self.is_(k, "of"):
            k += 1
        if self.is_(k, "the") and self.is_(k + 1, "year"):
            k += 1  # "March of the year 2013"
        if self.is_(k, "year"):
            j = k + 1
            while self.is_(j, "-"):
                j += 1
            if self.is_(j, "'"):
                j += 1
            y = self.at(j)
            if y is not None and y.low.isdigit() and len(y.low) in (2, 4):
                return _pivot_year(y.low, cfg), None, None, j + 1
            return None
        t = self.at(k)
        if t is None:
            return None
        if t.text == "'":
            nxt = self.at(k + 1)
            if nxt is not None and nxt.tight and _two_digits(nxt) is not None:
                return _pivot_year(nxt.low, cfg), None, None, k + 2
            if nxt is not None and _year4(nxt) is not None:
                return int(nxt.low), None, None, k + 2
            return None
        year = _year4(t)
        if year is not None:
            if self._split_year_follows(k):
                # "June 2024-25": year_basis decides, exactly as for "Q3 2024-25" -- a
                # fiscal year on a fiscal basis, and refused, with a reason, on a calendar one.
                try:
                    split, split_basis = parse_year_token(
                        f"{t.low}-{self.toks[k + 2].low}", cfg
                    )
                except ValueError as exc:
                    raise Declined(str(exc), k + 3) from None
                return split, split_basis, None, k + 3
            if quarter_check and self._quarter_follows(k + 1):
                return None
            mark, digits = self.at(k + 1), self.at(k + 2)
            if (
                mark is not None and mark.tight and mark.text in "-/."
                and digits is not None and digits.tight and digits.low.isdigit()
                and not self.day_of_a_month(k + 2)
            ):
                return None  # the "2024" of "2024-03-15" begins a date of its own
            return year, None, None, k + 1
        label = self.label_year(k, cfg)
        if label is not None:
            fy, fy_basis, end = label
            if quarter_check and self._quarter_follows(end):
                return None
            return fy, fy_basis, None, end
        if not comma and t.low in ("last", "next", "this", "current") and self.is_(k + 1, "year"):
            offset = {"last": -1, "next": 1}.get(t.low, 0)
            return None, None, offset, k + 2
        return None

    def two_digit_year(self, j: int, cfg: WranglerConfig) -> tuple[int, int] | None:
        """A two-digit year straight after a day's month, at token ``j``, and the token
        after it: touching the month ("15 MAR24"), joined by a touching mark ("15 Mar-24",
        "15-March/24", "15.Mar.24"), or after a space ("15-Mar 24"), whatever joined the day
        to the month. Not after a comma -- "on 15 Mar, 24 orders shipped" is a count -- nor
        a spaced dash, which is a span, nor before more of a date or a time. REGRESSION:
        only the day's own mark or a space would do, so "15 Mar-24" was this year's 15
        March, the year lost.

        Nor a day with its month after it, which begins the next date: REGRESSION -- "15
        March-20 of April 2024" was 15 March 2020. But after a comma, a year where the next
        date follows, as no count is: "15 Mar, 24 - 20 Apr, 24"."""
        t = self.at(j)
        if t is None:
            return None
        k = j
        if t.text in "-/." and t.tight:
            k += 1
            t = self.at(k)
            if t is None or not t.tight:
                return None
        elif t.text == "," and self.next_date_joined(j + 2):
            k += 1
            t = self.at(k)
            if t is None:
                return None
        if (
            _two_digits(t) is None or self.date_goes_on(k + 1) or self.is_(k + 1, ":")
            or self.day_of_a_month(k)
        ):
            return None
        return _pivot_year(t.low, cfg), k + 1

    def year_word(self, k: int) -> tuple[Basis, int] | None:
        """A word saying which kind of year follows, at token ``k``: "FY", "F.Y.", "fiscal
        year", "financial year", "fiscal" before a number, "CY", "C.Y.", "calendar year".
        Returns the basis and the token after the word."""
        t = self.at(k)
        if t is None:
            return None
        if t.low in ("fy", "cy"):
            j = k + 1
        elif t.low in ("f", "c") and self.is_(k + 1, ".") and self.is_(k + 2, "y"):
            j = k + 3 + (1 if self.is_(k + 3, ".") else 0)  # "F.Y.", "c.y"
        elif t.low in ("fiscal", "financial", "calendar") and self.is_(k + 1, "year"):
            j = k + 2
        elif t.low in ("fiscal", "financial") and self.number_follows(k + 1):
            j = k + 1  # "fiscal 2024", as US filings write it
        else:
            return None
        return (Basis.CALENDAR if t.low[0] == "c" else Basis.FISCAL), j

    def label_year(
        self, k: int, cfg: WranglerConfig, month_may_follow: bool = False
    ) -> tuple[int, Basis, int] | None:
        """A labelled year at token ``k`` -- "FY25", "FY 2024-25", "F.Y. 24", "fiscal year
        2024", "CY2024" -- as the year it names, its basis, and the token after it.

        Both calendar years may be written: "FY2024-25" is the fiscal year that starts in
        2024, which is not 2024 where years are named by their end. Unspaced, and not
        before a month, so "FY24 - 15 March" stays a range.
        """
        word = self.year_word(k)
        if word is None:
            return None
        basis, j = word
        while self.is_(j, "-"):  # "fy-24"
            j += 1
        if self.is_(j, "'"):
            j += 1
        t = self.at(j)
        if t is None or not t.low.isdigit() or len(t.low) not in (2, 4):
            return None
        year = _pivot_year(t.low, cfg)
        end = j + 1
        q = end + 1 + (1 if self.is_(end + 1, "'") else 0)  # the second year: "-25", "-'25"
        dash, two = self.at(end), self.at(q)
        if (
            basis is Basis.FISCAL
            and dash is not None and dash.tight and dash.text in "-/"
            and two is not None and two.tight and two.low.isdigit() and len(two.low) in (2, 4)
        ):
            after = q + 1
            month_after = self.month(after) is not None or (
                self.is_(after, ",", "of") and self.month(after + 1) is not None
            )
            if month_may_follow or not month_after:
                span = f"{t.low}-{two.low}"
                label = _fy_label_of_span(span, cfg)
                if label is None:
                    raise Declined(_not_one_fiscal_year(f"FY{span}"), q + 1)
                return label, basis, q + 1
        return year, basis, end

    def _quarter_follows(self, k: int) -> bool:
        """A quarter or half label at token ``k``, after an optional dash: "Q3", "- H1", "3Q"."""
        if self.is_(k, "-"):
            k += 1
        t, nxt = self.at(k), self.at(k + 1)
        if t is None:
            return False
        if t.low in ("q", "h") and nxt is not None and nxt.tight and nxt.low in "1234":
            return True
        return bool(re.fullmatch(r"[1-4]", t.low) and nxt is not None and nxt.tight
                    and nxt.low in ("q", "h"))

    def _split_year_follows(self, k: int) -> bool:
        dash, two, after = self.at(k + 1), self.at(k + 2), self.at(k + 3)
        return (
            dash is not None and dash.tight and dash.text in "-/"
            and two is not None and two.tight and _two_digits(two) is not None
            # Not the start of a longer date -- "2024-03-15" -- nor a range to a day or a
            # month: "15 Mar 2024-15 Apr 2024".
            and not (after is not None and after.tight and after.text in "-/.")
            and not (after is not None and MONTHS.get(after.low) is not None)
            and not (after is not None and _day(after) is not None)
        )

    def date_goes_on(self, i: int) -> bool:
        """More of a date at token ``i``: a time ("2024 14:30"), a mark and a number touching
        ("2024-03"), or a month ("2024 Apr"). Not a mark before a day that has a month of its
        own: in "15 Mar 24-20 Apr 24" the dash joins two dates."""
        t = self.at(i)
        if t is None:
            return False
        if t.text == ":" or MONTHS.get(t.low) is not None:
            return True
        after = self.at(i + 1)
        return (
            t.tight and t.text in "-/." and after is not None and after.tight
            and after.low[:1].isdigit() and not self.day_of_a_month(i + 1)
        )

    def next_date_joined(self, i: int) -> bool:
        """A word or mark joining another date at token ``i`` -- "- 20 April", "to the 20th
        of April", "and 20-Apr" -- which the date before it ends at."""
        if not self.is_(i, *_DATE_JOINS):
            return False
        j = i + 1 + (1 if self.is_(i + 1, "the") else 0)
        return self.day_of_a_month(j)

    def day_of_a_month(self, i: int) -> bool:
        """A day at token ``i`` with its month straight after it -- "20 April", "20-Apr",
        "20th of April": the next date beginning, not a year or more of the one before."""
        if self.day(i) is None:
            return False
        j = i + 1
        if self.is_(j, "-", "/", ".", tight=True) or self.is_(j, "of"):
            j += 1
        return self.month(j) is not None

    def ends_clause(self, i: int) -> bool:
        t = self.at(i)
        return t is None or t.text in ".;:!?)"

    def number_follows(self, i: int) -> bool:
        t = self.at(i)
        return t is not None and t.low[:1].isdigit()

    def dot_after_month(self, i: int) -> bool:
        """A full stop after the month at token ``i`` that is the month's: after a short
        name -- "Mar. 15", "Sept. 2013" -- or touching the number after it: "FEB.2018".
        After a full name and a space it ends a sentence. REGRESSION: "in March. 15 people
        came" was 15 March, and "in March. 2024 was good" March 2024."""
        if not self.is_(i + 1, "."):
            return False
        after = self.at(i + 2)
        return self.toks[i].low not in _FULL_MONTH_NAMES or (after is not None and after.tight)


#: Several dates read at once -- the days of a list -- each with the tokens it covers.
_Parts = list[tuple[Spec, int, int]]
#: A date read: the rule it stands in for, the spec, and the token it ends before.
_Read = tuple[str, Spec | _Parts, int]
#: A year as read after a month: the year, its basis, a year offset, and where it ends.
_YearRead = tuple[int | None, Basis | None, int | None, int]


def _year_of_day(p: _Pieces, j: int, cfg: WranglerConfig) -> _YearRead | None:
    """The year after a day's month, at token ``j``, however it is written -- ", 2024",
    "-24", " 24", "'24", "FY25", "last year" -- with its basis, a year offset, and where it
    ends. One reading for a day, a run of days and a list of them alike."""
    after = p.year_after(j, cfg)
    if after is not None:
        return after
    two = p.two_digit_year(j, cfg)
    return None if two is None else (two[0], None, None, two[1])


def _compact_day(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"15-Mar-2024", "15/Mar/2024", "15MAR2024", "13june2018", "15mar'24" -- all touching --
    and a day joined to its month by a mark, its year however it follows: "15-Mar 2024",
    "15/Mar, 2024", "15.Mar 2024", or none: "15-Mar"."""
    day = p.day(i)
    if day is None:
        return None
    j = i + 1
    sep = None
    if p.is_(j, "-", "/", ".", tight=True):
        sep, j = p.toks[j].text, j + 1
    month = p.month(j)
    if month is None or not p.toks[j].tight:
        return None
    j += 1
    k = j
    if p.is_(k, sep, tight=True) if sep is not None else p.is_(k, "'", tight=True):
        k += 1
    t = p.at(k)
    if (
        (k > j or sep is None) and t is not None and t.tight and t.low.isdigit()
        and len(t.low) in (2, 4) and not p.day_of_a_month(k)  # "15-Mar-20-Apr-2024"
    ):
        year = _pivot_year(t.low, cfg)
        return "dashed_day", Spec(Kind.ABS_DAY, year=year, month=month, day=day), k + 1
    if sep is None:
        return None
    month_end = j
    if p.is_(j, sep, tight=True) and p.day_of_a_month(j + 1):
        # "15-March-20-April-2024": the next date begins. REGRESSION: the 15th was lost,
        # and "March-20" read as March 2020.
        return "dashed_day", Spec(Kind.ABS_DAY, month=month, day=day), month_end
    if p.is_(j, sep, tight=True):
        # "15.Mar. 2024": the month's own full stop, the year after a space. Otherwise
        # "15-Mar-FY24" is not a day with something stranded after it.
        if sep != "." or p.at(j + 1) is None or p.toks[j + 1].tight:
            return None
        j += 1
    elif p.dot_after_month(j - 1):
        j += 1  # "15-Mar. 2024"
    # REGRESSION: "15-Mar 2024" was March 2024, the day lost, and "15-Mar" March alone.
    year_, basis, offset, end = _year_of_day(p, j, cfg) or (None, None, None, j)
    spec = Spec(Kind.ABS_DAY, year=year_, month=month, day=day, basis=basis, year_offset=offset)
    return "dashed_day", spec, end


def _dashed_month_day(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"Mar-15-2024", and "Mar-15 2024", "Mar-15, 2024": a year after it makes the 15 a
    day. Alone, "Mar-15" is March 2015 -- the dash settles "jan 24" as a month. A slash or
    a full stop the same: "March/15/2024", "March/15th, 2024", "March.15.24". REGRESSION:
    the first was nothing, the second three dates, and the third this year's 15 March."""
    month = p.month(i)
    if month is None or not p.is_(i + 1, "-", "/", ".", tight=True):
        return None
    mark = p.toks[i + 1].text
    day = p.day(i + 2)
    if day is None:
        return None
    t = p.at(i + 4)
    if p.is_(i + 3, mark, tight=True) and t is not None and t.tight:
        if not t.low.isdigit() or len(t.low) not in (2, 4):
            return None
        year = _pivot_year(t.low, cfg)
        return "dashed_month_day", Spec(Kind.ABS_DAY, year=year, month=month, day=day), i + 5
    if not p.toks[i + 2].tight:
        return None
    # REGRESSION: "Mar-15 2024" was March 2015, and "Mar-15, 2024" that and 2024 apart.
    after = p.year_after(i + 3, cfg)
    if after is None or after[0] is None or after[1] is not None:
        return None
    written = p.toks[after[3] - 1].low
    if not (written.isdigit() and len(written) == 4):
        return None
    return "dashed_month_day", Spec(Kind.ABS_DAY, year=after[0], month=month, day=day), after[3]


def _year_month_day(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"2024 March 15", and with one mark throughout: "2024-Mar-15", "2024/Mar/15".
    REGRESSION: "2024-Mar-15" was 2024 and March 2015; "2024/Mar/15" nothing."""
    t = p.at(i)
    if t is None or _year4(t) is None or not (1900 <= int(t.low) < 2100):
        return None
    j = i + 1
    sep = None
    if p.is_(j, "-", "/", ".", tight=True):
        sep, j = p.toks[j].text, j + 1
    month = p.month(j)
    if month is None or (sep is not None and not p.toks[j].tight):
        return None
    j += 1
    if sep is not None:
        if p.is_(j, sep, tight=True):
            j += 1
            if p.at(j) is None or not p.toks[j].tight:
                return None
        elif sep != "." or p.at(j) is None or p.toks[j].tight:
            return None  # "2024.Mar.15" reaches here as "2024.Mar 15": normalize ate the dot
    elif p.is_(j, ".", tight=True) and p.at(j + 1) is not None and p.toks[j + 1].tight:
        j += 1  # "2024Mar.15": the month's own full stop
    day = p.day(j)
    if day is None and sep is None and p.toks[j - 1].tight and not p.number_follows(j):
        # "2024March", "2024Mar": the year and its month run together. REGRESSION: nothing
        # was read, where "2024 March" is March 2024.
        return "year_month_day", Spec(Kind.ABS_MONTH, year=int(t.low), month=month), j
    if day is None or p.number_follows(j + 1) or p.is_(j + 1, ":"):
        return None
    run = _run_end(p, j + 1, between=False)
    if run is not None and run[0] >= day and not _month_follows(p, run[1]):
        # "2024 March 15-20". REGRESSION: only the 15th was read, and "2024 March 15th-20th"
        # ran from it to the 20th of this month.
        spec = Spec(Kind.ABS_MONTH, year=int(t.low), month=month, day_span=(day, run[0]))
        return "day_span", spec, run[1]
    return "year_month_day", Spec(Kind.ABS_DAY, year=int(t.low), month=month, day=day), j + 1


def _day_month_year(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"15 March 2024", "15th March", "01may 2018", "12 March, 2014", "15 Mar 24"."""
    day = p.day(i)
    month = p.month(i + 1)
    if day is None or month is None:
        return None
    # "15 Mar. 2024", "15Mar. 24": the month's own full stop. REGRESSION: it stopped the
    # year being read -- and with no year, it was left out of the date, so "15 Mar. - 20
    # Apr. 2024" was two dates. And "15 Mar 24": the day is filled, so a two-digit number
    # after the month is its year.
    j = i + 3 if p.dot_after_month(i + 1) else i + 2
    year, basis, offset, end = _year_of_day(p, j, cfg) or (None, None, None, j)
    spec = Spec(Kind.ABS_DAY, year=year, month=month, day=day, basis=basis, year_offset=offset)
    return "day_month_year", spec, end


#: Between the two days of a run: "15-20", "15 to 20", "15th through the 20th".
_RUN_JOINS = frozenset({"-", "to", "till", "until", "through", "thru"})


def _month_follows(p: _Pieces, k: int) -> bool:
    """More of a date at token ``k`` after days a month was written before -- a month of
    their own, "March 15 to 20 April", or a date going on, "March 1 to 03/04/2024"."""
    return p.date_goes_on(k) or p.is_(k, "of") and p.month(k + 1) is not None


def _backwards(first: int, last: int) -> str:
    """Why a run of days that ends before it starts is refused: "15-1 March" is no run, and
    neither end on its own is what was meant."""
    return f"a run of days from the {first} back to the {last} ends before it starts"


def _run_end(p: _Pieces, i: int, between: bool) -> tuple[int, int] | None:
    """The last day of a run whose join is at token ``i`` -- "- 20", "to the 20th", or after
    "between", "and 20" -- and the token after it."""
    if not (p.is_(i, "and") if between else p.is_(i, *_RUN_JOINS)):
        return None
    j = i + 1 + (1 if p.is_(i + 1, "the") else 0)
    last = p.day(j)
    return None if last is None else (last, j + 1)


def _days_month(p: _Pieces, i: int, cfg: WranglerConfig, between: bool = False) -> _Read | None:
    """Days running inside one month, the month written once after the last of them: "15-20
    March 2024", "15th to the 20th of March", "15-20Mar-24", "1-15 June FY25". The year is
    read exactly as a single day's is.

    REGRESSION: a pattern of its own read the year, and knew fewer ways of writing it:
    "15-20 Mar-24" was this year's March, and "15-20 June FY25" June 2025, the FY dropped.
    """
    first = p.day(i)
    if first is None:
        return None
    run = _run_end(p, i + 1, between)
    if run is None:
        return None
    last, k = run
    if p.is_(k, "of"):
        k += 1
    month = p.month(k)
    if month is None:
        return None
    j = k + 2 if p.dot_after_month(k) else k + 1
    year, basis, offset, end = _year_of_day(p, j, cfg) or (None, None, None, j)
    if last < first:
        raise Declined(_backwards(first, last), end)
    spec = Spec(
        Kind.ABS_MONTH, year=year, month=month, basis=basis, year_offset=offset,
        day_span=(first, last),
    )
    return "day_span", spec, end


def _month_days(p: _Pieces, i: int, cfg: WranglerConfig, between: bool = False) -> _Read | None:
    """The month first, then the days running inside it: "March 15-20, 2024", "Mar. 15th to
    the 20th", "March 15-20 '24". Not "March 15 to 20 April", which runs across two months
    and is the range step's, nor "March 1 to 03/04/2024", where a date of its own goes on."""
    month = p.month(i)
    if month is None:
        return None
    j = i + 2 if p.dot_after_month(i) else i + 1
    if p.is_(j, "the"):
        j += 1
    first = p.day(j)
    if first is None or p.toks[j].tight:  # "Mar-15-20" is a day of 2020
        return None
    run = _run_end(p, j + 1, between)
    if run is None:
        return None
    last, k = run
    if _month_follows(p, k):
        return None
    after = p.year_after(k, cfg, quarter_check=True)
    if after is None and (p.number_follows(k) or p.is_(k, ":")):
        return None  # "March 1-15 20" is nobody's
    year, basis, offset, end = after or (None, None, None, k)
    if last < first:
        raise Declined(_backwards(first, last), end)
    spec = Spec(
        Kind.ABS_MONTH, year=year, month=month, basis=basis, year_offset=offset,
        day_span=(first, last),
    )
    return "day_span", spec, end


def _between_days(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"between 15 and 20 March 2024", "between the 15th and the 20th of March", "between
    March 15 and 20, 2024" -- the one place "and" joins two days into a run. REGRESSION:
    with "the" before each day, only the 20th was read."""
    j = i + 1 + (1 if p.is_(i + 1, "the") else 0)
    return _days_month(p, j, cfg, between=True) or _month_days(p, j, cfg, between=True)


def _list_next(p: _Pieces, i: int) -> int | None:
    """The token after the mark or word that lists another day, at token ``i``: ",", ",
    and", ", &", "and", "&", "vs", "versus"."""
    if p.is_(i, ","):
        return i + 2 if p.is_(i + 1, "and", "&") else i + 1
    if p.is_(i, "vs"):
        return i + 2 if p.is_(i + 1, ".", tight=True) else i + 1
    return i + 1 if p.is_(i, "and", "&", "versus") else None


def _listed(p: _Pieces, j: int) -> list[int]:
    """The tokens of the days listed from token ``j``: "15, 16 and 20", "1st & 15th"."""
    days = [j]
    while (k := _list_next(p, days[-1] + 1)) is not None and p.day(k) is not None:
        days.append(k)
    return days


def _list_parts(
    p: _Pieces, days: list[int], month: int, year_read: _YearRead, sure: bool
) -> _Parts:
    """Each listed day a date of its own, in the month and year written once for all."""
    year, basis, offset, end = year_read
    return [
        (
            Spec(
                Kind.ABS_DAY, year=year, month=month, day=p.day(d), basis=basis,
                year_offset=offset, confidence=1.0 if sure else 0.8,
            ),
            d,
            end if n == len(days) - 1 else d + 1,  # the month and the year go with the last
        )
        for n, d in enumerate(days)
    ]


def _days_list(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """Days sharing the month written after the last of them: "15, 16 and 20 March 2024",
    "1st & 15th of Mar-24". Each is a date of its own, its year read as a single day's is.
    A bare first number may be a count -- "the top 3 and 15 March" -- where a suffix settles
    it. REGRESSION: a pattern of its own read the year, and "15, 16 and 20 March-2024" made
    the 20th run to the end of 2024."""
    # Not a number after a month and a comma, which is a count: "on 15 Mar, 24 and 25 more".
    if p.day(i) is None or p.is_(i - 1, ",") and p.month(i - 2) is not None:
        return None
    days = _listed(p, i)
    if len(days) < 2:
        return None
    k = days[-1] + 1
    if p.is_(k, "of"):
        k += 1
    month = p.month(k)
    if month is None:
        return None
    j = k + 2 if p.dot_after_month(k) else k + 1
    year_read = _year_of_day(p, j, cfg) or (None, None, None, j)
    parts = _list_parts(p, days, month, year_read, sure=not p.toks[i].low.isdigit())
    return "day_list", parts, year_read[3]


def _month_days_list(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """The month first, then the days: "March 1, 5 and 9, 2024", "Mar 1 & 15". A bare last
    number only before a year, a stop or the end -- in "March 1 and 15 customers" the 15 is
    a count -- and not before a month: "March 1 and 15 April" lists two dates."""
    month = p.month(i)
    if month is None:
        return None
    j = i + 2 if p.dot_after_month(i) else i + 1
    if p.day(j) is None or p.toks[j].tight:
        return None
    days = _listed(p, j)
    if len(days) < 2:
        return None
    k = days[-1] + 1
    if _month_follows(p, k):
        return None
    after = p.year_after(k, cfg, quarter_check=True)
    if after is None and p.toks[days[-1]].low.isdigit() and not (
        p.ends_clause(k) or p.is_(k, ",")
    ):
        return None
    parts = _list_parts(p, days, month, after or (None, None, None, k), True)
    parts[0] = (parts[0][0], i, parts[0][2])  # the month is written with the first day
    return "day_list", parts, parts[-1][2]


def _month_day_year(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"March 15, 2024", "March 15 2024", "March the 15th", "Jan 1st 2024", "jan 24"."""
    month = p.month(i)
    if month is None:
        return None
    j = i + 1
    if p.dot_after_month(i):  # "Mar. 15"
        j += 1
    if p.is_(j, "the"):
        t = p.at(j + 1)
        if t is None or not t.low.endswith(("st", "nd", "rd", "th")):
            return None
        j += 1
    day_tok = p.at(j)
    number = _DAY.fullmatch(day_tok.low) if day_tok is not None else None
    if day_tok is None or number is None:
        return None
    day = p.day(j)
    if day is None:
        # "jan 87": above 31, a bare two-digit number after a month can only be its year.
        if number.group(2) or len(number.group(1)) != 2:
            return None
        if p.number_follows(j + 1) or p.is_(j + 1, ":"):
            return None
        spec = Spec(Kind.ABS_MONTH, month=month, year=_pivot_year(number.group(1), cfg))
        return "month_day_year", spec, j + 1
    after = p.year_after(j + 1, cfg, quarter_check=True)
    if after is not None:
        year, basis, offset, end = after
        spec = Spec(Kind.ABS_DAY, year=year, month=month, day=day, basis=basis, year_offset=offset)
        return "month_day_year", spec, end
    hour, colon, minute = p.at(j + 1), p.at(j + 2), p.at(j + 3)
    if (
        hour is not None and hour.low.isdigit() and len(hour.low) <= 2
        and colon is not None and colon.tight and colon.text == ":"
        and minute is not None and minute.tight and minute.low.isdigit()
    ):
        # "Mar 15 14:30:00", the start of every syslog line: the day, and the time is the
        # timestamp's own -- nothing left unread.
        return "syslog", Spec(Kind.ABS_DAY, month=month, day=day), j + 1
    if p.number_follows(j + 1) or p.is_(j + 1, ":"):
        return None  # "March 1 15" is nobody's
    digits = re.sub(r"\D", "", day_tok.low)
    if day_tok.low.isdigit() and len(digits) == 2 and (
        int(digits) > 31 or cfg.month_number is MonthNumber.YEAR
    ):
        spec = Spec(Kind.ABS_MONTH, month=month, year=_pivot_year(digits, cfg))
        return "month_day_year", spec, j + 1
    return "month_day_year", Spec(Kind.ABS_DAY, month=month, day=day), j + 1


def _month_ordinal_word(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"January first, 2024" -- only before a year or the end of a clause."""
    month = p.month(i)
    if month is None:
        return None
    j = i + 1 + (1 if p.is_(i + 1, "the") else 0)
    t = p.at(j)
    if t is None or t.low not in _WORD_ORDINALS:
        return None
    after = p.year_after(j + 1, cfg)
    if after is None and not (p.ends_clause(j + 1) or p.is_(j + 1, ",") and p.ends_clause(j + 2)):
        return None
    year, _, offset, end = after if after is not None else (None, None, None, j + 1)
    spec = Spec(Kind.ABS_DAY, year=year, month=month, day=_WORD_ORDINALS[t.low], year_offset=offset)
    return "month_ordinal_word", spec, end


def _month_year(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"March 2024", "June, 2013", "March of 2024", "Mar'24", "June' 2018", "Mar-24",
    "June FY25", "March last year"."""
    month = p.month(i)
    if month is None:
        return None
    if p.is_(i + 1, "-", tight=True):
        t = p.at(i + 2)
        if t is not None and t.tight and t.low.isdigit() and len(t.low) in (2, 4):
            dashed = _pivot_year(t.low, cfg)
            return "dashed_month", Spec(Kind.ABS_MONTH, year=dashed, month=month), i + 3
        label = p.label_year(i + 2, cfg)
        if label is not None and t is not None and t.tight:  # "Jun-FY25"
            fy, fy_basis, end = label
            return "month_year", Spec(Kind.ABS_MONTH, year=fy, month=month, basis=fy_basis), end
    if p.dot_after_month(i) and p.number_follows(i + 2):  # "FEB.2018"
        i += 1
    after = p.year_after(i + 1, cfg)
    if after is None:
        return None
    year, basis, offset, j = after
    spec = Spec(Kind.ABS_MONTH, year=year, month=month, basis=basis, year_offset=offset)
    return "month_year", spec, j


def _month_alone(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    month = p.month(i)
    return None if month is None else ("month", Spec(Kind.ABS_MONTH, month=month), i + 1)


def _fy_month(p: _Pieces, i: int, cfg: WranglerConfig) -> _Read | None:
    """"FY25 June", "FY2024-25 March" -- the fiscal year first, then the month inside it --
    and the same run together or with a calendar year: "FY24March", "FY2024-25March",
    "CY2024Mar". REGRESSION: run together, the first was nothing and the second a span from
    FY2024 to the 25th of March. Not "may": in "FY25 may see growth" it is a verb."""
    label = p.label_year(i, cfg, month_may_follow=True)
    if label is None:
        return None
    year, basis, end = label
    t = p.at(end)
    month = p.month(end)
    if t is None or month is None or t.low == "may":
        return None
    if basis is Basis.CALENDAR and not t.tight:
        return None  # "CY2024 March", spaced, is the join step's, as "2024 March" is
    return "fy_month", Spec(Kind.ABS_MONTH, year=year, month=month, basis=basis), end + 1


_ReadFn = Callable[[_Pieces, int, WranglerConfig], _Read | None]

#: In priority order, by what the date opens with -- only those readers are tried. A
#: weekday in front -- "Friday, 15 March" -- is left to the join step, which already handles
#: "last Friday, 15 March" as well.
_OPENING_WITH_A_NUMBER: tuple[_ReadFn, ...] = (
    _days_month, _days_list, _compact_day, _year_month_day, _day_month_year,
)
_OPENING_WITH_A_MONTH: tuple[_ReadFn, ...] = (
    _month_days, _month_days_list, _dashed_month_day, _month_day_year, _month_ordinal_word,
    _month_year, _month_alone,
)
_OPENING_WITH_A_YEAR_WORD: tuple[_ReadFn, ...] = (_fy_month,)
_OPENING_WITH_BETWEEN: tuple[_ReadFn, ...] = (_between_days,)
#: Words a date of this family can open with, besides a number or a month.
_OPENERS = frozenset({"fy", "f", "fiscal", "financial", "cy", "c", "calendar"})


#: A date found: the rule it stands in for, its spec (None when declined, with the reason),
#: and its span in the text. A list of days is several specs, each with its own span.
Found = tuple[str, Spec | list[tuple[Spec, int, int]] | None, int, int, str | None]


class Reader:
    """The day-month-year dates of one text, asked for in order from any position.

    The scanner for every other rule and this reader take turns, leftmost first, exactly
    as one alternation would: so either has to be able to start again wherever the other
    stopped. What each token opens is worked out once and kept.
    """

    def __init__(self, text: str, cfg: WranglerConfig) -> None:
        #: Whether the text names a month at all; with none, there is nothing here to read.
        self.names_a_month = _MAY_HOLD.search(text) is not None
        self.p = _Pieces(tokenize(text) if self.names_a_month else [])
        self.cfg = cfg
        self.starts = [t.start for t in self.p.toks]
        self._opens: dict[int, Found | None] = {}

    def next_date(self, pos: int) -> Found | None:
        """The first date that starts at or after character ``pos``."""
        p = self.p
        for i in range(bisect_left(self.starts, pos), p.n):
            if i not in self._opens:
                self._opens[i] = self._open(i)
            found = self._opens[i]
            if found is not None:
                return found
        return None

    def _open(self, i: int) -> Found | None:
        p = self.p
        t = p.toks[i]
        # Only a number, a month or a fiscal-year word can open one of these dates; most
        # words are none of them.
        if t.low[0].isdigit():
            readers = _OPENING_WITH_A_NUMBER
        elif p.months[i] is not None:
            readers = _OPENING_WITH_A_MONTH
        elif t.low in _OPENERS:
            readers = _OPENING_WITH_A_YEAR_WORD
        elif t.low == "between":
            readers = _OPENING_WITH_BETWEEN
        else:
            return None
        # A word boundary, as every rule opens with: not the "123" of "abc123".
        if t.tight and i and p.toks[i - 1].low[-1:].isalnum():
            return None
        for reader in readers:
            try:
                got = reader(p, i, self.cfg)
            except Declined as no:
                return reader.__name__.lstrip("_"), None, t.start, p.toks[no.end - 1].end, str(no)
            if got is not None:
                rule, spec, end = got
                if isinstance(spec, list):
                    spans = [(s, p.toks[lo].start, p.toks[hi - 1].end) for s, lo, hi in spec]
                    return rule, spans, t.start, p.toks[end - 1].end, None
                return rule, spec, t.start, p.toks[end - 1].end, None
        return None


def read(text: str, cfg: WranglerConfig) -> list[Found]:
    """Every day, month and year date in ``text``, on its own: leftmost first, each reader
    in priority order, as the scanner does."""
    reader = Reader(text, cfg)
    out: list[Found] = []
    pos = 0
    while (found := reader.next_date(pos)) is not None:
        out.append(found)
        pos = found[3]
    return out
