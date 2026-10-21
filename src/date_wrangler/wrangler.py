"""Finding dates in text.

    prefilter -> normalise -> scan -> link -> merge ranges -> modifiers -> resolve

Ranges are assembled after scanning rather than during it, by looking at the gap between
two matches. That keeps the scanning pattern small, makes a new connector a one-line
addition, and lets an explicit year on one endpoint reach the other before either is
resolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .config import DEFAULT_CONFIG, WranglerConfig
from .normalize import Normalized, normalize
from .resolve import UnresolvableSpec, default_year_for, resolve
from .rules import RULES, Rule
from .spec import Kind, Part, Spec
from .tokens import Reader
from .types import DateMatch, DateRange, Grain, Mod, grain_rank
from .vocab import (
    DECADE_WORDS,
    FUTURE_WORDS,
    MONTH_DISPLAY,
    MONTH_NAMES,
    PAST_WORDS,
    UNIT_WORDS,
    WEEKDAY_DISPLAY,
    WEEKDAY_NAMES,
    alt,
)

__all__ = ["parse", "parse_one", "substitute", "diagnose", "Diagnostic", "YEAR_GUESSED"]


# ---------------------------------------------------------------------------
# Prefilter
# ---------------------------------------------------------------------------

#: Every rule needs a digit or one of these words, so text with neither cannot hold a
#: date. Rejects most real traffic for one cheap scan.
_TRIGGERS = (
    set(MONTH_NAMES)
    | set(UNIT_WORDS)
    | set(PAST_WORDS)
    | set(FUTURE_WORDS)
    | set(WEEKDAY_NAMES)
    | {
        "q", "qtr", "qtrs", "h", "fy", "cy", "ytd", "mtd", "qtd",
        "this", "current", "present", "today", "yesterday", "tomorrow",
        "fiscal", "financial", "calendar", "since", "until", "till", "onwards",
        "ttm", "ltm", "ending", "ended", "ends",
        # Phrases whose only trigger is the word itself: a bare "weekend" or "EOM"
        # carries no digit and no unit word, so without these it never reaches the
        # scanner at all.
        "weekend", "eom", "eoq", "eoy", "eow", "eod", "cob", "eob",
        "wk", "cw", "kw",
        "beginning", "start", "early", "mid", "middle", "late", "end", "close",
        # Read only to say why they are not answered: "an hour ago" has no other trigger.
        "hour", "hours", "hr", "hrs", "minute", "minutes", "min", "mins",
        "second", "seconds", "sec", "secs", "recently", "lately", "ago", "every",
        # "as on date", "till date": today, as statements are asked for.
        "date", "dated", "tonight",
    }
    | set(DECADE_WORDS)
)
#: Tokenising and intersecting a set beats a 116-way alternation by roughly ten times on
#: text with no date in it, which is the case that has to be cheap: the alternation retries
#: every branch at every position and only stops when one finally matches, so its cost
#: grows with the length of text it is about to reject. Splitting into words is a single
#: character-class pass, and the set does the rest.
_WORDS = re.compile(r"[a-z]+")
_DIGIT = re.compile(r"\d")


def _might_hold_a_date(text: str) -> bool:
    """Cheap rejection. Every rule needs a digit or one of the trigger words."""
    if _DIGIT.search(text) is not None:
        return True
    return not _TRIGGERS.isdisjoint(_WORDS.findall(text.lower()))


try:  # Python 3.11 moved the regex parser; it is the same module either way.
    from re import _parser as _sre  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_parse as _sre


def _first_chars(pattern: str) -> str | None:
    """A character class holding every character a match of ``pattern`` can begin with,
    or None when that cannot be narrowed down.

    Python tries the alternatives of a pattern one at a time, so at every word in the text
    each rule runs until it fails -- and a rule that opens with thirty month names fails
    slowly. A one-character lookahead in front of it fails at once. It can only be a
    superset of what the rule accepts, so it changes the speed, never the matches; a test
    holds the guarded scanner to the plain one.
    """
    found: set[str] = set()

    def walk(items: Any) -> bool | None:
        """Add the first characters of a sequence; True if it can match empty, None if
        it can start with anything."""
        for op, av in items:
            empty = step(op, av)
            if empty is None:
                return None
            if not empty:
                return False
        return True

    def step(op: Any, av: Any) -> bool | None:
        if op is _sre.LITERAL:
            found.add(re.escape(chr(av)))
            return False
        if op is _sre.IN:
            for o, a in av:
                if o is _sre.LITERAL:
                    found.add(re.escape(chr(a)))
                elif o is _sre.RANGE:
                    found.add(f"{re.escape(chr(a[0]))}-{re.escape(chr(a[1]))}")
                elif o is _sre.CATEGORY and a is _sre.CATEGORY_DIGIT:
                    found.add(r"\d")
                else:
                    return None  # \s, \w, a negated class: too wide to be worth it
            return False
        if op is _sre.SUBPATTERN:
            return walk(av[-1])
        if op is _sre.BRANCH:
            empty = False
            for branch in av[1]:
                result = walk(branch)
                if result is None:
                    return None
                empty = empty or result
            return empty
        if op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
            low, _, item = av
            result = walk(item)
            return None if result is None else (result or low == 0)
        if op in (_sre.AT, _sre.ASSERT, _sre.ASSERT_NOT):
            return True  # takes up no characters
        return None

    if walk(_sre.parse(pattern)) is not False or not found:
        return None
    return "[" + "".join(sorted(found)) + "]"


def _guarded(rule: Rule) -> str:
    assert rule.pattern is not None
    first = _first_chars(rule.pattern)
    return rule.pattern if first is None else f"(?={first}){rule.pattern}"


#: The rules read by a regular expression. The rest are read from tokens (tokens.py).
_PATTERN_RULES = [rule for rule in RULES if rule.pattern is not None]
_SCAN_PATTERN = "|".join(f"(?P<{rule.name}>{_guarded(rule)})" for rule in _PATTERN_RULES)
#: Where each rule stands in RULES: of two dates starting at the same place, the earlier
#: rule's wins, whichever of the two readers found it.
_ORDER: dict[str, int] = {rule.name: k for k, rule in enumerate(RULES)}
#: Every rule opens with \b, so it is checked once, ahead of the alternation, instead of
#: once per rule at every position. Most positions are inside a word and fail it, and they
#: now fail after one test rather than forty-eight -- about a third of scanning time, with
#: nothing able to match differently, since each branch still begins with its own \b. A
#: test holds every rule to that opening, because one without it would quietly lose matches.
_SCANNER = re.compile(rf"\b(?:{_SCAN_PATTERN})", re.IGNORECASE)
#: The same rules, case-sensitive, for the lowered-ASCII fast path in :func:`_scan`. Every
#: rule pattern is written in lower case, so the two accept exactly the same fragments.
_SCANNER_CS = re.compile(rf"\b(?:{_SCAN_PATTERN})")
#: One more branch, last, for text that names a month: where a date read from tokens opens
#: -- "March", "15 March", "15mar" -- and no rule matches. Without it the scanner, finding
#: nothing at "15 March 2024", works on through "March" and on to "2024", a year by itself,
#: which the token reader's date then swallows: searched for, found and thrown away. With
#: it the scanner stops there, and starts again after the date. Being last, it matches
#: only where no rule does, so it changes where scanning stops, never what it finds; a
#: test holds _scan to the plain scanner over every phrase the tests hold.
_HOP = rf"(?P<_hop>(?=[a-z\d])(?:\d{{1,4}}(?:st|nd|rd|th)?[ \-/.]?)?{alt(MONTH_NAMES)}(?![a-z]))"
_HOP_SCANNER = re.compile(rf"\b(?:{_SCAN_PATTERN}|{_HOP})", re.IGNORECASE)
_HOP_SCANNER_CS = re.compile(rf"\b(?:{_SCAN_PATTERN}|{_HOP})")
_RULES_BY_NAME: dict[str, Rule] = {rule.name: rule for rule in RULES}


# ---------------------------------------------------------------------------
# Connectors, comparisons and modifiers
# ---------------------------------------------------------------------------

#: A gap that joins two periods into one span.
_STRONG_LINK = re.compile(r"^\s*(?:to|through|thru|until|till|up\s*to|upto|-|–|—)\s*$", re.I)
#: "and" joins a range only when nothing suggests a list.
#: "and" joins a range only when nothing suggests a list. A relative period after it may
#: keep its article: "2024 and the year before".
_WEAK_LINK = re.compile(r"^\s*(?:and|&)\s*(?:the\s+)?$", re.IGNORECASE)
#: A comma means the writer is enumerating, not describing a span; so does "or".
#: REGRESSION: "in 2012 or 2013" was 2012 alone -- "or" linked nothing, and the second
#: year, left without a cue, was dropped.
_LIST_LINK = re.compile(
    r"^\s*(?:,\s*(?:and\s+|or\s+)?|,?\s*(?:or|and/or)\s+)(?:the\s+)?$", re.IGNORECASE
)
#: "Jan/Feb 2024" lists; "2024-03-15/2024-03-20" is an ISO interval. The ends decide.
_SLASH_LINK = re.compile(r"^\s*/\s*$")

#: Gaps that mean "these are two things being contrasted", never one range. REGRESSION:
#: "compared to the previous month" linked nothing, for the "the".
_COMPARISON_GAP = re.compile(
    r"^\s*(?:vs\.?|v\.?|versus|against|compared\s+(?:to|with)|relative\s+to|over)\s*"
    r"(?:the\s+)?$",
    re.IGNORECASE,
)
#: Text before the first period that turns even a plain "to" into a comparison.
_COMPARISON_LEAD = re.compile(
    r"\b(?:compare[ds]?|comparing|comparison|benchmark(?:ed)?|contrast)\b[^.;!?]{0,24}$", re.I
)

_MOD_PREFIXES: tuple[tuple[re.Pattern[str], Mod], ...] = (
    (re.compile(r"\bas\s+(?:of|on|at)\s+$", re.IGNORECASE), Mod.AS_OF),
    # Inclusive and negated bounds come first. Each ends in a word a plain prefix below
    # would claim on its own -- "on or after" ends in "after", "not before" in "before" --
    # and read that way the boundary day is lost ("on or after 1 April" began on the 2nd)
    # or the meaning is reversed ("not before 1 April" meant up to 31 March).
    (
        re.compile(
            r"\b(?:(?:on|in)\s+or\s+after|not\s+before|no\s+earlier\s+than"
            r"|from\s+and\s+including)\s+$",
            re.IGNORECASE,
        ),
        Mod.SINCE,
    ),
    (
        re.compile(
            r"\b(?:(?:on|in)\s+or\s+before|not\s+after|no\s+later\s+than"
            r"|up\s+to\s+and\s+including|by)\s+$",
            re.IGNORECASE,
        ),
        Mod.UNTIL,
    ),
    # "starting 2012", "beginning from Q1": REGRESSION -- the word was not read, and the
    # answer was 2012 alone rather than everything from it.
    (
        re.compile(
            r"\b(?:since|(?:starting|beginning|commencing)(?:\s+(?:from|in|on|with))?)\s+$",
            re.IGNORECASE,
        ),
        Mod.SINCE,
    ),
    (re.compile(r"\b(?:prior\s+to|earlier\s+than|before)\s+$", re.IGNORECASE), Mod.BEFORE),
    (re.compile(r"\bpre-$", re.IGNORECASE), Mod.BEFORE),
    # "through 31 March" on its own is a deadline, inclusive. Inside a range ("Jan through
    # Mar") the two ends are merged before any modifier is looked for, so this never sees it.
    (re.compile(r"\b(?:up\s*to|upto|until|till|through|thru)\s+$", re.IGNORECASE), Mod.UNTIL),
    (re.compile(r"\b(?:after|later\s+than)\s+$", re.IGNORECASE), Mod.AFTER),
    (re.compile(r"\bpost-$", re.IGNORECASE), Mod.AFTER),
)

#: "from" starts something only when what follows is a single day: "from 1 April",
#: "from Monday". After a period it more often names a source -- "the figures from Q1" are
#: Q1's figures, not everything since -- so a period after "from" is left as it is.
_FROM_PREFIX = re.compile(r"\bfrom\s+$", re.IGNORECASE)
_DAY_KINDS = frozenset({Kind.ABS_DAY, Kind.DAY_KEYWORD, Kind.WEEKDAY})


def _names_a_day(spec: Spec) -> bool:
    """Whether ``spec`` is one day: "15 March", but also "15th of March", "the 15th", "the
    last day of March", "the first Monday of March" -- a day picked out of a period.

    REGRESSION: only the first kind counted, so "from 15 March" ran onwards while "from
    15th of March" was the 15th alone.
    """
    return (
        spec.kind in _DAY_KINDS
        or spec.day_of_period is not None
        or spec.nth_weekday is not None
    )
#: "two weeks from Friday" counts on from Friday; it does not start there. A duration
#: ahead of "from" leaves the word for the safety net, which flags the phrase as partial.
_DURATION_FROM = re.compile(
    r"\b(?:a|an|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:day|week|fortnight|month|quarter|half|year)s?\s+from\s+$",
    re.IGNORECASE,
)

#: Not followed by the start of another date: a number, a quarter or half, a month, a
#: fiscal or calendar year, or a word counting from today.
_NO_DATE_AFTER = (
    rf"(?!\s+(?:the\s+)?(?:\d|[qh]\s*\d|(?:{alt(MONTH_NAMES)}|fy|cy|last|next|this|today"
    rf"|yesterday|tomorrow)\b))"
)

#: "before the end of March" is a deadline for all of March, not "before its last third".
_END_OF = re.compile(r"(?:the\s+)?end\s+of\b", re.IGNORECASE)
_MOD_SUFFIXES: tuple[tuple[re.Pattern[str], Mod | None], ...] = (
    (re.compile(r"^\s*onwards?\b", re.IGNORECASE), Mod.SINCE),
    # "2012 and later", "2012 or after": everything from it, inclusive -- but not when
    # another date follows, as in "Q1 and after Q2", where the word is that date's.
    # REGRESSION (found by Hypothesis): "1Q and since 1Q" gave the word to both quarters,
    # and the two matches overlapped.
    (
        re.compile(rf"^\s*(?:and|or)\s+(?:later|after|beyond|onwards?)\b{_NO_DATE_AFTER}", re.I),
        Mod.SINCE,
    ),
    (
        re.compile(rf"^\s*(?:and|or)\s+(?:earlier|before|prior)\b{_NO_DATE_AFTER}", re.I),
        Mod.UNTIL,
    ),
    (re.compile(r"^\s*to\s+date\b", re.IGNORECASE), None),  # handled by the to_date rule
)

#: Words that introduce a range and belong inside its span -- leaving them out makes
#: substitution ungrammatical ("between from April to September"). "the years" may follow:
#: "between the years 2010 and 2015".
_RANGE_LEAD = re.compile(
    r"\b(?:from|between|betwn|b/w)\s+(?:the\s+)?(?:(?:years?|period)\s+)?$", re.IGNORECASE
)

#: The only words that make "and" a span rather than a list: "between March and June".
#: Not "from" -- "the figures from Q1 and Q3" are each quarter's figures.
_BETWEEN_LEAD = re.compile(
    r"\b(?:between|betwn|b/w)\s+(?:the\s+)?(?:(?:years?|period)\s+)?$", re.IGNORECASE
)

#: Words that make a bare month or year read as a date rather than a noun.
_CUE = re.compile(
    r"\b(?:in|on|at|for|during|of|since|from|until|till|by|through|between|before|after|"
    r"no\s+(?:later|earlier)\s+than|"
    r"vs|versus|compared\s+(?:to|with)|"
    r"sales|revenue|profit|data|report|numbers|figures|results|performance|growth|"
    r"spend|cost|budget|forecast|actuals|"
    # Everyday text, not just reporting. Without these "Can we meet Thursday?" and
    # "Rent is due on the 1st" found nothing, which is most of how dates get written.
    r"meet|meeting|due|deadline|scheduled|schedule|booked|book|expires|expiry|effective|"
    r"dated|born|joined|starts|starting|ends|ending|arrives|arriving|departing|returning|"
    r"delivered|delivery|ships|shipping|submitted|signed|executed|commencing|"
    r"admitted|discharged|appointment|renew|renewal|payable|"
    # "the years 2012 through 2014", "for the period 2012-2014".
    r"years?|period)"
    # An article may sit between the cue and the date: "on the 15th", "in the March figures".
    r"(?:\s+the)?\W*$",
    re.IGNORECASE,
)

#: Nouns that make a *preceding* month read as a date -- "the March figures", where "the"
#: is no cue at all. Only honoured for a capitalised month, because "it may report a loss"
#: is a modal verb and "they march to the capitol" is a march.
_TRAILING_CUE = re.compile(
    r"^(?:'s)?\W*(?:sales|revenue|revenues|profit|profits|data|report|reports|numbers|figures|"
    r"results|performance|growth|spend|costs?|budget|forecast|actuals|earnings|totals?|"
    r"quarter|close|invoices?|statements?|cohort|intake|targets?|releases?|launch|"
    r"deadline|payroll|salary|rent|billing|renewals?)\b",
    re.IGNORECASE,
)

#: Titles that make whatever follows a person: "Dr. June Patel".
_NAME_TITLE = re.compile(
    r"\b(?:mr|mrs|ms|miss|dr|prof|professor|sir|madam|rev)\.?\s+$", re.IGNORECASE
)

#: Things a person does and a month does not. Deliberately excludes "is"/"was"/"will",
#: which read fine either way -- "sales in June was strong" must stay a date.
_PERSON_VERB = re.compile(
    r"^\s+(?:said|says|told|tells|asked|asks|thinks|thought|wants|wanted|joined|joins|"
    r"resigned|wrote|writes|replied|replies|emailed|emails|phoned|signed|signs|agreed|"
    r"agrees|mentioned|mentions|confirmed|confirms|approved|approves|reviewed|reviews|"
    r"suggested|suggests|recommended|recommends|complained|apologised|apologized)\b",
    re.IGNORECASE,
)

#: A capitalised word straight after a capitalised month -- "June Patel" -- unless it is
#: one of the nouns a month legitimately qualifies ("June Quarter").
_SURNAME = re.compile(r"^\s+([A-Z][a-z]+)")

#: "June's laptop" is a person; "March's figures" is a month. The noun decides.
_POSSESSIVE = re.compile(r"^'s\s+([A-Za-z]+)")

#: Nouns a month can own or qualify, so they are never surname or possessive evidence.
_MONTH_NOUNS = frozenset({
    "quarter", "month", "year", "half", "period", "results", "figures", "numbers",
    "revenue", "revenues", "sales", "report", "reports", "data", "total", "totals",
    "earnings", "close", "forecast", "budget", "actuals", "performance", "growth",
    "spend", "cost", "costs", "invoice", "invoices", "statement", "statements",
    "onwards", "quarterly", "monthly", "target", "targets", "run", "intake", "cohort",
})

#: Rules whose matches are weak enough to need a cue in "balanced" mode.
_WEAK_RULES = frozenset(
    {"month", "bare_year", "weekday", "ordinal_day", "week_bare", "quoted_year"}
)

#: In front of these, a dotted number is a version, not a date: "version 2.1.2024".
_VERSION_LEAD = re.compile(
    r"\b(?:v|ver|version|release|rel|build|rev|revision|firmware|update|patch)\.?\s*$",
    re.IGNORECASE,
)

#: Rules that only mean something joined to the date before them: "to present", "to date".
_ENDS_ONLY = frozenset({"to_now"})

#: Words that change which days a period covers. Left unread beside a match, the answer is
#: not the one the writer asked for -- "March 2024 to date" is not all of March 2024. We
#: cannot resolve every combination, but we can refuse to pretend we read it.
#: A clock time. It has to carry am/pm or a colon: a bare number beside a date is far more
#: often a day or a year, and "15 Mar 24" must not read its own year as four in the morning.
_CLOCK = (
    r"(?:\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)|\d{1,2}:\d{2}|noon|midday|midnight)"
)

_QUALIFIER_AFTER = re.compile(
    r"^\W*(?:to\s+date|ago|onwards?|or\s+(?:later|earlier)|ytd|mtd|qtd)\b"
    # "yesterday at 2pm" is a moment; the answer is a whole day. That is a wider period
    # than the one asked for, so it may not be wrong, but it is certainly not complete.
    # Only a preposition counts: a clock time sitting straight against a date is part of a
    # timestamp -- "2024-03-15T14:30:00Z", "Mar 15 14:30:00" -- where the day is the
    # documented answer and nothing was overlooked.
    rf"|^\W*(?:at|by|around|@)\s*{_CLOCK}(?!\w)"
    # A part of the day needs no preposition: "tomorrow morning" is half a day at most.
    r"|^\s+(?:morning|afternoon|evening|night|lunchtime|midday|noon)\b",
    re.IGNORECASE,
)

#: The "-25" of "Q3 2024-25", where nothing said whether 2024-25 is one fiscal year or two.
_SPLIT_YEAR_TAIL = re.compile(r"^[-/]'?\d{2}(?![\d/-])")

#: "next Monday week", "Monday week" -- British for the Monday after next. Read as the
#: weekday alone it is a week early, so a weekday followed by "week" is a partial read.
_WEEKDAY_THEN_WEEK = re.compile(r"^\s+week\b(?!\s*\d)", re.IGNORECASE)
_ENDS_IN_WEEKDAY = re.compile(rf"\b(?:{alt(WEEKDAY_NAMES)})\s*$", re.IGNORECASE)
#: "a week on Monday", "two weeks from Friday" -- a weekday pushed on by a stated count.
_WEEKS_ON = re.compile(
    r"\b(?:a|one|two|three|\d+)\s+weeks?\s+(?:on|from)\s+$", re.IGNORECASE
)
#: "the week of", "the quarter of", "week commencing" -- a period named by a date in it.
_UNIT_OF = re.compile(
    r"\b(week|month|quarter|half|year)\s+(?:of|commencing|beginning|starting)\s+(?:the\s+)?$",
    re.IGNORECASE,
)
_QUALIFIER_BEFORE = re.compile(
    r"\b(?:first\s+half|second\s+half|latter\s+half|beginning|start|early|middle|mid|late|"
    r"end|same|this\s+time|\d{1,2}(?:st|nd|rd|th))\s+(?:of\s+|in\s+)?\W*$"
    # "a year from March" is March next year, not March. Only "from now"/"from today"
    # are actually resolved, so any other tail here is a period we did not compute.
    r"|\b(?:a|an|\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:day|week|fortnight|month|quarter|half|year)s?\s+from\s+(?:the\s+)?\W*$"
    # "between 2 and 5 years ago" is a run of years; only its far end was read.
    r"|\bbetween\s+(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+and\s+$"
    # "within 30 days of 15 March": a window around the date, either side of it.
    r"|\bwithin\s+(?:a|an|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:day|week|fortnight|month|quarter|year)s?\s+of\s+(?:the\s+)?$"
    # The same clock time, on the other side: "at 3pm on Tuesday", "between 2pm and 4pm
    # yesterday".
    rf"|\b(?:at|by|around)\s+{_CLOCK}(?:\s+(?:on|of))?\s*\W*$"
    # Two clocks first, so "between 2pm and 4pm yesterday" reports the window it dropped
    # rather than just the "4pm" nearest the match. Leftmost wins, so order is enough.
    rf"|\b{_CLOCK}\s*(?:and|to|till|until|-)\s*{_CLOCK}\s*\W*$"
    rf"|\b{_CLOCK}\s*\W*$",
    re.IGNORECASE,
)


#: An nth-of phrase whose period went unread: "last day of term", "the last Friday of her
#: career", "last 3 months of the year". The rules that cover the targets we *can* read run
#: first, so anything still shaped like this had its target dropped -- and the answer is
#: then a different date, not a rounder one. "last day of term" resolves to yesterday.
_NTH_LEAD = re.compile(
    r"^(?:the\s+)?(?:last|first|final|second|third|fourth|fifth|"
    r"\d{1,2}(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)
#: Takes a word or two past the preposition, so the diagnostic can name what it dropped --
#: "of her career" rather than "of h".
_NTH_TAIL = re.compile(
    r"^\W*(?:of|in)\s+(?:the\s+)?[a-z]+(?:\s+[a-z]+)?", re.IGNORECASE
)


def _looks_like_a_person(text: str, start: int, end: int) -> bool:
    """Whether a month word is being used as somebody's name.

    Four signals, each on its own enough: a title in front, a surname behind, a possessive
    over a noun no month owns, or a verb only people perform. None of them needs a name
    list, which is the point -- June, May, April and August are all common names and no
    list would ever be complete.

    This is shape, not meaning: "June saw record sales" is genuinely ambiguous and stays a
    month. The aim is only to stop the confident errors.
    """
    before = text[max(0, start - 24) : start]
    after = text[end : end + 24]
    if _NAME_TITLE.search(before) or _PERSON_VERB.match(after):
        return True
    if text[start].isupper():
        surname = _SURNAME.match(after)
        if surname and surname.group(1).lower() not in _MONTH_NOUNS:
            return True
    owned = _POSSESSIVE.match(after)
    return bool(owned and owned.group(1).lower() not in _MONTH_NOUNS)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A fragment that looked like a date but could not be resolved."""

    text: str
    span: tuple[int, int]
    rule: str
    reason: str


