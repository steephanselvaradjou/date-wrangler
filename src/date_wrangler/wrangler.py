"""Finding dates in text.

    prefilter -> normalise -> scan -> link -> merge ranges -> modifiers -> resolve

Ranges are assembled after scanning rather than during it, by looking at the gap between
two matches. That keeps the scanning pattern small, makes a new connector a one-line
addition, and lets an explicit year on one endpoint reach the other before either is
resolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, tzinfo

from .config import DEFAULT_CONFIG, WranglerConfig
from .normalize import Normalized, normalize
from .resolve import UnresolvableSpec, default_year_for, resolve
from .rules import RULES, Rule
from .spec import Kind, Part, Spec
from .types import DateMatch, DateRange, Mod, grain_rank
from .vocab import (
    FUTURE_WORDS,
    MONTH_NAMES,
    PAST_WORDS,
    UNIT_WORDS,
    WEEKDAY_NAMES,
    alt,
)

__all__ = ["parse", "parse_one", "substitute", "diagnose", "Diagnostic"]


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
    }
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


_SCAN_PATTERN = "|".join(f"(?P<{rule.name}>{rule.pattern})" for rule in RULES)
#: Every rule opens with \b, so it is checked once, ahead of the alternation, instead of
#: once per rule at every position. Most positions are inside a word and fail it, and they
#: now fail after one test rather than forty-eight -- about a third of scanning time, with
#: nothing able to match differently, since each branch still begins with its own \b. A
#: test holds every rule to that opening, because one without it would quietly lose matches.
_SCANNER = re.compile(rf"\b(?:{_SCAN_PATTERN})", re.IGNORECASE)
#: The same rules, case-sensitive, for the lowered-ASCII fast path in :func:`_scan`. Every
#: rule pattern is written in lower case, so the two accept exactly the same fragments.
_SCANNER_CS = re.compile(rf"\b(?:{_SCAN_PATTERN})")
_RULES_BY_NAME: dict[str, Rule] = {rule.name: rule for rule in RULES}


# ---------------------------------------------------------------------------
# Connectors, comparisons and modifiers
# ---------------------------------------------------------------------------

#: A gap that joins two periods into one span.
_STRONG_LINK = re.compile(r"^\s*(?:to|through|thru|until|till|up\s*to|upto|-|–|—)\s*$", re.I)
#: "and" joins a range only when nothing suggests a list.
_WEAK_LINK = re.compile(r"^\s*(?:and|&)\s*$", re.IGNORECASE)
#: A comma means the writer is enumerating, not describing a span.
_LIST_LINK = re.compile(r"^\s*,\s*(?:and\s+)?$", re.IGNORECASE)

#: Gaps that mean "these are two things being contrasted", never one range.
_COMPARISON_GAP = re.compile(
    r"^\s*(?:vs\.?|versus|against|compared\s+(?:to|with)|relative\s+to|over)\s*$", re.I
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
            r"\b(?:on\s+or\s+after|not\s+before|no\s+earlier\s+than|from\s+and\s+including)"
            r"\s+$",
            re.IGNORECASE,
        ),
        Mod.SINCE,
    ),
    (
        re.compile(
            r"\b(?:on\s+or\s+before|not\s+after|no\s+later\s+than"
            r"|up\s+to\s+and\s+including|by)\s+$",
            re.IGNORECASE,
        ),
        Mod.UNTIL,
    ),
    (re.compile(r"\bsince\s+$", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"\b(?:prior\s+to|earlier\s+than|before)\s+$", re.IGNORECASE), Mod.BEFORE),
    # "through 31 March" on its own is a deadline, inclusive. Inside a range ("Jan through
    # Mar") the two ends are merged before any modifier is looked for, so this never sees it.
    (re.compile(r"\b(?:up\s*to|upto|until|till|through|thru)\s+$", re.IGNORECASE), Mod.UNTIL),
    (re.compile(r"\bafter\s+$", re.IGNORECASE), Mod.AFTER),
)

#: "from" starts something only when what follows is a single day: "from 1 April",
#: "from Monday". After a period it more often names a source -- "the figures from Q1" are
#: Q1's figures, not everything since -- so a period after "from" is left as it is.
_FROM_PREFIX = re.compile(r"\bfrom\s+$", re.IGNORECASE)
_DAY_KINDS = frozenset({Kind.ABS_DAY, Kind.DAY_KEYWORD, Kind.WEEKDAY})
#: "two weeks from Friday" counts on from Friday; it does not start there. A duration
#: ahead of "from" leaves the word for the safety net, which flags the phrase as partial.
_DURATION_FROM = re.compile(
    r"\b(?:a|an|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:day|week|fortnight|month|quarter|half|year)s?\s+from\s+$",
    re.IGNORECASE,
)

#: "before the end of March" is a deadline for all of March, not "before its last third".
_END_OF = re.compile(r"(?:the\s+)?end\s+of\b", re.IGNORECASE)
_MOD_SUFFIXES: tuple[tuple[re.Pattern[str], Mod | None], ...] = (
    (re.compile(r"^\s*onwards?\b", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"^\s*or\s+later\b", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"^\s*or\s+earlier\b", re.IGNORECASE), Mod.UNTIL),
    (re.compile(r"^\s*to\s+date\b", re.IGNORECASE), None),  # handled by the to_date rule
)

#: Words that introduce a range and belong inside its span -- leaving them out makes
#: substitution ungrammatical ("between from April to September").
_RANGE_LEAD = re.compile(r"\b(?:from|between|betwn|b/w)\s+$", re.IGNORECASE)

#: An "and" anywhere in the text joining the two endpoints of a merged span.
_AND_JOIN = re.compile(r"\band\b|&", re.IGNORECASE)

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
    r"admitted|discharged|appointment|renew|renewal|payable)"
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
_WEAK_RULES = frozenset({"month", "bare_year", "weekday", "ordinal_day", "week_bare"})

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

#: "next Monday week", "Monday week" -- British for the Monday after next. Read as the
#: weekday alone it is a week early, so a weekday followed by "week" is a partial read.
_WEEKDAY_THEN_WEEK = re.compile(r"^\s+week\b(?!\s*\d)", re.IGNORECASE)
_ENDS_IN_WEEKDAY = re.compile(rf"\b(?:{alt(WEEKDAY_NAMES)})\s*$", re.IGNORECASE)
#: "a week on Monday", "two weeks from Friday" -- a weekday pushed on by a stated count.
_WEEKS_ON = re.compile(
    r"\b(?:a|one|two|three|\d+)\s+weeks?\s+(?:on|from)\s+$", re.IGNORECASE
)
_QUALIFIER_BEFORE = re.compile(
    r"\b(?:first\s+half|second\s+half|latter\s+half|beginning|start|early|middle|mid|late|"
    r"end|same|this\s+time|\d{1,2}(?:st|nd|rd|th))\s+(?:of\s+|in\s+)?\W*$"
    # "a year from March" is March next year, not March. Only "from now"/"from today"
    # are actually resolved, so any other tail here is a period we did not compute.
    r"|\b(?:a|an|\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:day|week|fortnight|month|quarter|half|year)s?\s+from\s+\W*$"
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
    """Run every rule's pattern over ``text`` in one pass.

    Scanning is where most of the time goes, and IGNORECASE over an alternation this size
    costs roughly half as much again as a plain match -- so ASCII text is lowered once and
    matched case-sensitively instead. Offsets survive because lowering ASCII cannot change
    a string's length; anything else falls back to the case-insensitive pattern, where that
    guarantee does not hold. Either way the *fragment* is sliced from the original, so the
    rules still see the writer's capitals.
    """
    if text.isascii():
        target, scanner = text.lower(), _SCANNER_CS
    else:
        target, scanner = text, _SCANNER
    out: list[_Raw] = []
    for m in scanner.finditer(target):
        name = m.lastgroup
        if name is None:
            continue
        rule = _RULES_BY_NAME.get(name)
        if rule is None:
            continue
        fragment = text[m.start() : m.end()]
        try:
            spec = rule.parse(fragment, cfg)
        except (ValueError, KeyError) as exc:
            spec = None
            if diags is not None:
                diags.append(Diagnostic(fragment, m.span(), name, str(exc)))
        if spec is None:
            if diags is not None:
                diags.append(
                    Diagnostic(fragment, m.span(), name, "matched but could not be read")
                )
            continue
        out.append(_Raw(name, spec, m.start(), m.end()))
    return out


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
    if cfg.strictness == "greedy" or raw.rule not in _WEAK_RULES:
        return True
    if cfg.strictness == "strict":
        return False
    if raw.rule == "month" and _looks_like_a_person(text, raw.start, raw.end):
        return False
    if chain_start == 0 and chain_end >= len(text.rstrip()):
        return True  # the whole input is the date
    if _CUE.search(text[max(0, chain_start - 32) : chain_start]):
        return True
    # "the March figures" -- no cue in front, but the noun behind settles it. Capitalised
    # only, so "it may report a loss" stays a modal verb.
    return bool(text[chain_start].isupper() and _TRAILING_CUE.match(text[chain_end:]))


# ---------------------------------------------------------------------------
# Linking adjacent matches
# ---------------------------------------------------------------------------


def _link_kinds(raws: list[_Raw], text: str) -> list[str | None]:
    """Classify the gap between each adjacent pair: range, list, comparison or nothing."""
    links: list[str | None] = []
    for a, b in zip(raws, raws[1:], strict=False):
        gap = text[a.end : b.start]
        if _COMPARISON_GAP.match(gap):
            links.append("compare")
        elif _LIST_LINK.match(gap):
            links.append("list")
        elif _STRONG_LINK.match(gap) or _WEAK_LINK.match(gap):
            lead = text[max(0, a.start - 40) : a.start]
            links.append("compare" if _COMPARISON_LEAD.search(lead) else "range")
        else:
            links.append(None)
    return links


def _demote_lists(links: list[str | None]) -> list[str | None]:
    """A comma anywhere in a run makes the whole run a list.

    "Q1, Q2 and Q3" is three quarters, not Q1 plus a Q2-to-Q3 span.
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
                if out[k] != "compare":
                    out[k] = "list"
        i = j
    return out


