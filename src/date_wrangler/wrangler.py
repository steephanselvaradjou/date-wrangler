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
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

from .config import DEFAULT_CONFIG, WranglerConfig
from .normalize import Normalized, normalize
from .resolve import UnresolvableSpec, default_year_for, resolve
from .rules import RULES, Rule
from .spec import Spec
from .types import DateMatch, DateRange, Grain, Mod
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
    }
)
_PREFILTER = re.compile(rf"\d|\b{alt(_TRIGGERS)}\b", re.IGNORECASE)

_SCANNER = re.compile(
    "|".join(f"(?P<{rule.name}>{rule.pattern})" for rule in RULES), re.IGNORECASE
)
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
    (re.compile(r"\bsince\s+$", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"\b(?:prior\s+to|earlier\s+than|before)\s+$", re.IGNORECASE), Mod.BEFORE),
    (re.compile(r"\b(?:up\s*to|upto|until|till)\s+$", re.IGNORECASE), Mod.UNTIL),
    (re.compile(r"\bafter\s+$", re.IGNORECASE), Mod.AFTER),
)
_MOD_SUFFIXES: tuple[tuple[re.Pattern[str], Mod | None], ...] = (
    (re.compile(r"^\s*onwards?\b", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"^\s*or\s+later\b", re.IGNORECASE), Mod.SINCE),
    (re.compile(r"^\s*or\s+earlier\b", re.IGNORECASE), Mod.UNTIL),
    (re.compile(r"^\s*to\s+date\b", re.IGNORECASE), None),  # handled by the to_date rule
)

#: Words that introduce a range and belong inside its span -- leaving them out makes
#: substitution ungrammatical ("between from April to September").
_RANGE_LEAD = re.compile(r"\b(?:from|between|betwn|b/w)\s+$", re.IGNORECASE)

#: Words that make a bare month or year read as a date rather than a noun.
_CUE = re.compile(
    r"\b(?:in|on|at|for|during|of|since|from|until|till|by|through|between|before|after|"
    r"vs|versus|compared\s+(?:to|with)|"
    r"sales|revenue|profit|data|report|numbers|figures|results|performance|growth|"
    r"spend|cost|budget|forecast|actuals)\W*$",
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
_WEAK_RULES = frozenset({"month", "bare_year", "weekday"})


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
    out: list[_Raw] = []
    for m in _SCANNER.finditer(text):
        name = m.lastgroup
        if name is None:
            continue
        rule = _RULES_BY_NAME.get(name)
        if rule is None:
            continue
        fragment = m.group(0)
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
    Q2 2024" across two different years defeats the point.
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
        years = [r.spec.year for r in members if r.spec.year is not None]
        if years:
            for r in members:
                if r.spec.year is None and not r.spec.is_relative:
                    r.spec = r.spec.with_(year=years[-1])
        i = j


def _unify(a: Spec, b: Spec) -> tuple[Spec, Spec]:
    """Make both endpoints agree on year and basis before resolving.

    Otherwise "Q1 to Q2 of 2024" resolves one end fiscally, the other on the calendar.
    """
    if not a.is_relative and not b.is_relative:
        if a.year is None and b.year is not None:
            a = a.with_(year=b.year)
        elif b.year is None and a.year is not None:
            b = b.with_(year=a.year)
    basis = a.basis if a.basis is not None else b.basis
    if basis is not None:
        if a.basis is None:
            a = a.with_(basis=basis)
        if b.basis is None:
            b = b.with_(basis=basis)
    return a, b


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
            return _Raw(raw.rule, raw.spec.with_(mod=mod), window + m.start(), raw.end)
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
    if not text or not _PREFILTER.search(text):
        return []

    day = _resolve_today(today, tz)
    norm = normalize(text)
    body = norm.text

    raws = _scan(body, config, diagnostics)
    if not raws:
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
            merged = _merge(raws[i], raws[j], day, config, body, norm, diagnostics, floor)
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
    return matches


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
    a: _Raw,
    b: _Raw,
    day: date,
    cfg: WranglerConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
    floor: int = 0,
) -> DateMatch | None:
    """Resolve two endpoints as one span, wrapping a year if they invert."""
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

    start = a.start
    window = max(floor, a.start - 12)
    before = body[window : a.start]
    lead = _RANGE_LEAD.search(before)
    if lead:
        start = window + lead.start()
    return _emit(merged, start, b.end, norm, min(a.spec.confidence, b.spec.confidence))


#: Coarsest grain wins when a range joins two resolutions.
_GRAIN_ORDER: tuple[Grain, ...] = (
    Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.HALF, Grain.YEAR,
)


def _grain_rank(g: Grain) -> int:
    return _GRAIN_ORDER.index(g)


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
) -> str:
    """Rewrite every date expression in ``text``. Only the matched phrase changes."""
    from .format import format_range

    render = formatter or format_range
    found = parse(text, today=today, tz=tz, config=config)
    out = text
    for match in reversed(found):
        lo, hi = match.span
        out = out[:lo] + render(match.range) + out[hi:]
    return out
