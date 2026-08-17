"""The ``dramatis`` command line interface.

``validate`` checks documents against the published schema and ``ingest`` reads a text into
a project store. Neither requires a model or a network connection (Invariant 6), so both
work offline and always will.

``analyse`` is the exception, and the only command that calls a provider. Its imports are
deferred so the other commands keep working when no provider SDK is installed.

Every command locates the project file rather than assuming it (see ``dramatis.locate``),
and only ``ingest`` may bring one into existence. ``status`` answers which project is in
use and what is in it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dramatis import __version__
from dramatis.ingest import IngestError, ingest_file, ingest_folder
from dramatis.locate import STORE_FILENAME, StoreNotFound, resolve_store
from dramatis.providers import Provider, ProviderError
from dramatis.schema import schema_version
from dramatis.store import COLLECTIVES_ARE_ACTORS, Store
from dramatis.validation import Issue, validate_file

DEFAULT_EFFORT = "medium"

STORE_HELP = (
    f"project file to use. Without this, {STORE_FILENAME} is looked for in the current "
    "directory and every directory above it."
)


# -- validate -------------------------------------------------------------------------


def _report_text(path: Path, issues: list[Issue], stream) -> None:
    if not issues:
        print(f"ok    {path}", file=stream)
        return
    noun = "problem" if len(issues) == 1 else "problems"
    print(f"FAIL  {path} — {len(issues)} {noun}", file=stream)
    for issue in issues:
        print(f"      [{issue.kind.value}] {issue}", file=stream)


def _report_json(results: dict[Path, list[Issue]], stream) -> None:
    payload = {
        "schema_version": schema_version(),
        "results": [
            {
                "path": str(path),
                "valid": not issues,
                "issues": [
                    {"kind": issue.kind.value, "path": issue.path, "message": issue.message}
                    for issue in issues
                ],
            }
            for path, issues in results.items()
        ],
    }
    json.dump(payload, stream, indent=2)
    stream.write("\n")


def _run_validate(args: argparse.Namespace) -> int:
    results = {path: validate_file(path) for path in args.paths}

    if args.as_json:
        _report_json(results, sys.stdout)
    else:
        for path, issues in results.items():
            if issues or not args.quiet:
                _report_text(path, issues, sys.stderr if issues else sys.stdout)

    return 1 if any(results.values()) else 0


# -- ingest ---------------------------------------------------------------------------


def _warn_if_changing_collectives(store: Store, wanted: bool) -> None:
    """Say out loud that changing this breaks comparison with what is already there.

    Permitted, not prevented: someone who learns halfway through that their corpus has
    factions should be able to act on it. But snapshots either side of the change answer
    different questions, and the tool that will later refuse to diff them should not be the
    first place anyone hears about it.
    """
    current = store.get_setting(COLLECTIVES_ARE_ACTORS)
    if current is None or bool(current) == wanted:
        return

    existing = sum(len(store.list_snapshots(work["id"])) for work in store.list_works())
    was, now = ("counted", "not counted") if current else ("not counted", "counted")
    # stderr, not stdout: these are remarks about the run, and --json puts a parseable
    # document on stdout that a note would corrupt.
    print(f"note: collectives were {was} as actors in this project; now {now}.", file=sys.stderr)
    if existing:
        noun = "snapshot" if existing == 1 else "snapshots"
        print(
            f"      the {existing} existing {noun} answered the other question and will not "
            "compare with anything analysed from here.",
            file=sys.stderr,
        )


COLLECTIVES_DEFAULT_NOTE = (
    "note: collectives are not counted as actors (the default; set it with --collectives-as-actors)"
)
"""Said whenever the question could not be put to anybody.