@dataclass(slots=True)
class _Raw:
    """A scanned fragment, before linking and resolution."""

    rule: str
    spec: Spec
    start: int
    end: int


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _scan(text: str, cfg: WranglerConfig, diags: list[Diagnostic] | None) -> list[_Raw]:
    """Find every date fragment in ``text``, left to right.

    Two readers take turns: one regular expression over the rules that have a pattern, and
    the token reader (tokens.py) for the dates written with month names. Whichever finds
    the next date first takes it, and of two starting at the same place the one whose rule
    comes first in RULES -- exactly what one alternation over every rule does.

    Scanning is where most of the time goes, and IGNORECASE over an alternation this size
    costs roughly half as much again as a plain match -- so ASCII text is lowered once and
    matched case-sensitively instead. Offsets survive because lowering ASCII cannot change
    a string's length; anything else falls back to the case-insensitive pattern, where that
    guarantee does not hold. Either way the *fragment* is sliced from the original, so the
    rules still see the writer's capitals.

    Where the text names a month, the scanner has one more branch, ``_hop``: it stops where
    a date read from tokens opens and no rule matches, rather than working on through it.
    A hop at or after the start of the reader's next date means no rule matches before the
    hop, so that date comes first: it is taken, and scanning resumes after it. A hop with
    no reader's date at or before it means only that no rule matches there, and scanning
    goes on from the next character.
    """
    reader = Reader(text, cfg)
    if text.isascii():
        target = text.lower()
        scanner = _HOP_SCANNER_CS if reader.names_a_month else _SCANNER_CS
    else:
        target, scanner = text, _HOP_SCANNER if reader.names_a_month else _SCANNER
    out: list[_Raw] = []
    pos = 0
    m = scanner.search(target, pos)
    d = reader.next_date(pos)
    while m is not None or d is not None:
        if m is not None and m.lastgroup == "_hop" and (d is None or d[2] > m.start()):
            m = scanner.search(target, m.start() + 1)
            continue
        if d is not None and (
            m is None
            or d[2] < m.start()
            or (
                d[2] == m.start()
                and (m.lastgroup == "_hop" or _ORDER[d[0]] < _ORDER[m.lastgroup or ""])
            )
        ):
            name, spec, lo, hi, reason = d
            if spec is not None:
                out.append(_Raw(name, spec, lo, hi))
            elif diags is not None:
                diags.append(Diagnostic(text[lo:hi], (lo, hi), name, reason or ""))
            pos = hi
        else:
            assert m is not None
            pos = m.end()
            _read_match(m, text, cfg, out, diags)
        if m is not None and m.start() < pos:
            m = scanner.search(target, pos)
        if d is not None and d[2] < pos:
            d = reader.next_date(pos)
    return out


