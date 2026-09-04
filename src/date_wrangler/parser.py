"""Finding dates in text.

The pass structure is: reject cheaply, scan once, then reason about what was found.

    prefilter -> normalise -> scan -> link -> merge ranges -> modifiers -> resolve

Ranges are assembled *after* scanning rather than during it. The predecessor inlined its
entire token alternation twice inside one pattern so that "X to Y" could match in a single
shot, which made the expression enormous, made every no-date string pay for it, and left
the connector vocabulary fixed at ``to``, ``and`` and ``-``. Looking at the gap between two
matches instead makes ``through``, ``till``, ``until`` and an en dash one-line additions,
and it is what allows an explicit year or basis on one endpoint to reach the other before
either is resolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

from .config import DEFAULT_CONFIG, ParserConfig
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

#: Every rule needs either a digit or one of these words, so a string containing neither
#: cannot possibly hold a date. Most real traffic is rejected here for the price of one
#: scan, instead of paying for the full alternation.
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
#: ``and`` joins a range only when nothing suggests a list.
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

#: Words that introduce a range and belong inside its span. Leaving them outside makes
#: substitution both ungrammatical ("between from April to September") and non-idempotent,
#: since the replacement re-emits a "from" the original one then sits in front of.
_RANGE_LEAD = re.compile(r"\b(?:from|between|betwn|b/w)\s+$", re.IGNORECASE)

#: Words that make a bare month name or bare year plausible as a date rather than a noun.
_CUE = re.compile(
    r"\b(?:in|on|at|for|during|of|since|from|until|till|by|through|between|before|after|"
    r"sales|revenue|profit|data|report|numbers|figures|results|performance|growth|"
    r"spend|cost|budget|forecast|actuals)\W*$",
    re.IGNORECASE,
)

#: Rules whose matches are weak enough to need a cue in "balanced" mode.
_WEAK_RULES = frozenset({"month", "bare_year", "weekday"})


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


def _scan(text: str, cfg: ParserConfig, diags: list[Diagnostic] | None) -> list[_Raw]:
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
    raw: _Raw, text: str, cfg: ParserConfig, chain_start: int, chain_end: int
) -> bool:
    """Whether a weak match survives the configured strictness.

    A bare month name in running prose -- "the march on Washington", "my august
    colleague" -- is the dominant false positive for this kind of library, and no amount
    of extra patterns fixes it. Requiring a nearby cue does.

    The cue is looked for before the whole *chain*, not before this match, so that
    "from Jan to Mar" is admitted on the strength of the "from" that introduces it. Being
    joined to another period is not on its own enough: "a may-december romance" is two
    month names either side of a dash and nothing more.
    """
    if cfg.strictness == "greedy" or raw.rule not in _WEAK_RULES:
        return True
    if cfg.strictness == "strict":
        return False
    if chain_start == 0 and chain_end >= len(text.rstrip()):
        return True  # the whole input is the date
    return bool(_CUE.search(text[max(0, chain_start - 32) : chain_start]))


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
    """A comma anywhere in a run of joined periods makes the whole run a list.

    "Q1, Q2 and Q3" is three quarters, not Q1 plus a Q2-to-Q3 span. The predecessor read
    the trailing "and" as a range connector and silently merged the last two.
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
    """Share one stated year across a list of periods, or across a comparison.

    "Jan, Feb, Mar 2024" names three months of the same year; resolving the first two
    against today's year and only the last against 2024 is never what was meant. The same
    goes for "Q1 versus Q2 2024", where comparing quarters from two different years
    defeats the point of the comparison.
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
    """Make two endpoints of a range agree about year and basis before resolving.

    Without this, "Q1 to Q2 of 2024" resolved its first endpoint fiscally and its second
    on the calendar and returned a fifteen month span.
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


