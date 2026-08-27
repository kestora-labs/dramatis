"""The ``dramatis`` command line interface.

``validate`` checks documents against the published schema and ``ingest`` reads a text into
a project store. Neither requires a model, and neither reaches a network unless ``ingest`` is
given ``--drive`` — which is the only thing that makes it name a Drive source, and is never
inferred from what a path looks like. ``authorise`` is the browser consent that flag needs,
and it writes its credential outside every project.

``analyse`` calls a provider, and ``structure`` does too but only when asked with
``--ask``. Their provider imports are deferred so the other commands keep working when no
provider SDK is installed, and ``structure`` without ``--ask`` stays offline: looking at what
a folder holds should never cost anything.

Every command locates the project file rather than assuming it (see ``dramatis.locate``),
and only ``ingest`` may bring one into existence. ``status`` answers which project is in
use and what is in it, ``review`` and ``correct`` record what a person made of what a reading
proposed, ``merge`` and ``split`` settle who is who, ``continuity`` reports what the corpus
no longer agrees with itself about, and ``export`` hands a reading to somebody else's tool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dramatis import __version__
from dramatis.continuity import ContinuityError
from dramatis.continuity import as_json as continuity_as_json
from dramatis.continuity import report as continuity_report
from dramatis.correction import (
    CHARACTER_FIELDS,
    RELATION_FIELDS,
    CorrectionError,
    correction_as_json,
)
from dramatis.correction import as_json as corrections_as_json
from dramatis.correction import history as correction_history
from dramatis.correction import record as record_correction
from dramatis.export import FORMATS as EXPORT_FORMATS
from dramatis.identity import IdentityError
from dramatis.identity import describe as describe_decisions
from dramatis.identity import merge as merge_characters
from dramatis.identity import split as split_character
from dramatis.ingest import ingest_file, ingest_folder, ingest_source
from dramatis.locate import STORE_FILENAME, StoreNotFound, resolve_store
from dramatis.providers import Provider, ProviderError
from dramatis.review import STATUSES as REVIEW_STATUSES
from dramatis.review import ReviewError
from dramatis.review import as_json as review_as_json
from dramatis.review import history as review_history
from dramatis.review import overlay as review_overlay
from dramatis.review import record as record_review
from dramatis.schema import schema_version
from dramatis.sources import FileSystemSource, IngestError
from dramatis.store import COLLECTIVES_ARE_ACTORS, AmbiguousAliasError, Store
from dramatis.validation import Issue, validate_file

CHARACTER_CORRECTABLE = tuple(entry.name for entry in CHARACTER_FIELDS)
RELATION_CORRECTABLE = tuple(entry.name for entry in RELATION_FIELDS)

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


# -- structure ------------------------------------------------------------------------


def _corrections(pairs: list[str]) -> dict[str, str]:
    """Parse ``--set path=role`` arguments.

    A bad pair is refused rather than ignored. Somebody correcting a map is telling the tool
    it got something wrong, and dropping that on the floor would save the wrong answer.
    """
    from dramatis.structure import StructureError

    corrections: dict[str, str] = {}
    for pair in pairs:
        path, separator, role = pair.partition("=")
        if not separator or not path or not role:
            raise StructureError(f"--set wants PATH=ROLE, not {pair!r}")
        corrections[path] = role
    return corrections


def _print_structure(structure: Any) -> None:
    # ASCII only, for the reason IngestResult.summary gives: a Windows console under a
    # legacy code page renders typographic punctuation as replacement characters, and output
    # that looks corrupted is worse than output that looks plain.
    print(f"{structure.root} - {len(structure.documents)} documents")
    for plan in structure.documents:
        print()
        settled = " (confirmed)" if plan.role.settled else ""
        print(f"  {plan.path}  ({plan.characters:,} characters)")
        print(f"    role         {plan.role.value}{settled}")
        print(f"                 {plan.role.basis}")
        print(f"    addressing   {plan.addressing.value}")
        print(f"                 {plan.addressing.basis}")
        print(f"    revision of  {plan.revision_of.value or '(none)'}")
        print(f"                 {plan.revision_of.basis}")
        if len(plan.regions) > 1:
            for region in plan.regions:
                span = f"{region.starts_at:,}-{region.ends_at:,}"
                print(f"    region       {region.label} [{span}] -> {region.role.value}")


def _run_structure(args: argparse.Namespace) -> int:
    """Show what a folder appears to hold, and record what somebody says it holds.

    Without ``--ask`` this calls no model and reaches no network, so looking at a proposal
    costs nothing. Without ``--confirm`` it writes nothing.

    Deliberately not a conversation. An earlier version of the ingest prompt asked questions
    on stdin and fell over with EOFError wherever stdin was not a terminal, which is most
    places a CLI actually runs. Corrections arrive as ``--set`` arguments, which are also what
    a person can put in a script, read back later, or paste into a bug report.
    """
    from dramatis.structure import (
        StructureError,
        as_json,
        confirm,
        propose_structure,
        propose_with_model,
        restore,
        save,
    )

    if bool(args.path) == bool(args.drive):
        which = "both a path and --drive" if args.path else "neither a path nor --drive"
        print(f"error: name one corpus to look at; this named {which}.", file=sys.stderr)
        return 2

    needs_store = args.confirm or args.forget or args.store is not None
    store_path = None
    if needs_store:
        try:
            store_path = resolve_store(args.store).require()
        except StoreNotFound as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    try:
        # Whether this command reaches a network is decided by --drive and by nothing else,
        # exactly as it is for `ingest`. A path is never inspected to see whether it might be
        # a Drive address (**D59**).
        source = _drive_source(args.drive) if args.drive else FileSystemSource(args.path)
        named = args.drive or args.path

        if args.forget:
            # The source's root, not a resolved path: a Drive corpus has no path to resolve,
            # and resolving one would key the forget against something no map was saved under.
            with Store(store_path) as store:
                forgotten = store.forget_structure_map(source.root)
            print(f"forgot {forgotten} confirmed document(s) for {named}")
            return 0

        # Read the corpus once and pass the reading on. Every later step here wants the
        # same texts, and asking the source again for them would be a second read — free
        # for a folder, not free for a Drive folder.
        reading = source.read()
        structure = propose_structure(source, reading)
        texts = reading.texts

        if args.ask:
            from dramatis.providers.anthropic_provider import AnthropicProvider

            structure = propose_with_model(
                structure,
                texts,
                AnthropicProvider(model=args.model),
                effort=args.effort,
            )
        elif store_path is not None:
            with Store(store_path) as store:
                structure = restore(structure, store.structure_map(structure.root), texts)

        if args.confirm:
            # Corrections apply to whatever is on screen: the model's reading if one was
            # asked for, the saved answers otherwise. Both are things a person is agreeing to.
            structure = confirm(structure, _corrections(args.set or []))
            with Store(store_path) as store:
                saved = save(structure, store)
            print(f"confirmed and saved {saved} document(s) for {structure.root}", file=sys.stderr)
        elif args.set:
            print(
                "note: --set was given without --confirm, so nothing was saved. The map "
                "below is what confirming would record.",
                file=sys.stderr,
            )
            structure = confirm(structure, _corrections(args.set))

    except (IngestError, StructureError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ProviderError as error:
        print(f"error: the provider failed: {error}", file=sys.stderr)
        return 1

    if args.as_json:
        json.dump(as_json(structure), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_structure(structure)

    # stderr, like every other remark this CLI makes, so --json stays parseable.
    for note in structure.notes:
        print(f"note: {note}", file=sys.stderr)
    for path, why in structure.skipped:
        print(f"note: skipped {path}: {why}", file=sys.stderr)

    return 0


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
                "omitted": list(result.omitted),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    print(result.summary)
    created = "" if location.exists else "  (new project)"
    print(f"  store       {location.path}{created}")
    if getattr(args, "drive", None):
        # The root, not the address that was typed: it is what a confirmed structure map is
        # keyed by, and the two spellings are not the same string.
        print(f"  source      {_drive_root(args.drive)}")
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
    # Said separately from a skip, because it is a different fact: nothing failed to be read,
    # somebody said this file is not part of the work.
    for path in result.omitted:
        print(f"note: left out {path}: no part of the work", file=sys.stderr)

    return 0


def _drive_root(address: str) -> str:
    from dramatis.drive import folder_id, root_of

    return root_of(folder_id(address))


def _drive_source(address: str):
    """A Drive source with whatever credential this machine has cached (**4.14**).

    Built here rather than in `ingest` because this is the seam where "the run named a Drive
    source" is decided, and nothing below it may make that decision on a path's behalf.
    """
    from dramatis.drive import DriveSource
    from dramatis.google_auth import drive_credentials

    # The folder is parsed first, so a mistyped address is a message rather than a reason to
    # go looking for a credential and then fail about the credential instead.
    source = DriveSource(address)
    return DriveSource(source.folder, credentials=drive_credentials())


REVOKE_URL = "https://myaccount.google.com/permissions"


def _run_authorise(args: argparse.Namespace) -> int:
    """Consent once to read Google Drive, and say where the answer was put (**4.14**).

    Nothing here touches a project. The credential is written to the user's own configuration
    directory precisely because a project store is a thing people send to each other, and a
    refresh token must not travel in one.
    """
    from dramatis.google_auth import (
        CLIENT_SECRET_ENV,
        AuthError,
        ClientSecret,
        authorise,
        credential_path,
        forget_credential,
        load_credential,
        save_credential,
    )

    where = credential_path()

    if args.forget:
        forgotten = forget_credential(where)
        had = "forgot the Google credential at" if forgotten else "no Google credential at"
        print(f"{had} {where}")
        # Said every time, because the difference matters and is not obvious: deleting a
        # cached token stops this machine using the grant and does not end it at Google.
        print(
            f"note: the grant itself is still live. End it at {REVOKE_URL}.",
            file=sys.stderr,
        )
        return 0

    if args.status:
        try:
            credential = load_credential(where)
        except AuthError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"authorised: {where}")
        print(f"  scope     {credential.scope}")
        print(f"  client    {credential.client_id}")
        print(f"  obtained  {credential.obtained_at or '(unrecorded)'}")
        return 0

    secret_path = args.client_secret or os.environ.get(CLIENT_SECRET_ENV)
    if not secret_path:
        print(
            "error: no client secret. Dramatis ships no OAuth client of its own — a client "
            "identifier published in an open-source repository is a shared secret with the "
            "whole internet — so create one of type Desktop app in your own Google Cloud "
            "project, download its JSON, and pass it with --client-secret.",
            file=sys.stderr,
        )
        return 1

    try:
        credential = authorise(ClientSecret.load(secret_path))
        saved = save_credential(credential, where)
    except AuthError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Somebody walked away from a consent screen. 130 is the conventional exit for an
        # interrupt, and the sentence says what was not written rather than what was.
        print(file=sys.stderr)
        print("aborted; nothing was written.", file=sys.stderr)
        return 130

    print(f"authorised: {saved}")
    print(f"  scope     {credential.scope}")
    print()
    print("this file is not part of any project. Ingest a folder with:")
    print("  dramatis ingest --drive https://drive.google.com/drive/folders/<id>")
    return 0


def _run_ingest(args: argparse.Namespace) -> int:
    if bool(args.path) == bool(args.drive):
        which = "both a path and --drive" if args.path else "neither a path nor --drive"
        print(f"error: name one corpus to read; this named {which}.", file=sys.stderr)
        return 2

    location = resolve_store(args.store)
    collectives = _collectives_setting(args, location)

    # Whether this run reaches a network is decided *here*, by whether --drive was given, and
    # never by what the positional argument looks like. A path that happens to read like a
    # Drive address is a path: sniffing it would mean a typo could send somebody's folder
    # name to Google, which is exactly what 4.14 says must not be possible.
    corpus: Any
    if args.drive:
        try:
            corpus = _drive_source(args.drive)
        except IngestError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        folder = True
    else:
        # A folder and a file are one command, because which one a draft is kept in is a fact
        # about the writer's habits rather than a decision the user should have to spell out.
        corpus = args.path
        folder = Path(args.path).is_dir()

    try:
        with Store(location.path) as store:
            if collectives is not None and location.exists:
                _warn_if_changing_collectives(store, collectives)
            ingest = ingest_source if args.drive else (ingest_folder if folder else ingest_file)
            result = ingest(
                store,
                corpus,
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


# -- characters -----------------------------------------------------------------------


def _run_characters(args: argparse.Namespace) -> int:
    """The collection's cast, and which works each character appears in.

    Calls no model and reaches no network (Invariant 6): every answer here is arithmetic over
    snapshots already stored. Whoever spans most works is listed first, because that is the
    question a shared-universe registry is opened to ask.
    """
    from dramatis.registry import RegistryError, as_json, build_registry

    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        collections = store.list_collections()
        collection_id = args.collection
        if collection_id is None:
            if not collections:
                print("error: this project holds no collection", file=sys.stderr)
                return 1
            collection_id = str(collections[0]["id"])

        try:
            registry = build_registry(store, collection_id)
        except RegistryError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    if args.as_json:
        json.dump(as_json(registry), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    # ASCII only, for the reason IngestResult.summary gives: a Windows console under a legacy
    # code page renders typographic punctuation as replacement characters.
    works = len(registry.works)
    print(f"{registry.collection_name} - {len(registry)} characters across {works} works")
    for entry in registry.entries:
        if args.spanning and not entry.spans:
            continue
        where = ", ".join(
            f"{appearance.work_title} ({appearance.relations} "
            f"{'relation' if appearance.relations == 1 else 'relations'})"
            for appearance in entry.appearances
        )
        print()
        print(f"  {entry.name}  [{entry.character.kind}]")
        if entry.character.aliases:
            print(f"    also        {', '.join(entry.character.aliases)}")
        # "in no current reading" rather than nothing: a character the newest snapshot of
        # every work leaves out is a real state, and a blank line reads as a bug.
        print(f"    appears in  {where or 'no current reading of any work'}")

    # Merges and splits last, and never only counted: after a merge the cast above shows one
    # character answering to two names, which is the outcome. Only this says a person decided
    # it, and that is the difference between a curated identity and one a model proposed.
    if registry.decisions:
        print()
        print(f"decisions   {len(registry.decisions)}")
        for line in describe_decisions(registry.decisions):
            print(f"  {line}")

    for title in registry.unanalysed:
        print(
            f"note: {title} has never been analysed, so nobody appears in it yet", file=sys.stderr
        )

    return 0


# -- review ---------------------------------------------------------------------------


def _newest_snapshot(store: Store) -> Any:
    """The most recently written snapshot in the project, or None.

    So that ``dramatis review`` with no argument reviews what was just analysed, which is
    what somebody sitting down after a run wants. Which snapshot it chose is always printed:
    a command that silently picked one of several would have somebody accepting a cast from a
    reading they are not looking at.
    """
    found = [
        snapshot for work in store.list_works() for snapshot in store.list_snapshots(work["id"])
    ]
    return max(found, key=lambda snapshot: (snapshot.created_at, snapshot.id), default=None)


def _run_review(args: argparse.Namespace) -> int:
    """Show or set where human review of a reading's nodes and edges stands.

    Calls no model and reaches no network (Invariant 6). Setting a status writes a decision
    beside the snapshot; the snapshot itself is immutable and is never touched.
    """
    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.character and args.relation:
        print("error: name a --character or a --relation, not both", file=sys.stderr)
        return 1

    kind = "character" if args.character else "relation" if args.relation else None
    subject = args.character or args.relation

    if args.status and subject is None:
        print("error: say what is being reviewed: --character ID or --relation ID", file=sys.stderr)
        return 1

    with Store(path) as store:
        snapshot = store.get_snapshot(args.snapshot) if args.snapshot else _newest_snapshot(store)
        if snapshot is None:
            if args.snapshot:
                print(f"error: no snapshot {args.snapshot!r}", file=sys.stderr)
            else:
                print("error: this project holds no snapshot yet", file=sys.stderr)
            return 1

        if args.status:
            try:
                decision = record_review(
                    store,
                    snapshot_id=snapshot.id,
                    subject_kind=str(kind),
                    subject_id=str(subject),
                    status=args.status,
                    note=args.note,
                )
            except ReviewError as error:
                print(f"error: {error}", file=sys.stderr)
                return 1

            if args.as_json:
                json.dump(
                    {
                        "snapshot_id": decision.snapshot_id,
                        "work_id": decision.work_id,
                        "kind": decision.subject_kind,
                        "id": decision.subject_id,
                        "status": decision.status,
                        "note": decision.note,
                        "decided_at": decision.decided_at,
                    },
                    sys.stdout,
                    indent=2,
                    ensure_ascii=False,
                )
                sys.stdout.write("\n")
                return 0

            print(f"{decision.subject_kind} {decision.subject_id} is now {decision.status}")
            if decision.note:
                print(f"  note: {decision.note}")
            return 0

        if args.history:
            if subject is None:
                print(
                    "error: --history is about one subject: name a --character or a --relation",
                    file=sys.stderr,
                )
                return 1
            past = review_history(store, snapshot.work_id, str(kind), str(subject))
            if args.as_json:
                json.dump(
                    [
                        {
                            "status": decision.status,
                            "note": decision.note,
                            "decided_at": decision.decided_at,
                            "decided_in": decision.snapshot_id,
                        }
                        for decision in past
                    ],
                    sys.stdout,
                    indent=2,
                    ensure_ascii=False,
                )
                sys.stdout.write("\n")
                return 0
            if not past:
                print(f"nobody has ruled on {kind} {subject} yet")
                return 0
            print(f"{kind} {subject}")
            for decision in past:
                print(f"  {decision.decided_at}  {decision.status}  in {decision.snapshot_id}")
                if decision.note:
                    print(f"      {decision.note}")
            return 0

        state = review_overlay(store, snapshot)
        work = store.get_work(snapshot.work_id) or {}

    if args.as_json:
        json.dump(review_as_json(state), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    # ASCII only, for the reason IngestResult.summary gives: a Windows console under a legacy
    # code page renders typographic punctuation as replacement characters.
    tally = ", ".join(f"{count} {name}" for name, count in state.counts.items() if count)
    print(f"{snapshot.id}  {work.get('title', snapshot.work_id)}")
    print(f"{len(state)} subjects - {tally}; {state.reviewed} ruled on by a person")
    print()
    for entry in state.subjects:
        if args.pending and entry.reviewed:
            continue
        print(f"  [{entry.status:<9}] {entry.kind:<9}  {entry.label}   {entry.id}")
        if entry.note:
            print(f"      note: {entry.note}")

    return 0


# -- correct --------------------------------------------------------------------------


def _correction_value(field: str, given: list[str]) -> Any:
    """Turn what a shell can pass into the type the field actually holds.

    A command line has only strings, and a correction stored as the wrong type would reach
    the schema as the wrong type. List fields take several words because that is how a shell
    says a list; everything else takes exactly one, and saying so beats silently using the
    first.
    """
    if field in ("aliases", "types"):
        return list(given)
    if len(given) != 1:
        raise CorrectionError(f"{field!r} takes one value, not {len(given)}")
    only = given[0]
    if field == "valence":
        try:
            return float(only)
        except ValueError as error:
            raise CorrectionError(f"{only!r} is not a number") from error
    if field == "directed":
        if only.lower() not in ("true", "false"):
            raise CorrectionError(f"{only!r} is not true or false")
        return only.lower() == "true"
    return only


def _print_correction(correction: Any) -> None:
    was = json.dumps(correction.was, ensure_ascii=False)
    now = json.dumps(correction.value, ensure_ascii=False)
    print(f"  {correction.subject_kind:<9} {correction.subject_id}")
    print(f"    {correction.field}: {was} -> {now}")
    if correction.note:
        print(f"    note: {correction.note}")


def _run_correct(args: argparse.Namespace) -> int:
    """Show or record corrections to a reading's characters and relations.

    Calls no model and reaches no network (Invariant 6). Recording one changes no stored
    snapshot: it is written into the graph by the next analysis.
    """
    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.character and args.relation:
        print("error: name a --character or a --relation, not both", file=sys.stderr)
        return 1

    kind = "character" if args.character else "relation" if args.relation else None
    subject = args.character or args.relation

    if args.field and subject is None:
        print(
            "error: say what is being corrected: --character ID or --relation ID",
            file=sys.stderr,
        )
        return 1
    if args.field and not args.value:
        print(f"error: --field {args.field} needs a --value", file=sys.stderr)
        return 1

    with Store(path) as store:
        snapshot = store.get_snapshot(args.snapshot) if args.snapshot else _newest_snapshot(store)
        if snapshot is None:
            if args.snapshot:
                print(f"error: no snapshot {args.snapshot!r}", file=sys.stderr)
            else:
                print("error: this project holds no snapshot yet", file=sys.stderr)
            return 1

        if args.field:
            try:
                correction = record_correction(
                    store,
                    snapshot_id=snapshot.id,
                    subject_kind=str(kind),
                    subject_id=str(subject),
                    field=args.field,
                    value=_correction_value(args.field, list(args.value)),
                    note=args.note,
                )
            except (CorrectionError, ReviewError) as error:
                print(f"error: {error}", file=sys.stderr)
                return 1

            if args.as_json:
                json.dump(correction_as_json(correction), sys.stdout, indent=2, ensure_ascii=False)
                sys.stdout.write("\n")
                return 0

            print(f"corrected {correction.subject_kind} {correction.subject_id}")
            print(
                f"  {correction.field}: "
                f"{json.dumps(correction.was, ensure_ascii=False)} -> "
                f"{json.dumps(correction.value, ensure_ascii=False)}"
            )
            # Said plainly, because the snapshot on screen will not change and somebody who
            # expected it to would think nothing had happened.
            print("  applies to the next analysis; this snapshot is unchanged")
            return 0

        if args.history:
            if subject is None:
                print(
                    "error: --history is about one subject: name a --character or a --relation",
                    file=sys.stderr,
                )
                return 1
            past = correction_history(store, snapshot.work_id, str(kind), str(subject))
            if args.as_json:
                json.dump(
                    [correction_as_json(entry) for entry in past],
                    sys.stdout,
                    indent=2,
                    ensure_ascii=False,
                )
                sys.stdout.write("\n")
                return 0
            if not past:
                print(f"nobody has corrected {kind} {subject}")
                return 0
            print(f"{kind} {subject}")
            for entry in past:
                print(f"  {entry.corrected_at}  {entry.field}")
                print(
                    f"      {json.dumps(entry.was, ensure_ascii=False)} -> "
                    f"{json.dumps(entry.value, ensure_ascii=False)}"
                )
                if entry.note:
                    print(f"      {entry.note}")
            return 0

        payload = corrections_as_json(store, snapshot.id, snapshot.work_id)
        standing = list(store.current_corrections(snapshot.work_id).values())
        conflicts = store.list_correction_conflicts(snapshot.work_id, snapshot_id=snapshot.id)
        work = store.get_work(snapshot.work_id) or {}

    if args.as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(f"{snapshot.id}  {work.get('title', snapshot.work_id)}")
    print(f"{len(standing)} standing correction(s), {len(conflicts)} disagreed with here")
    for correction in standing:
        print()
        _print_correction(correction)

    # Never only counted: a reading that argued with a correction and was overruled is the
    # thing this whole bullet exists to stop happening quietly.
    for conflict in conflicts:
        print()
        print(f"  disagreement  {conflict.subject_kind} {conflict.subject_id}")
        print(
            f"    this reading said {conflict.field}="
            f"{json.dumps(conflict.proposed, ensure_ascii=False)}; the correction "
            f"{json.dumps(conflict.held, ensure_ascii=False)} stood"
        )

    return 0


# -- merge and split ------------------------------------------------------------------


def _collection_for(store: Store, named: str | None) -> str | None:
    """Which collection to act on. A project holds one, so naming it is rarely needed."""
    collections = store.list_collections()
    if named is not None:
        return named if any(str(entry["id"]) == named for entry in collections) else None
    return str(collections[0]["id"]) if collections else None


def _report_decision(store: Store, collection_id: str, warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        print(f"note: {warning}", file=sys.stderr)
    remaining = len(store.list_characters(collection_id))
    print(f"  registry now holds {remaining} character(s)")
    print("  the next analysis of this collection reads it this way")


def _run_merge(args: argparse.Namespace) -> int:
    """Declare that two registered characters are one person.

    Calls no model and reaches no network (Invariant 6), and rewrites no snapshot: the
    registry is the mechanism, and the next reading resolves both names to one character.
    """
    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        collection_id = _collection_for(store, args.collection)
        if collection_id is None:
            print("error: this project holds no such collection", file=sys.stderr)
            return 1

        try:
            result = merge_characters(
                store,
                collection_id,
                into=args.into,
                absorb=args.character,
                note=args.note,
            )
        except (IdentityError, AmbiguousAliasError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        if args.as_json:
            json.dump(
                {
                    "action": result.decision.action,
                    "absorbed": result.absorbed.id,
                    "survivor": result.survivor.id,
                    "forms": list(result.decision.forms),
                    "aliases": list(result.survivor.aliases),
                    "warnings": list(result.warnings),
                },
                sys.stdout,
                indent=2,
                ensure_ascii=False,
            )
            sys.stdout.write("\n")
            return 0

        print(f"merged {result.absorbed.id} into {result.survivor.id}")
        print(f"  {result.survivor.name} now answers to {', '.join(result.survivor.surface_forms)}")
        _report_decision(store, collection_id, result.warnings)
        return 0


def _run_split(args: argparse.Namespace) -> int:
    """Declare that one registered character is two people."""
    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        collection_id = _collection_for(store, args.collection)
        if collection_id is None:
            print("error: this project holds no such collection", file=sys.stderr)
            return 1

        try:
            result = split_character(
                store,
                collection_id,
                character=args.character,
                forms=list(args.forms),
                name=args.name,
                note=args.note,
            )
        except (IdentityError, AmbiguousAliasError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        if args.as_json:
            json.dump(
                {
                    "action": result.decision.action,
                    "source": result.source.id,
                    "created": result.created.id,
                    "forms": list(result.decision.forms),
                    "warnings": list(result.warnings),
                },
                sys.stdout,
                indent=2,
                ensure_ascii=False,
            )
            sys.stdout.write("\n")
            return 0

        print(f"split {result.created.id} out of {result.source.id}")
        print(f"  {result.created.name} answers to {', '.join(result.created.surface_forms)}")
        print(f"  {result.source.name} answers to {', '.join(result.source.surface_forms)}")
        _report_decision(store, collection_id, result.warnings)
        return 0


# -- continuity -----------------------------------------------------------------------


def _run_continuity(args: argparse.Namespace) -> int:
    """Report what the corpus no longer agrees with itself about.

    Calls no model and reaches no network (Invariant 6), and changes nothing: every finding
    here has more than one right answer and choosing between them is the author's.
    """
    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        works = store.list_works()
        work_id = args.work
        if work_id is None:
            if not works:
                print("error: this project holds no work yet", file=sys.stderr)
                return 1
            if len(works) > 1:
                titles = ", ".join(f"{w['title']} ({w['id']})" for w in works)
                print(
                    f"error: this project holds several works, so name one: {titles}",
                    file=sys.stderr,
                )
                return 1
            work_id = str(works[0]["id"])

        try:
            found = continuity_report(
                store, work_id, snapshot_id=args.snapshot, against=args.against
            )
        except ContinuityError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        title = str((store.get_work(work_id) or {}).get("title", work_id))

    if args.as_json:
        json.dump(continuity_as_json(found), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    # ASCII only, for the reason IngestResult.summary gives: a Windows console under a legacy
    # code page renders typographic punctuation as replacement characters.
    print(f"{title}  ({found.snapshot_id})")
    if found.unchanged:
        # Not a finding and not a defect. Saying so beats an empty report, which reads as
        # "checked and clean" when what happened is "there was nothing to compare".
        print(f"  the reading is of the current text ({found.read_revision})")
    else:
        print(f"  reading of {found.read_revision} against {found.against_revision}")
    print(f"  {len(found)} finding(s)")

    for note in found.notes:
        print(f"note: {note}", file=sys.stderr)

    if found.stale_names:
        print()
        print("names the work has moved on from")
        for entry in found.stale_names:
            print(f"  {entry}")
            for location in entry.locations:
                print(f"    {location}")

    if found.lost_positions:
        print()
        print("claims pointing at a position that is gone")
        for entry in found.lost_positions:
            print(f"  {entry}")
            if entry.quotation:
                print(f'    "{entry.quotation}"')

    if found.superseded:
        print()
        print("documents replaced and still being read")
        for entry in found.superseded:
            print(f"  {entry}")

    if found.empty and not found.unchanged:
        print()
        print("nothing stale, nothing lost, nothing read twice.")

    return 0


# -- import ---------------------------------------------------------------------------


def _run_import(args: argparse.Namespace) -> int:
    """Read a Dramatis document somebody else produced into this project (**6.3**).

    Calls no model and reaches no network (Invariant 6). Writes nothing unless the whole
    document passes: a half-imported reading looks exactly like one somebody meant to have.
    """
    from dramatis.importer import ImportRefused, import_file

    location = resolve_store(args.store)
    try:
        # Like `ingest`, and unlike every other command: importing into a project that does
        # not exist yet is the ordinary case for somebody handed a file.
        path = location.path if location.explicit else location.require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        try:
            result = import_file(store, args.path)
        except ImportRefused as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    # Before the payload, and to stderr either way: the JSON carries the counts, but a person
    # watching a machine-readable run still needs to know the text did not come with the file.
    if result.documents_recorded:
        # Said every time, and not as a warning: it is what the format is *for*. A Dramatis
        # document says what was read without carrying somebody's manuscript, and a reader
        # who does not know that will think the evidence is broken when a passage will not
        # open.
        print(
            f"note: {result.documents_recorded} document(s) recorded without their text. A "
            "Dramatis document carries hashes, not the work. Quotations are readable; the "
            "passages around them need `dramatis ingest` of the same files.",
            file=sys.stderr,
        )
    if result.documents_already_here:
        print(
            f"note: {result.documents_already_here} document(s) were already here and were "
            "left as they are, text included.",
            file=sys.stderr,
        )

    if args.as_json:
        json.dump(
            {
                "snapshot_id": result.snapshot_id,
                "work_id": result.work_id,
                "collection_id": result.collection_id,
                "characters": result.characters,
                "relations": result.relations,
                "evidence": result.evidence,
                "documents_recorded": result.documents_recorded,
                "documents_already_here": result.documents_already_here,
                "already_present": result.already_present,
                "store": str(path),
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0

    print(result.summary)

    return 0


# -- export ---------------------------------------------------------------------------


KNOWN_EXPORT_SUFFIXES = (
    ".annotations.jsonld",
    ".nodes.csv",
    ".edges.csv",
    ".graphml",
    ".gexf",
    ".jsonld",
    ".json",
    ".csv",
)
"""Endings ``--output`` may already carry, longest-first so ``.nodes.csv`` wins over ``.csv``."""


def _export_target(base: Path, part: Any) -> Path:
    """Where one part of an export goes, given the name the caller asked for.

    ``--output graph.gexf`` writes ``graph.gexf`` and ``--output graph`` writes it too, so
    neither habit produces ``graph.gexf.gexf``. CSV is two parts and has no single name to
    take, so ``--output graph`` and ``--output graph.csv`` both write ``graph.nodes.csv`` and
    ``graph.edges.csv``.
    """
    if base.name.endswith(part.suffix):
        return base
    stem = base.name
    for known in KNOWN_EXPORT_SUFFIXES:
        if stem.endswith(known):
            stem = stem[: -len(known)]
            break
    return base.with_name(stem + part.suffix)


def _run_export(args: argparse.Namespace) -> int:
    """Write a stored snapshot out in a format some other tool can read.

    Calls no model and reaches no network (Invariant 6): everything exported is already in
    the store. Standing review decisions are applied on the way out, because they supersede
    what the snapshot declared (**5.1**) and an export is the copy that gets cited.
    """
    from dramatis.export import ExportError, export_document

    try:
        path = resolve_store(args.store).require()
    except StoreNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with Store(path) as store:
        if args.snapshot:
            snapshot = store.get_snapshot(args.snapshot)
            if snapshot is None:
                print(f"error: this project holds no snapshot {args.snapshot}", file=sys.stderr)
                return 1
        else:
            snapshot = _newest_snapshot(store)
            if snapshot is None:
                print("error: this project holds no reading to export", file=sys.stderr)
                return 1
            # To stderr, and always said: the export itself may be going to stdout, and a
            # command that silently picked one of several readings would have somebody
            # citing a graph they are not looking at.
            print(f"note: exporting {snapshot.id}, the newest reading here", file=sys.stderr)

        statuses = {
            (subject.kind, subject.id): subject.status
            for subject in review_overlay(store, snapshot).subjects
        }

    try:
        rendered = export_document(snapshot.document, args.format, review=statuses)
    except ExportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.output is None:
        part = rendered.single
        if part is None:
            names = ", ".join(entry.suffix for entry in rendered.parts)
            print(
                f"error: {args.format} is written as {len(rendered.parts)} files ({names}), "
                "so it needs somewhere to put them: --output NAME",
                file=sys.stderr,
            )
            return 1
        sys.stdout.write(part.text)
        return 0

    for part in rendered.parts:
        target = _export_target(args.output, part)
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the text written is the text rendered: Python would otherwise
        # translate every \n to \r\n on Windows and one export would differ from another by
        # nothing but the machine that made it.
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(part.text)
        print(f"wrote {target}")

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


def _provider_for(args: argparse.Namespace) -> Provider:
    """Build the provider the run was asked for, and say what it will and will not do.

    Both remarks go to stderr, and both are about promises the user is relying on. A local
    model that is not on this machine has quietly stopped being local, and an effort setting
    the provider cannot honour is a knob connected to nothing — which the run's parameters
    will nonetheless record, because they record what was asked (**D35**).
    """
    if args.provider == "ollama":
        from dramatis.providers.ollama_provider import OllamaProvider

        ollama = OllamaProvider(model=args.model, host=args.host)
        if not ollama.is_local:
            print(
                f"note: {ollama.host} is not this machine, so the text will leave it. "
                "Unset OLLAMA_HOST or pass --host for a fully local analysis.",
                file=sys.stderr,
            )
        if args.effort is not None and not ollama.honours_effort:
            print(
                f"note: --effort {args.effort} is recorded but not sent: Ollama has no "
                "reasoning-effort setting, so this run reads the same as any other effort.",
                file=sys.stderr,
            )
        return ollama

    from dramatis.providers.anthropic_provider import AnthropicProvider

    if args.host is not None:
        raise ProviderError("--host applies to --provider ollama; Anthropic's address is fixed")
    return AnthropicProvider(model=args.model)


def _run_analyse(args: argparse.Namespace) -> int:
    from dramatis.extraction import DEFAULT_WINDOW_CHARACTERS, ExtractionError
    from dramatis.pipeline import PipelineError, analyse
    from dramatis.providers.cassette import CheckpointProvider
    from dramatis.resolution import ResolutionError
    from dramatis.snapshot import SnapshotError
    from dramatis.verification import DEFAULT_MAX_REJECTION_RATE, VerificationError

    try:
        provider = _provider_for(args)
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

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
    # Broken out whenever reference material was read. One total would hide the split, and
    # the split is the finding: what a corpus declares and what it enacts are different
    # claims on different scales, and a reader who sees only a sum cannot tell them apart.
    if result.asserted.relations:
        print(
            f"    observed {len(result.aggregation.relations)}  ({result.aggregation.weight_basis})"
        )
        print(f"    asserted {len(result.asserted.relations)}  ({result.asserted.weight_basis})")

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
        located = resolve_store(args.store)
        # A named store that does not exist yet is allowed, and only when named: **4.9**
        # creates a project *from the browser*, and a serve that refused to start without one
        # would make that impossible — the acceptance is a project created without touching
        # the command line, and you cannot reach the browser if the server will not run. An
        # unnamed missing store still raises, because that is somebody in the wrong directory
        # rather than somebody starting something new.
        path = located.path if located.explicit else located.require()
        # Checked before the banner: announcing an address and then failing leaves the
        # last line on screen saying the server is up when it never started.
        ensure_available()
    except (StoreNotFound, ServerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not path.is_file():
        print(
            f"note: no project at {path} yet. Create one in the browser, or with "
            "`dramatis ingest`.",
            file=sys.stderr,
        )

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

    structure = subcommands.add_parser(
        "structure",
        help="show what a corpus appears to hold",
        description=(
            "Propose a structure map for a corpus: which documents appear to be revisions "
            "of which, how each is addressed, and what still needs deciding. Calls no model "
            "and writes nothing unless you ask it to with --ask or --confirm. "
            "With --drive, the corpus is a Google Drive folder instead of a local path; a "
            "map confirmed against it is keyed by its Drive root, so a later "
            "`ingest --drive` of the same folder reuses it rather than asking again."
        ),
    )
    structure.add_argument("path", type=Path, metavar="FOLDER", nargs="?")
    structure.add_argument(
        "--drive",
        metavar="FOLDER",
        help=(
            "a Google Drive folder address or identifier, instead of a local path. Needs "
            "`dramatis authorise` to have been run once."
        ),
    )
    structure.add_argument("--store", default=None, help=STORE_HELP)
    structure.add_argument(
        "--ask",
        action="store_true",
        help=(
            "read the documents with a model to propose what each one is and where its "
            "narrative begins. Needs a credential; writes nothing on its own."
        ),
    )
    structure.add_argument(
        "--set",
        action="append",
        metavar="PATH=ROLE",
        help=(
            "correct one document, as PATH=narrative, PATH=reference, or PATH=excluded "
            "for a file that is in the folder but is no part of the work. Repeatable."
        ),
    )
    structure.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "save the map, corrections included, so later ingests of this folder do not ask "
            "again. Refuses while any document is still unknown."
        ),
    )
    structure.add_argument(
        "--forget",
        action="store_true",
        help="drop this folder's saved answers, so it is asked about again",
    )
    structure.add_argument(
        "--model",
        default=None,
        help="model identifier for --ask. Without this, the provider's default is used.",
    )
    structure.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default="medium",
        help="reasoning effort for --ask",
    )
    structure.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    structure.set_defaults(handler=_run_structure)

    authorise = subcommands.add_parser(
        "authorise",
        help="consent once to read Google Drive folders",
        description=(
            "Ask Google, in a browser, for read-only access to Drive, and cache the grant "
            "outside any project. Bring your own OAuth client of type Desktop "
            "app: Dramatis ships none, because a client identifier published in an "
            "open-source repository is a shared secret with the whole internet. The "
            "credential is written to your configuration directory and never into a project "
            "store, which is a file people send to each other."
        ),
    )
    authorise.add_argument(
        "--client-secret",
        metavar="FILE",
        help=(
            "the client_secret JSON downloaded from the Google Cloud console "
            "(or set DRAMATIS_GOOGLE_CLIENT_SECRET)"
        ),
    )
    authorise.add_argument(
        "--status",
        action="store_true",
        help="say whether this machine is authorised, and where the credential is",
    )
    authorise.add_argument(
        "--forget",
        action="store_true",
        help="delete the cached credential from this machine (does not revoke it at Google)",
    )
    authorise.set_defaults(handler=_run_authorise)

    ingest = subcommands.add_parser(
        "ingest",
        help="read a text into a project store",
        description=(
            "Read a plain-text file, hash it, and record it as a text revision. Ingesting "
            "the same content twice is a no-op: the revision identifier is derived from the "
            "content hash, so identical text always yields the same revision. "
            "With --drive, the corpus is read from a Google Drive folder instead; that flag "
            "is the only thing that makes this command reach a network, and a path is never "
            "inspected to see whether it might be one."
        ),
    )
    ingest.add_argument("path", type=Path, metavar="FILE|FOLDER", nargs="?")
    ingest.add_argument(
        "--drive",
        metavar="FOLDER",
        help=(
            "a Google Drive folder address or identifier, instead of a local path. Needs "
            "`dramatis authorise` to have been run once."
        ),
    )
    ingest.add_argument("--store", default=None, help=STORE_HELP)
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

    characters = subcommands.add_parser(
        "characters",
        help="the collection's cast, and which works each character appears in",
        description=(
            "List the character registry a collection shares across its works, with the "
            "works each character appears in and the snapshot that says so. Calls no model "
            "and writes nothing."
        ),
    )
    characters.add_argument("--store", default=None, help=STORE_HELP)
    characters.add_argument(
        "--collection",
        default=None,
        help="which collection to read. A project holds one, so this is rarely needed.",
    )
    characters.add_argument(
        "--spanning",
        action="store_true",
        help="only characters appearing in more than one work",
    )
    characters.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    characters.set_defaults(handler=_run_characters)

    review = subcommands.add_parser(
        "review",
        help="show or set where review of a reading's characters and relations stands",
        description=(
            "Everything a model returns is a proposal. This is the record of somebody "
            "having looked at one: accepted, corrected, rejected, or still proposed. "
            "Decisions are kept beside the snapshot, which stays immutable, and are keyed "
            "to the claim rather than to the document, so they outlive a re-analysis. "
            "Calls no model and writes nothing unless you name a --status."
        ),
    )
    review.add_argument(
        "snapshot",
        nargs="?",
        default=None,
        metavar="SNAPSHOT",
        help="which reading to review. Without this, the project's newest snapshot.",
    )
    review.add_argument("--store", default=None, help=STORE_HELP)
    review.add_argument("--character", default=None, metavar="ID", help="a node, by identifier")
    review.add_argument("--relation", default=None, metavar="ID", help="an edge, by identifier")
    review.add_argument(
        "--status",
        default=None,
        choices=REVIEW_STATUSES,
        help="record this decision about the named character or relation",
    )
    review.add_argument(
        "--note",
        default=None,
        help="why. Required with --status corrected, which is meaningless without it.",
    )
    review.add_argument(
        "--history",
        action="store_true",
        help="every decision ever taken about the named character or relation",
    )
    review.add_argument(
        "--pending",
        action="store_true",
        help="list only what nobody has ruled on yet",
    )
    review.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    review.set_defaults(handler=_run_review)

    correct = subcommands.add_parser(
        "correct",
        help="put right what a reading got wrong, so the correction outlives it",
        description=(
            "Replace what a reading said about a character or a relation. The correction is "
            "recorded against the reading it was made on and written into every snapshot "
            "built afterwards, so it survives re-analysis instead of having to be made again. "
            "Where a later reading disagrees, the correction stands and the disagreement is "
            "reported rather than swallowed. Calls no model, and changes no stored snapshot."
        ),
    )
    correct.add_argument(
        "snapshot",
        nargs="?",
        default=None,
        metavar="SNAPSHOT",
        help="the reading being corrected. Without this, the project's newest snapshot.",
    )
    correct.add_argument("--store", default=None, help=STORE_HELP)
    correct.add_argument("--character", default=None, metavar="ID", help="a node, by identifier")
    correct.add_argument("--relation", default=None, metavar="ID", help="an edge, by identifier")
    # Deliberately not a `choices` list. A field outside the vocabulary is usually one
    # somebody had a good reason to reach for — a weight, a piece of evidence, an id — and
    # `correction.check_field` answers each with why it is declined. `choices` would replace
    # all of that with "invalid choice", which is the one answer that teaches nothing.
    correct.add_argument(
        "--field",
        default=None,
        metavar="FIELD",
        help=(
            f"which field to put right. A character takes {', '.join(CHARACTER_CORRECTABLE)}; "
            f"a relation takes {', '.join(RELATION_CORRECTABLE)}."
        ),
    )
    correct.add_argument(
        "--value",
        nargs="+",
        default=None,
        metavar="V",
        help="the value it should hold. Several words for a list field (aliases, types).",
    )
    correct.add_argument("--note", default=None, help="why, for whoever reads this later")
    correct.add_argument(
        "--history",
        action="store_true",
        help="every correction ever made to the named character or relation",
    )
    correct.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    correct.set_defaults(handler=_run_correct)

    merge = subcommands.add_parser(
        "merge",
        help="declare that two registered characters are one person",
        description=(
            "Give one character's names to another and retire it. The registry is the whole "
            "mechanism: the next analysis resolves every one of those names to the surviving "
            "character, so the graph comes out merged without any snapshot being rewritten. "
            "The retired character keeps its row so an identifier in an older snapshot can "
            "still be traced. Calls no model."
        ),
    )
    merge.add_argument("character", metavar="ID", help="the character to absorb, by identifier")
    merge.add_argument(
        "--into", required=True, metavar="ID", help="the character that survives, by identifier"
    )
    merge.add_argument("--store", default=None, help=STORE_HELP)
    merge.add_argument(
        "--collection",
        default=None,
        help="which collection to act on. A project holds one, so this is rarely needed.",
    )
    merge.add_argument("--note", default=None, help="why, for whoever reads this later")
    merge.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    merge.set_defaults(handler=_run_merge)

    split = subcommands.add_parser(
        "split",
        help="declare that one registered character is two people",
        description=(
            "Move some of a character's names onto a new character. Everything not named "
            "stays where it is, and at least one name must: moving every name is a rename "
            "rather than a split. As with merge, the registry is the mechanism and no stored "
            "snapshot changes. Calls no model."
        ),
    )
    split.add_argument("character", metavar="ID", help="the character to split, by identifier")
    split.add_argument(
        "--form",
        dest="forms",
        action="append",
        required=True,
        metavar="FORM",
        help="a surface form to move to the new character. Repeat for several.",
    )
    split.add_argument(
        "--name",
        default=None,
        help="what to call the new character. Without this, the first form moved.",
    )
    split.add_argument("--store", default=None, help=STORE_HELP)
    split.add_argument(
        "--collection",
        default=None,
        help="which collection to act on. A project holds one, so this is rarely needed.",
    )
    split.add_argument("--note", default=None, help="why, for whoever reads this later")
    split.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    split.set_defaults(handler=_run_split)

    continuity = subcommands.add_parser(
        "continuity",
        help="report what the corpus no longer agrees with itself about",
        description=(
            "Check the last reading of a work against the text as it now stands. Reports "
            "names a document stopped using that the corpus still uses elsewhere, claims "
            "pointing at a structural position the text no longer has, and documents another "
            "one revises that are still being read alongside it. Calls no model, and changes "
            "nothing: each of these has more than one right answer."
        ),
    )
    continuity.add_argument(
        "work",
        nargs="?",
        default=None,
        metavar="WORK",
        help="which work to check. Needed only where the project holds several.",
    )
    continuity.add_argument("--store", default=None, help=STORE_HELP)
    continuity.add_argument(
        "--snapshot",
        default=None,
        metavar="ID",
        help="the reading to check. Without this, the work's newest snapshot.",
    )
    continuity.add_argument(
        "--against",
        default=None,
        metavar="REVISION",
        help="the text to check it against. Without this, the work's newest text revision.",
    )
    continuity.add_argument("--json", dest="as_json", action="store_true", help="machine-readable")
    continuity.set_defaults(handler=_run_continuity)

    importing = subcommands.add_parser(
        "import",
        help="read a Dramatis document into this project",
        description=(
            "Read a snapshot document, one this or any other tool produced against the "
            "published schema, into a project. The document is validated before anything "
            "is written, and an identifier that already means something else here is "
            "refused rather than merged. A Dramatis document carries hashes rather than the "
            "text, so its documents arrive without their source; ingesting the same files "
            "later joins the two. Calls no model and reaches no network."
        ),
    )
    importing.add_argument("path", type=Path, metavar="FILE")
    importing.add_argument("--store", default=None, help=STORE_HELP)
    importing.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable results on stdout",
    )
    importing.set_defaults(handler=_run_import)

    export = subcommands.add_parser(
        "export",
        help="write a reading out in a format another tool can read",
        description=(
            "Export a stored snapshot as GraphML or GEXF for a network tool, as CSV node and "
            "edge lists for a spreadsheet, or as JSON-LD. Every graph format carries the "
            "weight basis, the provenance of each claim, and what the reading is a reading "
            "of. The evidence behind those claims is its own export: annotations writes every "
            "quotation as a W3C Web Annotation. Standing review decisions are applied on the "
            "way out. Calls no model and reaches no network."
        ),
    )
    export.add_argument(
        "format",
        choices=list(EXPORT_FORMATS),
        metavar="FORMAT",
        help=f"one of: {', '.join(EXPORT_FORMATS)}",
    )
    export.add_argument("--store", default=None, help=STORE_HELP)
    export.add_argument(
        "--snapshot",
        default=None,
        metavar="ID",
        help="the reading to export. Without this, the newest in the project.",
    )
    export.add_argument(
        "-o",
        "--output",
        default=None,
        type=Path,
        metavar="NAME",
        help=(
            "where to write. The format's extension is added if it is not already there, and "
            "csv writes NAME.nodes.csv and NAME.edges.csv. Without this, the export goes to "
            "stdout, which csv cannot do."
        ),
    )
    export.set_defaults(handler=_run_export)

    status = subcommands.add_parser(
        "status",
        help="say which project this is and what is in it",
        description=(
            "Report the project file in use, how it was found, and what it holds. Reads "
            "only, and never creates a project."
        ),
    )
    status.add_argument("--store", default=None, help=STORE_HELP)
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
            "credential for a hosted provider; --provider ollama runs on this machine and "
            "needs none."
        ),
    )
    analyse.add_argument("revision", metavar="REVISION_ID")
    analyse.add_argument("--store", default=None, help=STORE_HELP)
    analyse.add_argument(
        "--provider",
        choices=["anthropic", "ollama"],
        default="anthropic",
        help=(
            "which provider to call. 'ollama' runs a model on this machine, so the text "
            "never leaves it and no credential is needed."
        ),
    )
    analyse.add_argument(
        "--host",
        default=None,
        help="where Ollama is, if not $OLLAMA_HOST or http://127.0.0.1:11434",
    )
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
            "Serve a project in a browser on this machine. Reads snapshots, and accepts "
            "writes to project metadata, review status, corrections and registry decisions "
            "from the local client only — every write refuses a cross-origin request. A "
            "stored snapshot is never altered: a correction or a merge is applied when the "
            "next one is built. It never calls a model, and never leaves the loopback "
            "interface unless told to."
        ),
    )
    serve.add_argument("--store", default=None, help=STORE_HELP)
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