#: What a rule read from a fragment: (True, what it read), or (False, why it refused).
_Reading = tuple[bool, Any]
#: Readings already made, per configuration. "Q1", "FY24" and "Q3 2024" recur from text to
#: text, and reading one again cost as much as the first time: a seventh to a quarter of
#: parse(). Keyed on the configuration object, which each entry holds, so its id cannot
#: pass to another while the entry lives. Bounded both ways, since a caller may make a
#: configuration per request; when full, an entry is emptied rather than grown.
_READINGS: dict[int, tuple[WranglerConfig, dict[tuple[str, str], _Reading]]] = {}
_MAX_CONFIGS = 8
_MAX_READINGS = 4096


def _reading(rule: Rule, fragment: str, cfg: WranglerConfig) -> _Reading:
    """``rule``'s reading of ``fragment``, made once per configuration."""
    entry = _READINGS.get(id(cfg))
    if entry is None or entry[0] is not cfg:
        if len(_READINGS) >= _MAX_CONFIGS:
            _READINGS.clear()
        entry = _READINGS[id(cfg)] = (cfg, {})
    readings = entry[1]
    key = (rule.name, fragment)
    found = readings.get(key)
    if found is None:
        assert rule.parse is not None
        try:
            found = (True, rule.parse(fragment, cfg))
        except (ValueError, KeyError) as exc:
            found = (False, str(exc))
        if len(readings) >= _MAX_READINGS:
            readings.clear()
        readings[key] = found
    return found


