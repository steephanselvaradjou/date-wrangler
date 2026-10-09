"""Guards on the things that only break at release time.

Nothing here tests behaviour. These exist because the README is baked into the wheel and
rendered on PyPI, so a stale line in it is published and cannot be corrected without
cutting another version -- which is exactly what happened: the status line still read
"early development (0.3.0)" on the 0.4.0 page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import date_wrangler

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

pytestmark = pytest.mark.skipif(
    not README.is_file(), reason="README.md is not shipped alongside an installed package"
)


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", date_wrangler.__version__)


def test_the_readme_repeats_no_version_number():
    """The released version is already in the PyPI page header, so repeating it in the body
    buys nothing and is one more thing to remember. Date examples are not version numbers,
    so only the prose is checked."""
    offenders = []
    for number, line in enumerate(README.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("|", "<!--")):
            continue  # the format table holds "15.03.2024"; the comment explains this rule
        if re.search(r"\b\d+\.\d+\.\d+\b", line):
            offenders.append(f"{number}: {line.strip()}")
    assert not offenders, "README pins a version that will go stale:\n" + "\n".join(offenders)


def test_the_status_matches_the_version():
    """The same trap from the other side: from 1.0 the README must stop saying the API may
    still change, and PyPI must stop calling the package alpha."""
    if int(date_wrangler.__version__.split(".")[0]) < 1:
        return
    readme = README.read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "early development" not in readme
    assert "Development Status :: 5 - Production/Stable" in pyproject