def _apply_modifier(raw: _Raw, text: str) -> _Raw:
    """Attach a leading or trailing modifier, extending the span to cover it."""
    for pattern, mod in _MOD_SUFFIXES:
        if mod is not None and pattern.match(text[raw.end : raw.end + 24]):
            m = pattern.match(text[raw.end : raw.end + 24])
            assert m is not None
            return _Raw(raw.rule, raw.spec.with_(mod=mod), raw.start, raw.end + m.end())
    before = text[max(0, raw.start - 24) : raw.start]
    for pattern, mod in _MOD_PREFIXES:
        m = pattern.search(before)
        if m:
            return _Raw(
                raw.rule,
                raw.spec.with_(mod=mod),
                raw.start - (len(before) - m.start()),
                raw.end,
            )
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_today(today: date | datetime | None, tz: tzinfo | None) -> date:
    """Work out which day "today" is.

    Every relative phrase this library understands is measured from a single day, so
    getting that day wrong is silently wrong everywhere at once. The failure mode is
    specific: a server running UTC answering a tenant in Asia/Kolkata at 04:00 local time
    on the 1st is still on the previous date, so "this month" quietly returns last month.

    Hence three ways to say it, in decreasing order of how much they leave to chance:

    * ``today=date(...)`` -- exact, and what tests should use.
    * ``tz=ZoneInfo("Asia/Kolkata")`` -- the current date *there*, not on this machine.
    * neither -- the machine's own local date, which is only right when the server and the
      reader share a timezone.

    A ``datetime`` is accepted wherever a ``date`` is, and combined with ``tz`` it converts
    first, answering "which day was that instant, for this reader".
    """
    if today is None:
        # datetime.now(None) is the naive local clock, so the no-argument path is
        # unchanged for callers who pass neither.
        return datetime.now(tz).date()
    # datetime subclasses date, so it has to be tested first.
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
    config: ParserConfig = DEFAULT_CONFIG,
    diagnostics: list[Diagnostic] | None = None,
) -> list[DateMatch]:
    """Find every date expression in ``text``.

    Spans index ``text`` as given, so a caller can highlight or replace without searching
    again. Pass a list as ``diagnostics`` to collect fragments that looked like dates but
    could not be resolved -- that is what distinguishes "nothing there" from "I saw
    something and could not read it".
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

    # Group matches into chains of anything joined by a link, so strictness can judge the
    # chain as a whole rather than each member in isolation.
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
    i = 0
    while i < len(raws):
        if i not in kept:
            i += 1
            continue
        j = i
        while j < len(links) and links[j] == "range" and (j + 1) in kept:
            j += 1
        if j > i:
            merged = _merge(raws[i], raws[j], day, config, body, norm, diagnostics)
            if merged is not None:
                matches.append(merged)
                i = j + 1
                continue
        single = _single(raws[i], day, config, body, norm, diagnostics)
        if single is not None:
            matches.append(single)
        i += 1
    return matches


def _emit(
    r: DateRange, start: int, end: int, norm: Normalized, confidence: float
) -> DateMatch:
    lo, hi = norm.to_original(start, end)
    return DateMatch(range=r, text=norm.original[lo:hi], span=(lo, hi), confidence=confidence)


def _single(
    raw: _Raw,
    day: date,
    cfg: ParserConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
) -> DateMatch | None:
    raw = _apply_modifier(raw, body)
    try:
        r = resolve(raw.spec, day, cfg)
    except UnresolvableSpec as exc:
        if diags is not None:
            diags.append(
                Diagnostic(body[raw.start : raw.end], (raw.start, raw.end), raw.rule, str(exc))
            )
        return None
    return _emit(r, raw.start, raw.end, norm, raw.spec.confidence)


def _merge(
    a: _Raw,
    b: _Raw,
    day: date,
    cfg: ParserConfig,
    body: str,
    norm: Normalized,
    diags: list[Diagnostic] | None,
) -> DateMatch | None:
    """Resolve two endpoints as one span, wrapping to the next year if they invert."""
    sa, sb = _unify(a.spec, b.spec)
    try:
        ra, rb = resolve(sa, day, cfg), resolve(sb, day, cfg)
    except UnresolvableSpec as exc:
        if diags is not None:
            diags.append(Diagnostic(body[a.start : b.end], (a.start, b.end), "range", str(exc)))
        return None

    if ra.start is not None and rb.end is not None and rb.end < ra.start:
        # "Nov to Feb" and "Q4 to Q1" cross a year boundary: retry the far end one year on.
        # The year to bump is whatever resolve() would have defaulted to, which for a
        # fiscal quarter is a fiscal label rather than any calendar year in the result.
        assumed = default_year_for(sb, day, cfg)
        if assumed is not None:
            try:
                retried = resolve(sb.with_(year=assumed + 1), day, cfg)
            except UnresolvableSpec:
                retried = None
            if retried is not None and retried.end is not None and retried.end >= ra.start:
                rb = retried
        if rb.end is not None and rb.end < ra.start:
            if diags is not None:
                diags.append(
                    Diagnostic(
                        body[a.start : b.end],
                        (a.start, b.end),
                        "range",
                        "range ends before it starts",
                    )
                )
            return None

    grain = ra.grain if ra.grain == rb.grain else max(ra.grain, rb.grain, key=_grain_rank)
    merged = DateRange(ra.start, rb.end, grain, ra.basis)

    start = a.start
    before = body[max(0, a.start - 12) : a.start]
    lead = _RANGE_LEAD.search(before)
    if lead:
        start = a.start - (len(before) - lead.start())
    return _emit(merged, start, b.end, norm, min(a.spec.confidence, b.spec.confidence))


#: Coarsest grain wins when a range joins two different resolutions.
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
    config: ParserConfig = DEFAULT_CONFIG,
) -> DateMatch | None:
    """The first date expression in ``text``, or None."""
    found = parse(text, today=today, tz=tz, config=config)
    return found[0] if found else None


def diagnose(
    text: str,
    *,
    today: date | datetime | None = None,
    tz: tzinfo | None = None,
    config: ParserConfig = DEFAULT_CONFIG,
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
    config: ParserConfig = DEFAULT_CONFIG,
    formatter: Callable[[DateRange], str] | None = None,
) -> str:
    """Rewrite every date expression in ``text`` using ``formatter``.

    Only the matched phrase is replaced. The predecessor's patterns swallowed neighbouring
    filler words, so "sales report of Q1" came back as "sales <range>" with "report"
    silently deleted.
    """
    from .format import format_range

    render = formatter or format_range
    found = parse(text, today=today, tz=tz, config=config)
    out = text
    for match in reversed(found):
        lo, hi = match.span
        out = out[:lo] + render(match.range) + out[hi:]
    return out