One definition rather than one per path: a user who meets this in a pipeline and again in a
terminal that could not be read should be told the same thing, and two phrasings of one
fact drift the moment either is edited.
"""


def _ask_collectives_are_actors() -> bool | None:
    """Ask once, when a project is being created (D19).

    Returns None when the question could not be asked at all, which the caller treats
    exactly as it treats a non-interactive run: take the default and say so.

    Only asked at a terminal, and `isatty` is not sufficient to know there is one. A CI
    runner, an agent harness, an editor's terminal, or `dramatis ingest ... < /dev/null`
    under a pty all report a tty and then answer EOF. Before this returned None, that raised
    out of `input()` as a traceback in the middle of creating a project — the least helpful
    possible response to a question nobody was there to answer.

    On stderr, like the notes, so that piping a `--json` ingest somewhere never mixes a
    question into the document.
    """
    for line in (
        "\nThis project has not recorded whether collectives count as actors.",
        "  A family, a crew, a faction: a character in its own right, or only the people",
        "  named in it? Groups are usually noise in a novel and the point in a serial with",
        "  factions. It applies to the whole project, and is changeable later.",
    ):
        print(line, file=sys.stderr)

    print("Count collectives as actors? [y/N] ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip().lower() in {"y", "yes"}
    except EOFError:
        # Nobody there. The default is the same one a pipeline gets, and it is announced.
        print(file=sys.stderr)
        return None
    except KeyboardInterrupt:
        # Deliberately not the default. Somebody *is* there, and they interrupted a question
        # about how their project will be studied — reading that as "no" would record a
        # decision they declined to make, on a setting that makes snapshots either side of
        # it incomparable. 130 is the conventional exit for an interrupt.
        print("\naborted; nothing was written.", file=sys.stderr)
        raise SystemExit(130) from None


def _collectives_setting(args: argparse.Namespace, location) -> bool | None:
    """What to record, or None to leave the project's answer alone."""
    if args.collectives_are_actors is not None:
        return bool(args.collectives_are_actors)

    creating = not location.exists
    if not creating:
        return None

    answer = _ask_collectives_are_actors() if sys.stdin.isatty() else None
    if answer is None:
        print(COLLECTIVES_DEFAULT_NOTE, file=sys.stderr)
        return None
    return answer