def _read_match(
    m: re.Match[str],
    text: str,
    cfg: WranglerConfig,
    out: list[_Raw],
    diags: list[Diagnostic] | None,
) -> None:
    """Hand one regular-expression match to its rule, and keep what it reads."""
    name = m.lastgroup
    rule = _RULES_BY_NAME.get(name) if name is not None else None
    if name is None or rule is None or rule.parse is None:
        return
    fragment = text[m.start() : m.end()]
    read, spec = _reading(rule, fragment, cfg)
    if not read:
        # The rule said why; that reason alone, not a second, vaguer one beside it.
        if diags is not None:
            diags.append(Diagnostic(fragment, m.span(), name, spec))
        return
    if spec is None:
        if diags is not None:
            diags.append(Diagnostic(fragment, m.span(), name, "matched but could not be read"))
        return
    if isinstance(spec, list):
        # Several dates in one fragment -- "1st and 15th March" -- each with its own span,
        # so they link as a list like any other.
        out.extend(_Raw(name, s, m.start() + lo, m.start() + hi) for s, lo, hi in spec)
        return
    out.append(_Raw(name, spec, m.start(), m.end()))


def _passes_strictness(
    raw: _Raw, text: str, cfg: WranglerConfig, chain_start: int, chain_end: int
) -> bool:
    """Whether a weak match survives the configured strictness.

    Bare month names in prose -- "the march on Washington" -- are the dominant false
    positive here, and no amount of extra patterns fixes it; a nearby cue does.

    The cue is looked for before the whole chain so "from Jan to Mar" passes on its
    "from". Being joined is not enough on its own: "a may-december romance" is two month
    names either side of a dash.

    A name beats every cue. "sales by June Patel" has a perfectly good cue in "by", and is
    still a person, so :func:`_looks_like_a_person` is checked first and vetoes outright.
    """
    if raw.rule == "numeric" and _VERSION_LEAD.search(text[max(0, raw.start - 16) : raw.start]):
        return False
    if cfg.strictness == "greedy" or raw.rule not in _WEAK_RULES:
        return True
    if cfg.strictness == "strict":
        return False
    if raw.rule == "month" and _looks_like_a_person(text, raw.start, raw.end):
        return False
    if chain_start == 0 and chain_end >= len(text.rstrip()):
        return True  # the whole input is the date
    before = text[max(0, chain_start - 32) : chain_start]
    if _CUE.search(before):
        return True
    # A word that bounds the period is a cue too: "prior to 2012", "2012 onwards".
    # REGRESSION: both were read, then dropped, because a bare year needs a cue.
    if any(p.search(before) for p, _ in _MOD_PREFIXES) or any(
        mod is not None and p.match(text[chain_end : chain_end + 24]) for p, mod in _MOD_SUFFIXES
    ):
        return True
    # "the March figures", "the 1998's figures" -- no cue in front, but the noun behind
    # settles it. Capitalised only, so "it may report a loss" stays a modal verb.
    first = text[chain_start]
    return bool((first.isupper() or first.isdigit()) and _TRAILING_CUE.match(text[chain_end:]))


# ---------------------------------------------------------------------------
# Fragments that are one date
# ---------------------------------------------------------------------------

#: Between a period and the year that is its own: "June, 2013", "Q1, 2024".
_COMMA_GAP = re.compile(r"^\s*,\s*$")
#: Between a weekday and the date it names: "Friday, 15 March", "Friday 15 March".
_WEEKDAY_GAP = re.compile(r"^\s*,?\s*$")
#: Before "till date", "to present": "01/06/2018 to till date", "10th June 2018-to till date".
_TILL_GAP = re.compile(r"^\s*(?:-\s*)?(?:to\s*)?$", re.IGNORECASE)
#: Between a count and the date it counts from: "3 days before 15 March", "prior to".
_FROM_DATE_GAP = re.compile(r"^\s*(?:to\s+)?$", re.IGNORECASE)
#: A count that points at a date after it: "3 days before", "a year after".
_COUNTS_FROM = re.compile(r"\b(?:before|after|prior|earlier|later)\s*$", re.IGNORECASE)
_OF_GAP = re.compile(r"^\s+of\s+(?:the\s+)?$", re.IGNORECASE)
_TILL_RULES = frozenset({"till_now", "to_now"})
#: Between a quarter, half or month and the year after it: "Q3 in 2024", "H1-FY25",
#: "Q3/FY24", "June/2024". A dash or slash only touching both: "June - FY25" is how a range
#: is written, from June to the end of FY25.
_LABEL_YEAR_GAP = re.compile(r"^(?:[-/]|\s*|\s+(?:in|of)\s+(?:the\s+)?)$", re.IGNORECASE)
#: Between a year and the quarter, half or month after it: "FY 2024 Qtr 3", "2024 - third
#: quarter", "2024 March". Not a comma, which may list them: "2023, Q3 and Q4".
_YEAR_LABEL_GAP = re.compile(r"^\s*(?:[-/]\s*)?$")
_FULL_MONTH_NAMES = frozenset(name.lower() for name in MONTH_DISPLAY)


def _join_one_date(raws: list[_Raw], text: str, cfg: WranglerConfig) -> list[_Raw]:
    """Join fragments the scanner found apart that name one date between them.

    - A year after a comma is the year of the period before it: "June, 2013", "Q1, 2024".
      REGRESSION: the year came back as a second period, so "in June, 2013" was June 2013
      *and* all of 2013, and a question about one month became a comparison of two.
    - A quarter, half or month and a year beside it are one period: "Q3 in 2024", "1H-FY24",
      "Q3/FY24", "FY 2024 Qtr 3", "2024 - third quarter", "June/2024", "Mar. FY24", "2024
      March". The scanner finds both; they came back as two periods, or as a span from
      this year's quarter to the whole year. A spaced dash after the label is still a
      span -- "June - FY25" -- and one before it is not: "FY24 - Q3" is a label.
    - A weekday belongs to the date it introduces: "Friday, 15 March 2024". It was read as
      this week's Friday, a second and wrong date.
    - "till date", "to present" end the date before them: "01/06/2018 to till date".
    - A count before a date counts from it: "3 days before 15 March", not from today.
    """
    out: list[_Raw] = []
    for raw in raws:
        joined = _joined(out[-1], raw, text, cfg) if out else None
        if joined is not None:
            out[-1] = joined
        else:
            out.append(raw)
    kept: list[_Raw] = []
    for raw in out:
        if raw.rule in _ENDS_ONLY:
            continue  # "to present" with no date before it: "we want to present the results"
        if raw.rule == "quoted_year":
            raw = _Raw(raw.rule, raw.spec, raw.start - 1, raw.end)  # take the apostrophe
        kept.append(raw)
    return kept


def _joined(prev: _Raw, raw: _Raw, text: str, cfg: WranglerConfig) -> _Raw | None:
    """``prev`` and ``raw`` as one fragment, or None if they are two."""
    a, b = prev.spec, raw.spec
    gap = text[prev.end : raw.start]
    if gap.startswith(".") and _is_abbreviated_month(prev, text):
        gap = gap[1:]  # "Mar. FY24", "Mar., FY24"; but "in March. 2024 was" is two sentences
    if _COMMA_GAP.match(gap) and _takes_a_year(a) and _is_a_year(b):
        basis = a.basis if a.basis is not None else b.basis
        return _Raw("comma_year", a.with_(year=b.year, basis=basis), prev.start, raw.end)
    # A year cut in two on a calendar basis -- "2024-25" as 2024 to 2025 -- is not one year.
    if prev.rule != "split_year" and raw.rule != "split_year":
        if _is_a_label(prev, text) and _is_a_year(b) and _LABEL_YEAR_GAP.match(gap):
            basis = a.basis if a.basis is not None else b.basis
            # "Q1 in 2024" reads as "Q1 of 2024". Not a month: a plain year beside one is
            # the calendar year it falls in, whatever the basis -- "June in 2025" is June 2025.
            if basis is None and a.kind is not Kind.ABS_MONTH and gap.strip()[:2].lower() in (
                "in", "of"
            ):
                basis = cfg.effective_of_year_basis
            return _Raw("label_year", a.with_(year=b.year, basis=basis), prev.start, raw.end)
        if _is_a_year(a) and _is_a_label(raw, text) and _YEAR_LABEL_GAP.match(gap):
            basis = b.basis if b.basis is not None else a.basis
            return _Raw("label_year", b.with_(year=a.year, basis=basis), prev.start, raw.end)
    if (
        a.kind is Kind.WEEKDAY
        and a.weekday_span is None
        and _WEEKDAY_GAP.match(gap)
        and _names_a_day(b)
        and not b.is_relative
    ):
        return _Raw(raw.rule, b.with_(weekday=a.index), prev.start, raw.end)
    if raw.rule in _TILL_RULES and prev.rule not in _TILL_RULES and _TILL_GAP.match(gap):
        return _Raw(prev.rule, a.with_(through_today=True), prev.start, raw.end)
    if (
        prev.rule == "in_future"
        and text[prev.start : prev.end].lower().startswith("within")
        and _OF_GAP.match(gap)
        and not b.is_relative
    ):
        # "within 30 days of 15 March": either side of the date, or only after it? Kept as
        # the date, flagged -- and not as thirty days from today.
        return _Raw("within_of", b, prev.start, raw.end)
    if (
        a.kind is Kind.AGO
        and not a.business
        and a.unit is not None
        and _FROM_DATE_GAP.match(gap)
        and _COUNTS_FROM.search(text[prev.start : prev.end])
        and not b.is_relative
    ):
        rule = "shift_the" if prev.rule == "unit_before" else "shift"
        shift = (a.direction * (a.count or 1), a.unit)
        return _Raw(rule, b.with_(shift=shift), prev.start, raw.end)
    return None


def _takes_a_year(spec: Spec) -> bool:
    """A period that names no year and could have one: "June", "Q1", "the end of March"."""
    return (
        spec.year is None
        and spec.year_offset is None
        and not spec.is_relative
        and spec.kind
        in (Kind.ABS_DAY, Kind.ABS_MONTH, Kind.ABS_QUARTER, Kind.ABS_HALF, Kind.PERIOD_ENDING)
    )


