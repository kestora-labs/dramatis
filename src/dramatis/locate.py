"""Finding the project file.

A project is a single file, and the commonest way to lose an afternoon with one is to run
a command from the wrong directory. Before this module the default was a bare relative
path: a command run one folder over did not fail, it created a second empty project and
reported success.

So a store is *located* rather than assumed. Discovery walks up from the working directory
looking for the project file, the way git finds ``.git``, which means a command works
anywhere inside a project rather than only at its root. And a location knows whether the
file it names actually exists, so read paths can refuse instead of conjuring an empty one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STORE_FILENAME = "dramatis.sqlite"


@dataclass(frozen=True)
class StoreLocation:
    """Where a command decided the project file is, and how it decided."""

    path: Path
    exists: bool
    explicit: bool
    searched_from: Path | None = None

    @property
    def how(self) -> str:
        if self.explicit:
            return "named on the command line"
        if self.searched_from is None:
            return "the default in this directory"
        if self.exists:
            return f"found by searching upward from {self.searched_from}"
        return f"would be created here; nothing found above {self.searched_from}"

    def require(self) -> Path:
        """Return the path, or explain that there is no project here.

        Called by every command that reads. Creating a project is `ingest`'s job and
        nobody else's: a read that silently creates an empty store reports success for
        work it did not do.
        """
        if self.exists:
            return self.path
        raise StoreNotFound(
            f"no Dramatis project at {self.path}. "
            + (
                "Check the path."
                if self.explicit
                else (
                    f"Nothing was found in {self.searched_from} or any directory above it. "
                    "Start one with `dramatis ingest`, or point at an existing project "
                    "with --store."
                )
            )
        )


class StoreNotFound(Exception):
    """A command that reads was pointed at a project that does not exist."""


def find_upwards(start: Path, filename: str = STORE_FILENAME) -> Path | None:
    """Return the nearest ``filename`` at or above ``start``, if any."""
    current = start.resolve()
    for directory in (current, *current.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def resolve_store(
    explicit: Path | str | None = None, *, start: Path | str | None = None
) -> StoreLocation:
    """Decide which project file a command should use.

    An explicitly named path is taken as given and never searched for — being sent
    somewhere unexpected because a file happened to sit in a parent directory would be
    worse than the problem discovery solves.
    """
    if explicit is not None:
        path = Path(explicit)
        return StoreLocation(path=path, exists=path.is_file(), explicit=True)

    origin = Path(start) if start is not None else Path.cwd()
    found = find_upwards(origin)
    if found is not None:
        return StoreLocation(path=found, exists=True, explicit=False, searched_from=origin)

    return StoreLocation(
        path=origin / STORE_FILENAME, exists=False, explicit=False, searched_from=origin
    )