def _report_folder_ingest(args: argparse.Namespace, location, result) -> int:
    """Print what a folder ingest did, including what it declined to read."""
    if args.as_json:
        json.dump(
            {
                "store": str(location.path),
                "store_created": not location.exists,
                "collection_id": result.collection_id,
                "work_id": result.work_id,
                "revision_id": result.revision_id,
                "sha256": result.sha256,
                "characters": result.characters,
                "already_present": result.already_present,
                "compared_with": result.compared_with,
                "documents": [
                    {
                        "path": entry.path,
                        "document_id": entry.document_id,
                        "sha256": entry.sha256,
                        "characters": entry.characters,
                        "state": entry.state,
                    }
                    for entry in result.documents
                ],
                "skipped": [{"path": path, "why": why} for path, why in result.skipped],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    print(result.summary)
    created = "" if location.exists else "  (new project)"
    print(f"  store       {location.path}{created}")
    print(f"  collection  {result.collection_id}")
    print(f"  work        {result.work_id}")
    print(f"  revision    {result.revision_id}")
    if result.compared_with:
        print(f"  against     {result.compared_with}")

    print()
    for entry in result.documents:
        print(f"  {entry.state:<9} {entry.path}")

    # On stderr, so a skipped file is visible when the output is being read and does not
    # contaminate a pipeline reading stdout.
    for path, why in result.skipped:
        print(f"note: skipped {path}: {why}", file=sys.stderr)

    return 0


def _run_ingest(args: argparse.Namespace) -> int:
    location = resolve_store(args.store)
    collectives = _collectives_setting(args, location)
    # A folder and a file are one command, because which one a draft is kept in is a fact
    # about the writer's habits rather than a decision the user should have to spell out.
    folder = Path(args.path).is_dir()
    try:
        with Store(location.path) as store:
            if collectives is not None and location.exists:
                _warn_if_changing_collectives(store, collectives)
            ingest = ingest_folder if folder else ingest_file
            result = ingest(
                store,
                args.path,
                work_title=args.work,
                collection_name=args.collection,
                creator=args.creator,
                language=args.language,
                label=args.label,
                role=args.role,
                collectives_are_actors=collectives,
            )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if folder:
        return _report_folder_ingest(args, location, result)

    if args.as_json:
        json.dump(
            {
                "store": str(location.path),
                "store_created": not location.exists,
                "collection_id": result.collection_id,
                "work_id": result.work_id,
                "document_id": result.document_id,
                "revision_id": result.revision_id,
                "sha256": result.sha256,
                "characters": result.characters,
                "already_present": result.already_present,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(result.summary)
        created = "" if location.exists else "  (new project)"
        print(f"  store       {location.path}{created}")
        print(f"  collection  {result.collection_id}")
        print(f"  work        {result.work_id}")
        print(f"  document    {result.document_id}")
        print(f"  revision    {result.revision_id}")

    return 0


# -- status ---------------------------------------------------------------------------


def _run_status(args: argparse.Namespace) -> int:
    location = resolve_store(args.store)
    try:
        path = location.require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        collections = store.list_collections()
        works = store.list_works()
        summary = {
            "store": str(path),
            "located": location.how,
            "store_version": store.store_version,
            "collections": [{"id": entry["id"], "name": entry["name"]} for entry in collections],
            "characters": store.count("characters"),
            "settings": store.settings(),
            "works": [],
        }
        for work in works:
            revisions = store.list_text_revisions(work["id"])
            snapshots = store.list_snapshots(work["id"])
            summary["works"].append(
                {
                    "id": work["id"],
                    "title": work["title"],
                    "creator": work.get("creator"),
                    "revisions": len(revisions),
                    "snapshots": [
                        {
                            "id": snapshot.id,
                            "label": snapshot.label,
                            "created_at": snapshot.created_at,
                            "text_revision_id": snapshot.text_revision_id,
                            "analysis_run_id": snapshot.analysis_run_id,
                            "characters": len(snapshot.document.get("characters", [])),
                            "relations": len(snapshot.document.get("relations", [])),
                        }
                        for snapshot in snapshots
                    ],
                }
            )

    if args.as_json:
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(f"project     {summary['store']}")
    print(f"            {summary['located']}")

    # Before the ingested-anything check: settings are properties of the study, and a
    # project may hold them before it holds a word of text.
    settings = summary["settings"]
    if settings:
        print()
        for position, (name, value) in enumerate(settings.items()):
            label = "settings" if position == 0 else ""
            # Rendered as JSON so a value reads as the type it is — `true` is the switch,
            # `"true"` is somebody's typo.
            print(f"{label:<12}{name} = {json.dumps(value, ensure_ascii=False)}")

    if not summary["collections"]:
        print("\nnothing ingested yet.")
        return 0

    for entry in summary["collections"]:
        print(f"\ncollection  {entry['name']}  ({entry['id']})")
    print(f"registry    {summary['characters']} character(s)")

    for work in summary["works"]:
        creator = f" — {work['creator']}" if work["creator"] else ""
        print(f"\nwork        {work['title']}{creator}  ({work['id']})")
        print(f"            {work['revisions']} revision(s), {len(work['snapshots'])} snapshot(s)")
        for snapshot in work["snapshots"]:
            label = f"  {snapshot['label']}" if snapshot["label"] else ""
            print(
                f"  {snapshot['id']}  {snapshot['created_at']}  "
                f"{snapshot['characters']} characters, {snapshot['relations']} relations{label}"
            )

    return 0


# -- analyse --------------------------------------------------------------------------


def _settings_like(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    """The analysis settings recorded by an existing snapshot's run.

    This is what makes 3.6's sentence sayable: *re-run an analysis against a new text
    revision while holding the prompt constant*. Two graphs are only evidence about a
    rewrite if the reading was held still between them, and "I passed the same flags" is not
    the same claim as "the run recorded the same configuration".

    A setting given explicitly on the command line still wins — the point is to make holding
    the analysis still the easy path, not to make changing it impossible — and the override
    is reported, because a re-run that quietly ignored half of what it was told to copy
    would be worse than one that never offered.
    """
    from dramatis.pipeline import PipelineError

    snapshot = store.get_snapshot(args.like)
    if snapshot is None:
        raise PipelineError(f"no snapshot {args.like!r} to copy the analysis from")

    run = store.get_analysis_run(snapshot.analysis_run_id)
    if run is None:
        raise PipelineError(f"snapshot {args.like!r} names a run that is gone")

    parameters = dict(run.get("parameters") or {})
    wanted = {
        key: parameters[key]
        for key in ("effort", "target_characters", "max_rejection_rate")
        if key in parameters
    }

    # An explicit --effort is an instruction, not an accident, so it overrides. Said out
    # loud because the result will not be comparable with the snapshot it was copied from.
    if args.effort is not None and "effort" in wanted and args.effort != wanted["effort"]:
        print(
            f"note: --effort {args.effort} overrides the {wanted['effort']} recorded by "
            f"{args.like}; this run will not be comparable with it.",
            file=sys.stderr,
        )
        wanted.pop("effort")

    print(
        f"note: holding the analysis as recorded by {args.like} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(wanted.items())) or 'nothing to copy'})",
        file=sys.stderr,
    )
    return wanted


def _run_analyse(args: argparse.Namespace) -> int:
    from dramatis.extraction import DEFAULT_WINDOW_CHARACTERS, ExtractionError
    from dramatis.pipeline import PipelineError, analyse
    from dramatis.providers.anthropic_provider import AnthropicProvider
    from dramatis.providers.cassette import CheckpointProvider
    from dramatis.resolution import ResolutionError
    from dramatis.snapshot import SnapshotError
    from dramatis.verification import DEFAULT_MAX_REJECTION_RATE, VerificationError

    provider: Provider = AnthropicProvider(model=args.model)
    checkpoint: CheckpointProvider | None = None
    if args.checkpoint is not None:
        checkpoint = CheckpointProvider(provider, args.checkpoint)
        provider = checkpoint

    try:
        # Analysis reads a project; it never brings one into existence. A read that
        # silently created an empty store would report success for work it did not do.
        path = resolve_store(args.store).require()
        with Store(path) as store:
            settings = _settings_like(store, args) if args.like else {}
            result = analyse(
                store,
                args.revision,
                provider,
                effort=settings.get("effort", args.effort or DEFAULT_EFFORT),
                target_characters=settings.get("target_characters", DEFAULT_WINDOW_CHARACTERS),
                max_rejection_rate=settings.get("max_rejection_rate", DEFAULT_MAX_REJECTION_RATE),
                label=args.label,
            )
    except (
        StoreNotFound,
        PipelineError,
        ExtractionError,
        ResolutionError,
        VerificationError,
        SnapshotError,
        ProviderError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    snapshot = result.snapshot
    document = snapshot.document

    # stderr, and only when it actually resumed something: on a first run the checkpoint has
    # nothing to say, and --json puts a parseable document on stdout that a note would spoil.
    if checkpoint is not None and checkpoint.served:
        print(
            f"note: {checkpoint.served} model call(s) served from {args.checkpoint}, "
            f"{checkpoint.fetched} made live.",
            file=sys.stderr,
        )

    if args.as_json:
        json.dump(document, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(f"snapshot    {snapshot.id}")
    print(f"  revision  {snapshot.text_revision_id}")
    print(f"  run       {snapshot.analysis_run_id}")
    print(f"  model     {document['analysis_runs'][0]['model']}")
    print(f"  characters {len(document['characters'])}")
    print(f"  relations  {len(document['relations'])}")

    verification = result.verification
    if verification.rejected:
        print(
            f"  rejected   {verification.rejected} of {verification.checked} quotations "
            "as not verbatim"
        )
    if verification.relocated:
        print(f"  relocated  {verification.relocated} quotation(s) to the right passage")
    for warning in result.warnings[:10]:
        print(f"  note      {warning}", file=sys.stderr)

    return 0


# -- serve ----------------------------------------------------------------------------


def _run_serve(args: argparse.Namespace) -> int:
    from dramatis.server import DEFAULT_HOST, ServerError, ensure_available, serve

    try:
        path = resolve_store(args.store).require()
        # Checked before the banner: announcing an address and then failing leaves the
        # last line on screen saying the server is up when it never started.
        ensure_available()
    except (StoreNotFound, ServerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.host != DEFAULT_HOST:
        print(
            f"warning: serving on {args.host}, not just this machine. A project file holds "
            "unpublished work — make sure that is what you intend.",
            file=sys.stderr,
        )

    try:
        print(f"Dramatis on http://{args.host}:{args.port}  (project: {path})")
        serve(path, host=args.host, port=args.port)
    except ServerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    return 0


# -- parser ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dramatis",
        description="Character relationship graphs for narrative works.",
    )
    parser.add_argument("--version", action="version", version=f"dramatis {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser(
        "validate",
        help="check documents against the Dramatis schema",
        description=(
            "Validate one or more snapshot documents against the published schema and "
            "check that their internal references resolve."
        ),
    )
    validate.add_argument("paths", nargs="+", type=Path, metavar="FILE")
    validate.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable results on stdout",
    )
    validate.add_argument("-q", "--quiet", action="store_true", help="report failures only")
    validate.set_defaults(handler=_run_validate)

    ingest = subcommands.add_parser(
        "ingest",
        help="read a text into a project store",
        description=(
            "Read a plain-text file, hash it, and record it as a text revision. Ingesting "
            "the same content twice is a no-op: the revision identifier is derived from the "
            "content hash, so identical text always yields the same revision."
        ),
    )
    ingest.add_argument("path", type=Path, metavar="FILE|FOLDER")
    ingest.add_argument("--store", type=Path, default=None, help=STORE_HELP)
    ingest.add_argument("--work", help="work title (default: derived from the filename)")
    ingest.add_argument("--collection", help="collection name (default: the work title)")
    ingest.add_argument("--creator", help="author of the work")
    ingest.add_argument("--language", help="BCP 47 language tag, e.g. en")
    ingest.add_argument("--label", help="human-facing name for this revision")
    ingest.add_argument(
        "--role",
        choices=["narrative", "reference"],
        default="narrative",
        help="whether the text enacts the story or describes it (default: narrative)",
    )
    collectives = ingest.add_mutually_exclusive_group()
    collectives.add_argument(
        "--collectives-as-actors",
        dest="collectives_are_actors",
        action="store_true",
        default=None,
        help=(
            "count a group — a family, a crew, a faction — as a character in its own right. "
            "Applies to the whole project. Without this or its negation, a new project asks."
        ),
    )
    collectives.add_argument(
        "--no-collectives-as-actors",
        dest="collectives_are_actors",
        action="store_false",
        help="report only the people named in a group (the default)",
    )
    ingest.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable results on stdout",
    )
    ingest.set_defaults(handler=_run_ingest)

    status = subcommands.add_parser(
        "status",
        help="say which project this is and what is in it",
        description=(
            "Report the project file in use, how it was found, and what it holds. Reads "
            "only, and never creates a project."
        ),
    )
    status.add_argument("--store", type=Path, default=None, help=STORE_HELP)
    status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable results on stdout",
    )
    status.set_defaults(handler=_run_status)

    analyse = subcommands.add_parser(
        "analyse",
        aliases=["analyze"],
        help="analyse a text revision and record a snapshot",
        description=(
            "Read a stored text revision with a model, verify every quotation against the "
            "source, and record the resulting graph as an immutable snapshot. Needs a "
            "credential: this is the only command that calls a provider."
        ),
    )
    analyse.add_argument("revision", metavar="REVISION_ID")
    analyse.add_argument("--store", type=Path, default=None, help=STORE_HELP)
    analyse.add_argument(
        "--model",
        default=None,
        help="model identifier to use. Without this, the provider's default is used.",
    )
    analyse.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default=None,
        help=(
            "how much work the model should spend per window (default: medium). Left "
            "unset it is a default; given, it is an instruction, and --like says so when "
            "the two disagree."
        ),
    )
    analyse.add_argument(
        "--like",
        metavar="SNAPSHOT_ID",
        default=None,
        help=(
            "read this snapshot's analysis settings and use them, so a re-run against a "
            "different revision holds the analysis still and any difference between the two "
            "graphs belongs to the text. Settings given explicitly still win, and are "
            "reported."
        ),
    )
    analyse.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=(
            "record every model call to this file and serve it back on a later run, so an "
            "interrupted analysis resumes instead of paying for the calls again. The file "
            "holds the text sent to the model — keep it beside the project, not in version "
            "control."
        ),
    )
    analyse.add_argument("--label", help="human-facing name for this snapshot")
    analyse.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="write the snapshot document to stdout",
    )
    analyse.set_defaults(handler=_run_analyse)

    serve = subcommands.add_parser(
        "serve",
        help="browse stored snapshots in a browser",
        description=(
            "Serve stored snapshots on this machine. Reads only: it never calls a model "
            "and never leaves the loopback interface unless told to."
        ),
    )
    serve.add_argument("--store", type=Path, default=None, help=STORE_HELP)
    serve.add_argument("--port", type=int, default=7373, help="port to listen on (default: 7373)")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (default: 127.0.0.1, this machine only)",
    )
    serve.set_defaults(handler=_run_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