def _is_a_label(raw: _Raw, text: str) -> bool:
    """A quarter, half or month that names no year: "Q3", "1H", "fiscal Q3", "March". Not
    a lower-case "may", which is the verb: "2024 may see growth"."""
    spec = raw.spec
    if spec.kind is Kind.ABS_MONTH:
        return _takes_a_year(spec) and text[raw.start : raw.end] != "may"
    return spec.kind in (Kind.ABS_QUARTER, Kind.ABS_HALF) and _takes_a_year(spec)


def _is_abbreviated_month(raw: _Raw, text: str) -> bool:
    """A month written short, so a full stop after it is the abbreviation's: "Mar.", "Sept."."""
    word = text[raw.start : raw.end].lower()
    return raw.spec.kind is Kind.ABS_MONTH and word not in _FULL_MONTH_NAMES and word.isalpha()


def _is_a_year(spec: Spec) -> bool:
    """A year and nothing more: "2013", "FY25", "CY2024"."""
    return (
        spec.kind is Kind.ABS_YEAR
        and spec.year is not None
        and spec.part is None
        and spec.sub_period is None
        and spec.day_of_period is None
        and spec.nth_weekday is None
        and spec.within is None
        and not spec.through_today
    )


# ---------------------------------------------------------------------------
# Linking adjacent matches
# ---------------------------------------------------------------------------


def _link_kinds(raws: list[_Raw], text: str) -> list[str | None]:
    """Classify the gap between each adjacent pair: range, list, comparison or nothing."""
    links: list[str | None] = []
    for a, b in zip(raws, raws[1:], strict=False):
        gap = text[a.end : b.start]
        if a.rule == b.rule == "split_year" and gap.strip() in ("-", "/"):
            links.append("range")  # "2024-25" on a calendar basis: 2024 to 2025
        elif _COMPARISON_GAP.match(gap):
            links.append("compare")
        elif _SLASH_LINK.match(gap):
            # Two dated days either side of a slash are an ISO 8601 interval.
            links.append("range" if _is_dated_day(a.spec) and _is_dated_day(b.spec) else "list")
        elif _LIST_LINK.match(gap):
            links.append("list")
        elif _STRONG_LINK.match(gap) or _WEAK_LINK.match(gap):
            lead = text[max(0, a.start - 40) : a.start]
            opened = lead.rfind("(")
            if opened > lead.rfind(")"):
                # Inside brackets, a "compare" outside them does not reach in: in "compare
                # Q1 2024 (January 2024 to March 2024) to Q1 2025" the bracket is one span.
                lead = lead[opened + 1 :]
            if _COMPARISON_LEAD.search(lead):
                links.append("compare")
            elif _WEAK_LINK.match(gap) and not _BETWEEN_LEAD.search(lead[-24:]):
                # "and" lists periods; only "between ... and" makes a span. "Q1 and Q3" is
                # two quarters -- one range for both would hold Q2, which nobody named.
                links.append("and")
            else:
                links.append("range")
        else:
            links.append(None)
    return links


def _demote_lists(links: list[str | None]) -> list[str | None]:
    """A comma anywhere in a run makes every "and" in it a list too.

    "Q1, Q2 and Q3" is three quarters. A span stays a span: "2015 to 2016, 2016 to 2017"
    is two spans. REGRESSION: the comma turned each "to" into a list as well, and the
    answer was six separate years.
    """
    out = list(links)
    i = 0
    while i < len(out):
        if out[i] is None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is not None:
            j += 1
        run = out[i:j]
        if "list" in run:
            for k in range(i, j):
                if out[k] == "and":
                    out[k] = "list"
        i = j
    return out


def _propagate_year(raws: list[_Raw], links: list[str | None]) -> None:
    """Share one stated year across a list, or across a comparison.

    "Jan, Feb, Mar 2024" is three months of the same year, and comparing "Q1 versus
    Q2 2024" across two different years defeats the point. The year's basis travels with
    it: in "Q1, Q2 and Q3 of FY25" all three are fiscal quarters, not just the last.

    A list may hold spans of its own -- "Jan to Mar and Jul to Sep 2024" -- so the year
    reaches every period in it, spans included.

    Each side of a comparison shares its own year first, and only a side with none left
    takes one from across it. REGRESSION: "Q1 and Q3 2024 vs 2023" gave Q1 the 2023 on the
    far side of the "vs", when 2024 was written beside it.
    """
    for lo, hi in _runs(links, lambda k: k is not None and k != "compare"):
        if any(k in ("and", "list") for k in links[lo:hi]):
            _share_year(raws[lo : hi + 1])
    for lo, hi in _runs(links, lambda k: k is not None):
        if "compare" in links[lo:hi]:
            _share_year(raws[lo : hi + 1])


def _runs(
    links: list[str | None], linked: Callable[[str | None], bool]
) -> Iterator[tuple[int, int]]:
    """Each maximal run of links passing ``linked``, as ``(first, past_last)`` link indices;
    the run joins the matches ``first`` to ``past_last`` inclusive."""
    i = 0
    while i < len(links):
        if not linked(links[i]):
            i += 1
            continue
        j = i
        while j < len(links) and linked(links[j]):
            j += 1
        yield i, j
        i = j


def _share_year(members: list[_Raw]) -> None:
    """Give the last year stated among ``members``, and its basis, to those with none."""
    stated = [r.spec for r in members if r.spec.year is not None]
    if not stated:
        return
    year, basis = stated[-1].year, stated[-1].basis
    for r in members:
        if r.spec.year is None and not r.spec.is_relative:
            r.spec = r.spec.with_(year=year)
            if r.spec.basis is None and basis is not None:
                r.spec = r.spec.with_(basis=basis)


def _unify(a: Spec, b: Spec) -> tuple[Spec, Spec]:
    """Make both endpoints agree on year and basis before resolving.

    Otherwise "Q1 to Q2 of 2024" resolves one end fiscally, the other on the calendar.

    A month or day with a plain year of its own keeps the calendar: "March 2024" is March
    2024, and joining it to "FY24" must not turn it into the March of fiscal 2024.
    """
    a_dated, b_dated = _is_dated_month(a), _is_dated_month(b)
    if not a.is_relative and not b.is_relative:
        if a.year is None and b.year is not None:
            a = a.with_(year=b.year)
        elif b.year is None and a.year is not None:
            b = b.with_(year=a.year)
    basis = a.basis if a.basis is not None else b.basis
    if basis is not None:
        if a.basis is None and not a_dated:
            a = a.with_(basis=basis)
        if b.basis is None and not b_dated:
            b = b.with_(basis=basis)
    return a, b


def _is_dated_month(spec: Spec) -> bool:
    """A month or day that names its own year: a calendar fact, whatever it is joined to."""
    return spec.kind in (Kind.ABS_MONTH, Kind.ABS_DAY) and spec.year is not None


def _is_dated_day(spec: Spec) -> bool:
    return spec.kind is Kind.ABS_DAY and spec.year is not None


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------


def _apply_modifier(raw: _Raw, text: str, floor: int = 0) -> _Raw:
    """Attach a leading or trailing modifier, extending the span over it.

    ``floor`` is where the previous match ended. A prefix may not reach back past it, or
    two matches end up claiming the same words: in "0 MONTH BEFORE 1Q" the "before"
    belongs to the count on its left, and 1Q must not swallow it as a modifier.
    """
    for pattern, mod in _MOD_SUFFIXES:
        if mod is not None and pattern.match(text[raw.end : raw.end + 24]):
            m = pattern.match(text[raw.end : raw.end + 24])
            assert m is not None
            return _Raw(raw.rule, raw.spec.with_(mod=mod), raw.start, raw.end + m.end())
    window = max(floor, raw.start - 28)
    before = text[window : raw.start]
    # Past a "the": "since the last quarter" is open-ended. REGRESSION: the article hid the
    # prefix, and the answer was the closed quarter at full confidence.
    article = _ARTICLE.search(before) if "the" in before.lower() else None
    if article is not None:
        before = before[: article.start()]
    for pattern, mod in _MOD_PREFIXES:
        m = pattern.search(before)
        if m:
            spec = raw.spec.with_(mod=mod)
            # "before / by / until the end of March" means by the close of March. Read
            # literally it was "before the last third of March", which ended on the 20th.
            if (
                mod in (Mod.BEFORE, Mod.UNTIL)
                and spec.part is Part.LATE
                and _END_OF.match(text[raw.start : raw.end])
            ):
                spec = spec.with_(part=None, mod=Mod.UNTIL)
            return _Raw(raw.rule, spec, window + m.start(), raw.end)
    m = _FROM_PREFIX.search(before)
    if (
        m
        and _names_a_day(raw.spec)
        and raw.spec.mod is None
        and not _DURATION_FROM.search(before)
    ):
        return _Raw(raw.rule, raw.spec.with_(mod=Mod.SINCE), window + m.start(), raw.end)
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_today(today: date | datetime | None, tz: tzinfo | None) -> date:
    """Work out which day "today" is.

    Every relative phrase is measured from one day, so getting it wrong is wrong
    everywhere at once -- a UTC server answering a reader in Asia/Kolkata just after local
    midnight is still on yesterday, and "this month" quietly returns last month.

    Three ways to say it, least to most left to chance: an explicit ``today``, a ``tz``
    (the date *there*), or neither (this machine's local date). A ``datetime`` works
    wherever a ``date`` does, and with ``tz`` it converts first.
    """
    if today is None:
        # datetime.now(None) is the naive local clock.
        return datetime.now(tz).date()
    # datetime subclasses date, so test it first.
    if isinstance(today, datetime):
        if tz is not None:
            if today.tzinfo is None:
                raise ValueError(
                    "cannot convert a naive datetime to tz; attach a tzinfo to `today` "
                    "or pass a plain date"
                )
            today = today.astimezone(tz)
        return today.date()
    if isinstance(today, date):
        return today
    raise TypeError(f"today must be a date, datetime or None, got {type(today).__name__}")


