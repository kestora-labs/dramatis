#!/usr/bin/env python3
"""Regenerate the fixture source text for reference corpus A.

The committed text is the novel only. This script records exactly how it was derived from
the upstream file so the derivation is reproducible rather than asserted: it downloads the
upstream edition, verifies the transcriber's boilerplate markers, strips everything outside
them, normalises line endings, and reports the hash of both the upstream file and the
result.

The novel is in the public domain worldwide. The surrounding transcription boilerplate and
the distributor's trademark are not part of the work, and are removed rather than
redistributed.

Usage:
    python fixtures/a/fetch_source.py [--check]

    --check  regenerate into memory and compare against the committed file without
             writing, exiting non-zero on any difference.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "source" / "pride-and-prejudice.txt"

UPSTREAM = "https://www.gutenberg.org/cache/epub/1342/pg1342.txt"
START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

# Hash of the upstream file as retrieved on 2026-08-16. Upstream re-releases its editions
# from time to time; a mismatch means the transcription changed and the fixture's expectation
# floor should be re-checked before the new text is accepted.
UPSTREAM_SHA256 = "74f2665d6e6925fc2c17dec644bec9e87df478a0f1836822125e8acbb3777806"

# Hash of the committed result of extract(). Asserted by the fixture tests, so an accidental
# edit to the source text is caught rather than silently changing what the floor is checked
# against.
FIXTURE_SHA256 = "e3bb81d19b34dd917187e2836340b02dceb3dc751e18308092a0074bbb2118ab"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract(raw: str) -> str:
    """Return the work itself, with the transcriber's front and back matter removed."""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    starts = [n for n, line in enumerate(lines) if line.startswith(START_MARKER)]
    ends = [n for n, line in enumerate(lines) if line.startswith(END_MARKER)]
    if len(starts) != 1 or len(ends) != 1:
        raise SystemExit(
            f"expected exactly one start and one end marker, found {len(starts)} and {len(ends)}"
        )
    if ends[0] <= starts[0]:
        raise SystemExit("end marker precedes start marker")

    body = "\n".join(lines[starts[0] + 1 : ends[0]])
    return body.strip("\n") + "\n"


def fetch() -> str:
    with urllib.request.urlopen(UPSTREAM) as response:  # noqa: S310 - fixed https URL
        return response.read().decode("utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare without writing")
    args = parser.parse_args(argv)

    raw = fetch()
    upstream_hash = sha256(raw)
    if upstream_hash != UPSTREAM_SHA256:
        print(
            "warning: upstream hash changed\n"
            f"  expected {UPSTREAM_SHA256}\n"
            f"  got      {upstream_hash}",
            file=sys.stderr,
        )

    text = extract(raw)
    print(f"upstream sha256 : {upstream_hash}")
    print(f"fixture  sha256 : {sha256(text)}")
    print(f"fixture  chars  : {len(text)}")

    if args.check:
        committed = OUTPUT.read_text(encoding="utf-8")
        if committed != text:
            print("committed fixture differs from regenerated text", file=sys.stderr)
            return 1
        print("committed fixture matches")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
