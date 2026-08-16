"""Guards the licensing and governance files against drift.

Working rule 4: a commit that ships no executable code still carries an automated gate.
For licensing, the gate that matters is internal consistency — a project whose LICENSE,
package metadata, and README disagree about its licence is worse than one with no licence
file at all, because the disagreement is not visible until someone needs it to be right.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _prose(relative: str) -> str:
    """Read a file with runs of whitespace collapsed.

    Prose in these files is hard-wrapped, so a phrase may straddle a line break. Assert
    against the collapsed form or the tests break every time a paragraph is re-flowed.
    """
    return re.sub(r"\s+", " ", _read(relative))


@pytest.mark.parametrize(
    "relative",
    [
        "LICENSE",
        "NOTICE",
        "README.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "DECISIONS.md",
        "docs/LICENSE",
        "docs/index.md",
    ],
)
def test_governance_file_exists_and_is_not_empty(relative: str) -> None:
    path = ROOT / relative
    assert path.is_file(), f"{relative} is missing"
    assert path.read_text(encoding="utf-8").strip(), f"{relative} is empty"


def test_code_licence_is_apache_2_0_everywhere_it_is_stated() -> None:
    licence = _read("LICENSE")
    assert "Apache License" in licence
    assert "Version 2.0, January 2004" in licence

    metadata = tomllib.loads(_read("pyproject.toml"))
    assert metadata["project"]["license"] == "Apache-2.0"

    package_json = _read("web/package.json")
    assert '"license": "Apache-2.0"' in package_json

    assert "Apache-2.0" in _read("README.md")


def test_docs_are_cc_by_4_0_and_say_so_distinctly_from_code() -> None:
    docs_licence = _read("docs/LICENSE")
    assert "Creative Commons Attribution 4.0" in docs_licence
    assert "Apache License 2.0" in docs_licence, "docs licence must point at the code licence"

    # NOTICE is where a downstream redistributor looks; the split must be stated there.
    notice = _read("NOTICE")
    assert "CC BY 4.0" in notice
    assert "docs/" in notice


def test_contributing_states_the_commit_message_convention() -> None:
    contributing = _prose("CONTRIBUTING.md")
    assert "phase 1.1 —" in contributing, "commit prefix example must be present"
    assert "One bullet, one commit" in contributing
    assert "No commit lands red" in contributing


def test_contributing_repeats_the_two_most_broken_invariants() -> None:
    contributing = _prose("CONTRIBUTING.md")
    assert "medium-neutral" in contributing
    assert "No egress" in contributing


def test_code_of_conduct_is_attributed() -> None:
    coc = _prose("CODE_OF_CONDUCT.md")
    assert "Contributor Covenant" in coc
    assert "version 2.1" in coc