def parse(
    text: str,
    *,
    today: date | datetime | None = None,
    tz: tzinfo | None = None,
    config: WranglerConfig = DEFAULT_CONFIG,
    diagnostics: list[Diagnostic] | None = None,
) -> list[DateMatch]:
    """Find every date expression in ``text``.

    Spans index ``text`` as given, so you can highlight or replace without searching
    again. Pass a list as ``diagnostics`` to collect fragments that looked like dates but
    would not resolve -- that is what tells "nothing there" from "could not read it".
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    if not text or not _might_hold_a_date(text):
        return []

    day = _resolve_today(today, tz)
    norm = normalize(text)
    body = norm.text

    mark = len(diagnostics) if diagnostics is not None else 0
    raws = _join_one_date(_scan(body, config, diagnostics), body, config)
    if not raws:
        _diagnostics_to_original(diagnostics, mark, norm)
        return []

    links = _demote_lists(_link_kinds(raws, body))
    _propagate_year(raws, links)
    anchors = _anchors(raws, links, body, day, config)
    raws_ok = []
    for idx, raw in enumerate(raws):
        if raw.rule == "unit_before" and idx not in anchors:
            # "the year before the merger": before what, it does not say.
            if diagnostics is not None:
                diagnostics.append(
                    Diagnostic(
                        body[raw.start : raw.end], (raw.start, raw.end), raw.rule,
                        f"{body[raw.start : raw.end]!r} counts from a date, and none is "
                        "given beside it",
                    )
                )
            raws_ok.append(False)
        else:
            raws_ok.append(True)

    # Group linked matches into chains, so strictness judges the chain as a whole. A chain
    # is also what the caller sees as a group: the dates named together.
    chain_of: dict[int, tuple[int, int]] = {}
    run_of: dict[int, int] = {}
    i = 0
    while i < len(raws):
        j = i
        while j < len(links) and links[j] is not None:
            j += 1
        for k in range(i, j + 1):
            chain_of[k] = (raws[i].start, raws[j].end)
            run_of[k] = i
        i = j + 1

    kept = {
        idx
        for idx, raw in enumerate(raws)
        if raws_ok[idx] and _passes_strictness(raw, body, config, *chain_of[idx])
    }
    # A weak date inside a span, between two dates that stand -- the March of "Q1 to March
    # to Q3" -- is no more on its own than the span is. REGRESSION (found by Hypothesis):
    # dropped for want of a cue, or by strict mode, it cut the span in two, and strictness
    # found more dates than greedy did. A name stays dropped: it is not a date at all.
    for lo in sorted(kept):
        hi = lo + 1
        while (
            hi < len(raws)
            and hi not in kept
            and links[hi - 1] == "range"
            and raws_ok[hi]
            and raws[hi].rule in _WEAK_RULES
            and not (
                raws[hi].rule == "month"
                and _looks_like_a_person(body, raws[hi].start, raws[hi].end)
            )
        ):
            hi += 1
        if hi > lo + 1 and hi in kept and links[hi - 1] == "range":
            kept.update(range(lo + 1, hi))

    matches: list[DateMatch] = []
    floor = 0  # where the previous match ended, so modifiers cannot reach back over it
    started: set[int] = set()  # groups that have a match already
    groups: list[tuple[int, str | None]] = []  # each match's group, and its link back

    def placed(m: DateMatch, i: int) -> DateMatch:
        run = run_of[i]
        link = links[i - 1] if i else None
        groups.append((run, _JOINED_BY.get(link) if run in started and link else None))
        started.add(run)
        return m

    i = 0
    while i < len(raws):
        if i not in kept:
            i += 1
            continue
        j = i
        while j < len(links) and links[j] == "range" and (j + 1) in kept:
            j += 1
        if j > i:
            merged = _merge(raws[i : j + 1], day, config, body, norm, diagnostics, floor)
            if merged is not None:
                matches.append(placed(merged, i))
                floor = raws[j].end
                i = j + 1
                continue
        single = _single(raws[i], anchors.get(i, day), config, body, norm, diagnostics, floor)
        if single is not None:
            matches.append(placed(single, i))
            floor = max(floor, raws[i].end)
        i += 1
    _diagnostics_to_original(diagnostics, mark, norm)
    found = _take_article(_flag_unread_qualifiers(matches, body, norm, diagnostics), norm.original)
    return _number_groups(found, groups)


#: The link before a match, as the caller sees it. "and" and a comma both list.
_JOINED_BY = {"and": "list", "list": "list", "compare": "compare", "range": "range"}

#: A relative period that counts from whatever was just named: "the previous month", "the
#: same quarter last year", "prior year", "the year before".
_COUNTS_FROM_NAMED = re.compile(
    r"^(?:the\s+)?(?:previous|prior|preceding|same)\b"
    r"|^(?:the\s+)?(?:day|week|fortnight|month|quarter|half|year)\s+(?:before|after)\b",
    re.IGNORECASE,
)


def _anchors(
    raws: list[_Raw], links: list[str | None], text: str, day: date, cfg: WranglerConfig
) -> dict[int, date]:
    """The day each relative period counts from, when it is not today.

    "March 2024 compared to the previous month" is February 2024, not the month before
    today; "2024 vs prior year" is 2023. REGRESSION: both counted from today, so the
    comparison was against a period nobody named.
    """
    out: dict[int, date] = {}
    for k, link in enumerate(links):
        if link is None or link == "range":
            continue
        a, b = raws[k], raws[k + 1]
        if a.spec.is_relative or not b.spec.is_relative:
            continue
        if not _COUNTS_FROM_NAMED.match(text[b.start : b.end]):
            continue
        try:
            start = resolve(a.spec, day, cfg).start
        except UnresolvableSpec:
            continue
        if start is not None:
            out[k + 1] = start
    return out


def _number_groups(
    matches: list[DateMatch], groups: list[tuple[int, str | None]]
) -> list[DateMatch]:
    """Give each match its group, numbered 0, 1, 2... in the order they appear.

    Inside :func:`parse` a group is known by the index of its first scanned fragment, which
    counts fragments that never became a match. The caller should not see that. Most text
    holds one date, already in group 0, so most matches are returned as they are.
    """
    number: dict[int, int] = {}
    out: list[DateMatch] = []
    for m, (run, joined) in zip(matches, groups, strict=True):
        n = number.setdefault(run, len(number))
        out.append(m if n == 0 and joined is None else replace(m, group=n, joined_by=joined))
    return out


_ARTICLE = re.compile(r"(?<![\w'])the\s+$", re.IGNORECASE)
#: Matches that can describe a noun -- "the March figures", "the Q1 numbers", "the 15 March
#: meeting" -- and so leave "the" to the noun. An ordinal is not one: "the 15th of March".
_DESCRIBES = re.compile(
    rf"(?:{alt(MONTH_NAMES)}|{alt(WEEKDAY_NAMES)}|today|yesterday|tomorrow)\b"
    r"|'|\d(?!\d*(?:st|nd|rd|th)\b)|[qh]\s*\d|[fc]\.?y\.?\s*'?\d",
    re.IGNORECASE,
)


def _take_article(matches: list[DateMatch], text: str) -> list[DateMatch]:
    """Give "the" to the phrase it belongs to: "the year 2013", "the last quarter".

    It was left outside the match, so replacing the phrase kept it and wrote "sales for the
    January 2013 to December 2013". "the 1990s" always took its "the". A match that can
    describe a noun -- "the March figures" -- leaves it alone, since there the article is
    the noun's: "the March 2026 figures" is the right rewrite.
    """
    out: list[DateMatch] = []
    floor = 0
    for m in matches:
        lo, hi = m.span
        start = max(floor, lo - 12)
        # Most matches have no "the" in front; a substring test turns them away cheaply.
        if "the" not in text[start:lo].lower():
            out.append(m)
            floor = hi
            continue
        article = _ARTICLE.search(text, start, lo)
        if article is not None and not _DESCRIBES.match(m.text):
            m = replace(m, text=text[article.start() : hi], span=(article.start(), hi))
        out.append(m)
        floor = m.span[1]
    return out


def _diagnostics_to_original(
    diags: list[Diagnostic] | None, mark: int, norm: Normalized
) -> None:
    """Re-point the diagnostics added since ``mark`` at the caller's string.

    They are found in the normalised text, where "¼" is three characters, so a span taken
    there can run past the end of what the caller passed. Matches are mapped in
    :func:`_emit`; this does the same for everything that did not become one.
    """
    if diags is None:
        return
    for k in range(mark, len(diags)):
        d = diags[k]
        lo, hi = norm.to_original(*d.span)
        diags[k] = replace(d, text=norm.original[lo:hi], span=(lo, hi))


def _flag_unread_qualifiers(
    matches: list[DateMatch],
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
) -> list[DateMatch]:
    """Lower confidence where a meaning-changing word next to a match went unread.

    The rules cover the combinations people actually write, but not every one. When a
    qualifier is left over the honest answer is "I read part of this", not a confident
    range -- so the match survives with reduced confidence and, if the caller asked for
    diagnostics, an explanation naming the words that were dropped.
    """
    if not matches:
        return matches
    spans = [m.span for m in matches]
    out: list[DateMatch] = []
    for idx, m in enumerate(matches):
        lo, hi = m.span
        after = norm.original[hi : hi + 24]
        prev_end = spans[idx - 1][1] if idx else 0
        before = norm.original[max(prev_end, lo - 24) : lo]
        dropped = _QUALIFIER_AFTER.match(after) or _QUALIFIER_BEFORE.search(before)
        if dropped is None and norm.original[hi - 4 : hi].isdigit():
            # "Q3 2024-25": the second year of a split year, left behind.
            dropped = _SPLIT_YEAR_TAIL.match(after)
        if dropped is None and _NTH_LEAD.match(norm.original[lo:hi]):
            dropped = _NTH_TAIL.match(after)
        if dropped is None and _ENDS_IN_WEEKDAY.search(norm.original[lo:hi]):
            dropped = _WEEKDAY_THEN_WEEK.match(after) or _WEEKS_ON.search(before)
        if dropped is None:
            # "the quarter of <something not read as a month>": a bigger period that holds
            # the date. "the month of March" is March, so only a coarser unit counts.
            tail = before.rstrip().lower()
            unit = _UNIT_OF.search(before) if tail.endswith(("of", "the", "ing")) else None
            if unit is not None and grain_rank(Grain[unit.group(1).upper()]) > grain_rank(
                m.range.grain
            ):
                dropped = unit
        if dropped is None:
            out.append(m)
            continue
        if diags is not None:
            diags.append(
                Diagnostic(
                    norm.original[lo:hi],
                    m.span,
                    "partial",
                    f"read {norm.original[lo:hi]!r} but not {dropped.group(0).strip()!r}, "
                    "which changes the period",
                )
            )
        out.append(replace(m, confidence=min(m.confidence, 0.5)))
    return out


def _emit(
    r: DateRange,
    start: int,
    end: int,
    norm: Normalized,
    confidence: float,
    endpoints: tuple[DateRange, DateRange] | None = None,
) -> DateMatch:
    lo, hi = norm.to_original(start, end)
    return DateMatch(
        range=r, text=norm.original[lo:hi], span=(lo, hi), confidence=confidence,
        endpoints=endpoints,
    )


def _day_span_ends(spec: Spec, r: DateRange) -> tuple[DateRange, DateRange] | None:
    """The first and last day of "1-15 March", read as one fragment rather than two."""
    if spec.day_span is None or spec.mod is not None or r.start is None or r.end is None:
        return None
    last = r.end - timedelta(days=1)
    return (
        DateRange(r.start, r.start + timedelta(days=1), Grain.DAY, r.basis),
        DateRange(last, r.end, Grain.DAY, r.basis),
    )


def _usable(r: DateRange) -> bool:
    """Whether a resolved range is worth handing back.

    A zero-width range is not an answer. ``clamp`` may legitimately produce one, but we
    must never invent one: it reads as a valid result and quietly matches no rows.
    """
    return not r.is_empty


def _single(
    raw: _Raw,
    day: date,
    cfg: WranglerConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
    floor: int = 0,
) -> DateMatch | None:
    raw = _apply_modifier(raw, body, floor)
    try:
        r = resolve(raw.spec, day, cfg)
    except UnresolvableSpec as exc:
        if diags is not None:
            diags.append(
                Diagnostic(body[raw.start : raw.end], (raw.start, raw.end), raw.rule, str(exc))
            )
        return None
    if not _usable(r):
        if diags is not None:
            diags.append(
                Diagnostic(
                    body[raw.start : raw.end], (raw.start, raw.end), raw.rule,
                    "resolved to an empty period",
                )
            )
        return None
    confidence = raw.spec.confidence
    if _year_guessed(raw.spec, day, cfg):
        confidence = min(confidence, YEAR_GUESSED)
    doubt = _weekday_clash(raw.spec, r) or _shift_doubt(raw, r) or _part_of_day(raw, body)
    if doubt is not None:
        confidence = min(confidence, 0.5)
        if diags is not None:
            span = (raw.start, raw.end)
            diags.append(Diagnostic(body[raw.start : raw.end], span, raw.rule, doubt))
    return _emit(r, raw.start, raw.end, norm, confidence, _day_span_ends(raw.spec, r))


#: The confidence of a period whose year was not written, and was taken from today:
#: "September", "Q1", "15 March". The period is certain; the year is a guess.
YEAR_GUESSED = 0.7


def _year_guessed(spec: Spec, day: date, cfg: WranglerConfig) -> bool:
    """Whether resolving ``spec`` takes its year from today because none was written.

    "last March" and "Q1 last year" say which year, relative to today; "Q3 to date" says it
    is the current one. "September" does not say at all.
    """
    return (
        spec.year is None
        and spec.year_offset is None
        and not spec.through_today
        and default_year_for(spec, day, cfg) is not None
    )


def _weekday_clash(spec: Spec, r: DateRange) -> str | None:
    """"Monday, 15 March 2024" -- when the weekday written is not the date's."""
    if spec.weekday is None or r.start is None or r.days != 1:
        return None
    actual = r.start.weekday()
    if actual == spec.weekday:
        return None
    return (
        f"{r.start:%d %B %Y} is a {WEEKDAY_DISPLAY[actual]}, not a "
        f"{WEEKDAY_DISPLAY[spec.weekday]}, so one of the two is wrong"
    )