def _propagate_year(raws: list[_Raw], links: list[str | None]) -> None:
    """Share one stated year across a list, or across a comparison.

    "Jan, Feb, Mar 2024" is three months of the same year, and comparing "Q1 versus
    Q2 2024" across two different years defeats the point. The year's basis travels with
    it: in "Q1, Q2 and Q3 of FY25" all three are fiscal quarters, not just the last.
    """
    shared = ("list", "compare")
    i = 0
    while i < len(links):
        if links[i] not in shared:
            i += 1
            continue
        j = i
        while j < len(links) and links[j] in shared:
            j += 1
        members = raws[i : j + 1]
        stated = [r.spec for r in members if r.spec.year is not None]
        if stated:
            year, basis = stated[-1].year, stated[-1].basis
            for r in members:
                if r.spec.year is None and not r.spec.is_relative:
                    r.spec = r.spec.with_(year=year)
                    if r.spec.basis is None and basis is not None:
                        r.spec = r.spec.with_(basis=basis)
        i = j


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
    window = max(floor, raw.start - 24)
    before = text[window : raw.start]
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
        and raw.spec.kind in _DAY_KINDS
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
    raws = _scan(body, config, diagnostics)
    if not raws:
        _diagnostics_to_original(diagnostics, mark, norm)
        return []

    links = _demote_lists(_link_kinds(raws, body))
    _propagate_year(raws, links)

    # Group linked matches into chains, so strictness judges the chain as a whole.
    chain_of: dict[int, tuple[int, int]] = {}
    i = 0
    while i < len(raws):
        j = i
        while j < len(links) and links[j] is not None:
            j += 1
        for k in range(i, j + 1):
            chain_of[k] = (raws[i].start, raws[j].end)
        i = j + 1

    kept = {
        idx
        for idx, raw in enumerate(raws)
        if _passes_strictness(raw, body, config, *chain_of[idx])
    }

    matches: list[DateMatch] = []
    floor = 0  # where the previous match ended, so modifiers cannot reach back over it
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
                matches.append(merged)
                floor = raws[j].end
                i = j + 1
                continue
        single = _single(raws[i], day, config, body, norm, diagnostics, floor)
        if single is not None:
            matches.append(single)
            floor = max(floor, raws[i].end)
        i += 1
    _diagnostics_to_original(diagnostics, mark, norm)
    return _flag_unread_qualifiers(matches, body, norm, diagnostics)


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
        if dropped is None and _NTH_LEAD.match(norm.original[lo:hi]):
            dropped = _NTH_TAIL.match(after)
        if dropped is None and _ENDS_IN_WEEKDAY.search(norm.original[lo:hi]):
            dropped = _WEEKDAY_THEN_WEEK.match(after) or _WEEKS_ON.search(before)
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
    r: DateRange, start: int, end: int, norm: Normalized, confidence: float
) -> DateMatch:
    lo, hi = norm.to_original(start, end)
    return DateMatch(range=r, text=norm.original[lo:hi], span=(lo, hi), confidence=confidence)


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
    return _emit(r, raw.start, raw.end, norm, raw.spec.confidence)


