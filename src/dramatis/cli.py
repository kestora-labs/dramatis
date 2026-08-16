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

from dramatis import __version__
from dramatis.ingest import IngestError, ingest_file
from dramatis.locate import STORE_FILENAME, StoreNotFound, resolve_store
from dramatis.providers import ProviderError
from dramatis.schema import schema_version
from dramatis.store import Store
from dramatis.validation import Issue, validate_file

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


def _run_ingest(args: argparse.Namespace) -> int:
    location = resolve_store(args.store)
    try:
        with Store(location.path) as store:
            result = ingest_file(
                store,
                args.path,
                work_title=args.work,
                collection_name=args.collection,
                creator=args.creator,
                language=args.language,
                label=args.label,
                role=args.role,
            )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

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


def _run_analyse(args: argparse.Namespace) -> int:
    from dramatis.extraction import ExtractionError
    from dramatis.pipeline import PipelineError, analyse
    from dramatis.providers.anthropic_provider import AnthropicProvider
    from dramatis.resolution import ResolutionError
    from dramatis.snapshot import SnapshotError
    from dramatis.verification import VerificationError

    provider = AnthropicProvider(model=args.model)

    try:
        # Analysis reads a project; it never brings one into existence. A read that
        # silently created an empty store would report success for work it did not do.
        path = resolve_store(args.store).require()
        with Store(path) as store:
            result = analyse(
                store,
                args.revision,
                provider,
                effort=args.effort,
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
    from dramatis.server import DEFAULT_HOST, ServerError, serve

    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
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
    ingest.add_argument("path", type=Path, metavar="FILE")
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
    analyse.add_argument("--model", default=None, help="model identifier to use")
    analyse.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default="medium",
        help="how much work the model should spend per window (default: medium)",
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