def _shift_doubt(raw: _Raw, r: DateRange) -> str | None:
    """A count from a date that may not mean what it is read as."""
    if raw.spec.shift is None:
        return None
    unit = raw.spec.shift[1]
    if raw.rule == "shift_the" and grain_rank(unit) > grain_rank(r.grain):
        # "the week before 15 March": the 8th, or the whole week before it?
        return (
            f"read as the {r.grain.value} a {unit.value} away; it may mean the whole "
            f"{unit.value} before or after instead"
        )
    if raw.rule == "shift" and grain_rank(unit) < grain_rank(r.grain):
        return f"moved a {r.grain.value} by {unit.value}s, which does not land on whole ones"
    return None


def _part_of_day(raw: _Raw, body: str) -> str | None:
    text = body[raw.start : raw.end]
    if raw.rule == "within_of":
        return (
            f"read {text!r} as the date alone: the window may run either side of it, or "
            "only after it"
        )
    if raw.rule != "part_of_day":
        return None
    return f"read {text!r} as today: date-wrangler answers in whole days"


def _merge(
    chain: list[_Raw],
    day: date,
    cfg: WranglerConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
    floor: int = 0,
) -> DateMatch | None:
    """Resolve a span of joined periods as one range: "Q1 to Q3", "from March to June",
    "between March and June". It runs from the start of the first to the end of the last,
    wrapping a year if they invert.

    A list -- "Q1 and Q3" -- never gets here: "and" without "between" links its periods
    as a list, and each comes back on its own (see :func:`_link_kinds`).
    """
    a, b = chain[0], chain[-1]
    start = a.start
    window = max(floor, a.start - 12)
    lead = _RANGE_LEAD.search(body[window : a.start])
    if lead:
        start = window + lead.start()

    sa, sb = _unify(a.spec, b.spec)
    try:
        ra, rb = resolve(sa, day, cfg), resolve(sb, day, cfg)
    except UnresolvableSpec as exc:
        if diags is not None:
            diags.append(Diagnostic(body[a.start : b.end], (a.start, b.end), "range", str(exc)))
        return None

    # The far end must finish strictly after the near end begins. Testing only for
    # "ends before it starts" misses adjacency: in "Q2 to Q1" the end of Q1 is exactly
    # the start of Q2, giving an empty range that quietly matches no rows.
    if ra.start is not None and rb.end is not None and rb.end <= ra.start:
        # Which end to shift depends on which one the writer pinned. "Q4 2024 to Q1"
        # means the Q1 after it; "from H2 to H1 2025" means the H2 before it. Only an
        # inferred year may move, so "Mar 2024 to Jan 2024" stays rejected.
        #
        # The year comes from the unified spec: default_year_for() returns None once
        # _unify has supplied one, and a fiscal label cannot be read off the dates.
        if not b.spec.has_explicit_year:
            base = sb.year if sb.year is not None else default_year_for(sb, day, cfg)
            if base is not None:
                try:
                    retried = resolve(sb.with_(year=base + 1), day, cfg)
                except UnresolvableSpec:
                    retried = None
                if retried is not None and retried.end is not None and retried.end > ra.start:
                    rb = retried
        elif not a.spec.has_explicit_year:
            base = sa.year if sa.year is not None else default_year_for(sa, day, cfg)
            far_end = rb.end
            if base is not None and far_end is not None:
                try:
                    retried = resolve(sa.with_(year=base - 1), day, cfg)
                except UnresolvableSpec:
                    retried = None
                if retried is not None and retried.start is not None and far_end > retried.start:
                    ra = retried
        if ra.start is not None and rb.end is not None and rb.end <= ra.start:
            if diags is not None:
                diags.append(
                    Diagnostic(
                        body[a.start : b.end],
                        (a.start, b.end),
                        "range",
                        "range does not end after it starts",
                    )
                )
            return None

    # "1Q to yesterday to 2000": a middle that falls outside the two ends does not run
    # forward, so the chain is not one span. REGRESSION: the middle was never looked at,
    # and the span ran from Q1 to 2000 over a "yesterday" that came before both.
    for mid in chain[1:-1]:
        try:
            rm = resolve(_unify(mid.spec, sb)[0], day, cfg)
        except UnresolvableSpec:
            rm = None
        if rm is None or (
            ra.start is not None
            and rb.end is not None
            and (
                (rm.start is not None and rm.start < ra.start)
                or (rm.end is not None and rm.end > rb.end)
            )
        ):
            if diags is not None:
                diags.append(
                    Diagnostic(
                        body[a.start : b.end], (a.start, b.end), "range",
                        "range does not run forward through its middle",
                    )
                )
            return None

    grain = ra.grain if ra.grain == rb.grain else max(ra.grain, rb.grain, key=_grain_rank)
    merged = DateRange(ra.start, rb.end, grain, ra.basis)
    if not _usable(merged):
        if diags is not None:
            diags.append(
                Diagnostic(
                    body[a.start : b.end], (a.start, b.end), "range",
                    "resolved to an empty period",
                )
            )
        return None

    confidence = min(a.spec.confidence, b.spec.confidence)
    # A relative end fixes the other's year: "Q1 to last month" is this year's Q1.
    if (_year_guessed(sa, day, cfg) and not sb.is_relative) or (
        _year_guessed(sb, day, cfg) and not sa.is_relative
    ):
        confidence = min(confidence, YEAR_GUESSED)
    clash = _weekday_clash(sa, ra) or _weekday_clash(sb, rb)
    if clash is not None:
        confidence = min(confidence, 0.5)
        if diags is not None:
            diags.append(Diagnostic(body[a.start : b.end], (a.start, b.end), "range", clash))
    # "from the start of A to the end of B" is a sensible thing to ask even when B lies
    # inside A -- "Q1 to 15 March" stops partway through Q1. It stops being sensible when
    # B begins no later than A does: "Q1 to January" is just January, and A has said
    # nothing at all. Keep the literal range, but do not vouch for it.
    if (
        ra.start is not None
        and ra.end is not None
        and rb.start is not None
        and rb.end is not None
        and rb.start <= ra.start
        and rb.end < ra.end
    ):
        confidence = min(confidence, 0.5)
        if diags is not None:
            diags.append(
                Diagnostic(
                    body[start : b.end],
                    (start, b.end),
                    "range",
                    f"read {body[start : b.end]!r} as {merged}, but it ends inside the "
                    "period it starts with, so the first period adds nothing",
                )
            )
    return _emit(merged, start, b.end, norm, confidence, (ra, rb))