def _merge(
    chain: list[_Raw],
    day: date,
    cfg: WranglerConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
    floor: int = 0,
) -> DateMatch | None:
    """Resolve a chain of joined periods as one range.

    The words between them decide which of two things it is. A *span* -- "Q1 to Q3",
    "from March to June", "between March and June" -- runs from the start of the first to
    the end of the last, wrapping a year if they invert. A *list* -- "Q1 and Q2" -- is the
    union of the periods in it, because "and" says which periods and nothing about order.
    """
    a, b = chain[0], chain[-1]
    start = a.start
    window = max(floor, a.start - 12)
    lead = _RANGE_LEAD.search(body[window : a.start])
    if lead:
        start = window + lead.start()
    # "and" anywhere in the join -- a chain of three is merged from both ends, so "Q1 and
    # Q3 and last month" carries "and Q3 and" between them -- and no "between" or "from"
    # ahead of it to make the whole thing a span.
    if lead is None and _AND_JOIN.search(body[a.end : b.start]) is not None:
        return _merge_list(chain, start, day, cfg, body, norm, diags)

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
    return _emit(merged, start, b.end, norm, confidence)


def _merge_list(
    chain: list[_Raw],
    start: int,
    day: date,
    cfg: WranglerConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
) -> DateMatch | None:
    """The union of a list of periods: "Q1 and Q2", "this year and Q1".

    Each period is resolved on its own -- a year written on the last reaches the others,
    so "Q1 and Q2 2024" is both in 2024 -- and the answer covers all of them. There is no
    year-wrap: a list says nothing about order, so "Q3 and Q1" is two quarters of one year.

    REGRESSION: lists were merged like spans, from the start of the first period to the
    end of the last. Where a later period sat inside an earlier one that cut the answer
    short at full confidence -- "this year and Q1" came back as Q1 alone.
    """
    last = chain[-1].spec
    specs = [_unify(raw.spec, last)[0] for raw in chain[:-1]]
    specs.append(_unify(chain[0].spec, last)[1])
    try:
        ranges = [resolve(s, day, cfg) for s in specs]
    except UnresolvableSpec as exc:
        if diags is not None:
            diags.append(
                Diagnostic(body[start : chain[-1].end], (start, chain[-1].end), "range", str(exc))
            )
        return None
    if any(r.start is None or r.end is None for r in ranges):
        return None

    ordered = sorted(ranges, key=lambda r: r.start)  # type: ignore[arg-type,return-value]
    lo = ordered[0].start
    hi = max(r.end for r in ordered)  # type: ignore[type-var]
    assert lo is not None and hi is not None
    grain = max((r.grain for r in ordered), key=_grain_rank)
    merged = DateRange(lo, hi, grain, ranges[0].basis)

    confidence = min(raw.spec.confidence for raw in chain)
    # A gap between the periods means the single range also covers days nobody named:
    # "Q1 and Q3" holds Q2. Joining is still the useful answer, but not a confident one.
    reach = ordered[0].end
    for r in ordered[1:]:
        assert reach is not None and r.start is not None and r.end is not None
        if r.start > reach:
            confidence = min(confidence, 0.5)
            if diags is not None:
                text = body[start : chain[-1].end]
                diags.append(
                    Diagnostic(
                        text,
                        (start, chain[-1].end),
                        "range",
                        f"read {text!r} as one span, which also covers "
                        f"{reach.isoformat()} to {r.start.isoformat()} in between; "
                        "write 'to' for a span, or separate the periods with a comma",
                    )
                )
            break
        reach = max(reach, r.end)
    return _emit(merged, start, chain[-1].end, norm, confidence)


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
    min_confidence: float = 0.0,
) -> str:
    """Rewrite every date expression in ``text``. Only the matched phrase changes.

    ``min_confidence`` leaves anything below it exactly as the writer typed it. Raise it
    whenever the output will be read as fact -- by a person or by a model -- because this
    is the one function that turns a flagged guess into a confident sentence:

        >>> substitute("revenue Q1 and Q3", today=today)
        'revenue April 2026 to December 2026'          # Q2 is in there, unremarked
        >>> substitute("revenue Q1 and Q3", today=today, min_confidence=0.9)
        'revenue Q1 and Q3'

    Both of those phrases come back from :func:`diagnose` at confidence 0.5 with an
    explanation. Rewriting them discards that explanation and leaves prose that reads as
    settled, which is worse than leaving the original words alone. The default stays 0.0
    so existing callers are unaffected; a future major version will raise it.
    """
    from .format import format_range

    render = formatter or format_range
    found = parse(text, today=today, tz=tz, config=config)
    out = text
    for match in reversed(found):
        if match.confidence < min_confidence:
            continue
        lo, hi = match.span
        out = out[:lo] + render(match.range) + out[hi:]
    return out