#: Coarsest grain wins when a range joins two resolutions. The ordering itself lives beside
#: the enum in :mod:`.types`, so joining and intersecting cannot drift apart.
_grain_rank = grain_rank


def parse_one(
    text: str,
    *,
    today: date | datetime | None = None,
    tz: tzinfo | None = None,
    config: WranglerConfig = DEFAULT_CONFIG,
) -> DateMatch | None:
    """The first date expression in ``text``, or None."""
    found = parse(text, today=today, tz=tz, config=config)
    return found[0] if found else None


def diagnose(
    text: str,
    *,
    today: date | datetime | None = None,
    tz: tzinfo | None = None,
    config: WranglerConfig = DEFAULT_CONFIG,
) -> tuple[list[DateMatch], list[Diagnostic]]:
    """:func:`parse`, with the unresolvable fragments alongside the matches."""
    diags: list[Diagnostic] = []
    found = parse(text, today=today, tz=tz, config=config, diagnostics=diags)
    return found, diags


def substitute(
    text: str,
    *,
    today: date | datetime | None = None,
    tz: tzinfo | None = None,
    config: WranglerConfig = DEFAULT_CONFIG,
    formatter: Callable[[DateRange], str] | None = None,
    min_confidence: float = 0.6,
    keep_text: bool = False,
) -> str:
    """Rewrite every date expression in ``text``. Only the matched phrase changes.

    By default the phrase is replaced by its dates. ``keep_text=True`` keeps the phrase and
    puts the dates after it in brackets, so the label survives for whoever reads on and the
    reading is there to check:

        >>> substitute("sales of Q4", today=today)
        'sales of October 2026 to December 2026'
        >>> substitute("sales of Q4", today=today, keep_text=True)
        'sales of Q4 (October 2026 to December 2026)'

    With ``keep_text`` a phrase that already says what the brackets would -- "March 2024" --
    is left alone, and so is one already followed by dates in brackets, whoever wrote them:
    running it again on its own output adds nothing. ``formatter`` decides how the dates
    look either way.

    ``min_confidence`` leaves anything below it exactly as the writer typed it. This is the
    one function that turns a guess into a confident sentence, so by default it declines
    the guesses: a match flagged at 0.5 -- read only in part, or a span that ends inside
    its own start -- stays as typed, while 0.7 and up (a month with no year, a bare "2024",
    an all-numeric date) is rewritten:

        >>> substitute("calls tomorrow morning", today=today)
        'calls tomorrow morning'                       # flagged: "morning" was not read
        >>> substitute("calls tomorrow morning", today=today, min_confidence=0)
        'calls 10 October 2026 morning'

    A list is not a guess: "revenue Q1 and Q3" comes back as its two quarters, each
    rewritten on its own.

    :func:`diagnose` returns that phrase at 0.5 with an explanation. Rewriting it discards
    the explanation and leaves prose that reads as settled, which is worse than leaving the
    original words alone. Pass ``min_confidence=0`` to rewrite everything, as before 1.0;
    0.75 to leave a period with no year as typed -- on historic data today's year is the
    wrong one -- or 0.9 to keep bare years as typed as well.
    """
    from .format import format_range

    render = formatter or format_range
    if keep_text:
        # One day for every read, so a call that straddles midnight cannot mix two.
        day = _resolve_today(today, tz)
        return _annotate(text, render, min_confidence, lambda s: parse(s, today=day, config=config))
    found = parse(text, today=today, tz=tz, config=config)
    out = text
    for match in reversed(found):
        if match.confidence < min_confidence:
            continue
        lo, hi = match.span
        out = out[:lo] + render(match.range) + out[hi:]
    return out


def _annotate(
    text: str,
    render: Callable[[DateRange], str],
    min_confidence: float,
    read: Callable[[str], list[DateMatch]],
) -> str:
    """``substitute(keep_text=True)``: put each phrase's dates after it, in brackets.

    Brackets that already hold a phrase's dates -- from an earlier pass, on any day and
    with any formatter, or from the writer -- are set aside and the rest is read without
    them. Reading only what the first pass read is what makes a second pass add nothing:
    leaving the brackets in would change the text around *other* phrases, and a bare year
    that had no cue the first time could pick one up from the inserted dates.

    The brackets are found first, from a few words each, so the whole text is read once.
    """
    removed = _annotations_in(text, render, read)
    found = read(_without(text, removed))
    annotated = {_to_bare(lo, removed) for lo, _ in removed}
    inserts: list[tuple[int, str]] = []
    for match in found:
        hi = match.span[1]
        if hi in annotated or match.confidence < min_confidence:
            continue
        dates = render(match.range)
        if _same_words(match.text, dates):
            continue
        inserts.append((_to_text(hi, removed, end=True), f" ({dates})"))
    out = text
    for at, piece in reversed(inserts):
        out = out[:at] + piece + out[at:]
    return out


_BRACKETS = re.compile(r"[ \t]*\([ \t]*([^()]*?)[ \t]*\)")


#: How many words back to look for the date a pair of brackets annotates.
_WORDS_BACK = 6


def _annotations_in(
    text: str, render: Callable[[DateRange], str], read: Callable[[str], list[DateMatch]]
) -> list[tuple[int, int]]:
    """Brackets that already annotate a date, as sorted spans of ``text``.

    That is: a date ends right before them, and they hold dates and nothing else -- or
    exactly what ``render`` writes for that date, for output that does not read back as
    one. All of it is judged on those few words alone, never in context, so inserting
    brackets elsewhere cannot change the verdict; that is what makes a second pass find
    exactly the brackets the first one wrote. "revenue (Q1 2024) grew" is not one:
    "revenue" is not a date.
    """
    seen: dict[str, list[DateMatch]] = {}

    def once(s: str) -> list[DateMatch]:
        # A document repeats its phrases -- "last quarter", the same dates -- so each few
        # words are read once however often they come round.
        if s not in seen:
            seen[s] = read(s)
        return seen[s]

    out: list[tuple[int, int]] = []
    for m in _BRACKETS.finditer(text):
        inner = m.group(1)
        if not inner:
            continue
        before = _dates_ending_at(text, m.start(), once)
        if _only_dates(inner, once):
            if next(before, None) is not None:
                out.append(m.span())
        # The fallback reads every window, so only for something a formatter could have
        # written: a date that does not read back still has a number in it.
        elif any(ch.isdigit() for ch in inner) and any(
            _same_words(inner, render(d.range)) for d in before
        ):
            out.append(m.span())
    return out


#: Words a formatter may put between or around dates: "1 April to 30 June", "up to ...".
_DATE_GLUE = re.compile(
    r"\b(?:to|and|through|until|till|from|since|onwards|before|after|as\s+of|up\s+to)\b",
    re.IGNORECASE,
)


def _only_dates(s: str, read: Callable[[str], list[DateMatch]]) -> bool:
    """Whether ``s`` is dates and the words and marks that join them, and nothing else."""
    found = read(s)
    if not found:
        return False
    rest = list(s)
    for m in found:
        lo, hi = m.span
        rest[lo:hi] = " " * (hi - lo)
    leftover = _DATE_GLUE.sub(" ", "".join(rest))
    return not any(ch.isalnum() for ch in leftover)


def _dates_ending_at(
    text: str, at: int, read: Callable[[str], list[DateMatch]]
) -> Iterator[DateMatch]:
    """The dates the last few words before ``at`` end in, each read on its own, the
    shortest first -- lazily, because one is usually all that is asked for."""
    head = text[max(0, at - 200) : at]
    for start in reversed([w.start() for w in re.finditer(r"\S+", head)][-_WORDS_BACK:]):
        words = head[start:]
        found = read(words)
        if found and found[-1].span[1] == len(words):
            yield found[-1]


def _without(text: str, removed: list[tuple[int, int]]) -> str:
    """``text`` with the sorted, disjoint ``removed`` spans taken out."""
    pieces, at = [], 0
    for lo, hi in removed:
        pieces.append(text[at:lo])
        at = hi
    pieces.append(text[at:])
    return "".join(pieces)


def _to_bare(pos: int, removed: list[tuple[int, int]]) -> int:
    """A position in the text, as a position in the text with ``removed`` taken out."""
    return pos - sum(hi - lo for lo, hi in removed if hi <= pos)


def _to_text(pos: int, removed: list[tuple[int, int]], *, end: bool) -> int:
    """A position in the text with ``removed`` taken out, back in the text.

    Where a removed span sat exactly at ``pos``, ``end`` puts the result before it -- the
    end of the phrase it follows -- and otherwise after it.
    """
    shift = 0
    for lo, hi in removed:
        at = lo - shift  # where this span sat in the shorter text
        if at < pos or (at == pos and not end):
            shift += hi - lo
        else:
            break
    return pos + shift


def _same_words(typed: str, dates: str) -> bool:
    """Whether the brackets would only repeat the phrase: "March 2024 (March 2024)"."""
    return " ".join(typed.split()).casefold() == " ".join(dates.split()).casefold()
