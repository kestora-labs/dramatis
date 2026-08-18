"""The local web server.

Serves stored snapshots to the browser and nothing else. Two constraints shape it.

**It hands back the stored document unchanged.** No view model, no reshaping, no computed
fields. What the client renders is what was archived and what would be cited, for the same
reason the store keeps the rendered document rather than a normalised copy — a second
representation is a second place for the truth to live, and they drift.

**It binds to the loopback interface.** A researcher's project file holds unpublished work.
Serving it on every interface by default would put a manuscript on the office network
because someone typed a command. Changing the interface is possible and deliberately
awkward, and says so.

The web framework is an optional dependency, imported lazily, so reading and validating a
project keeps working without it (Invariant 6).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from dramatis import __version__
from dramatis.diff import DiffError, diff_snapshots
from dramatis.passage import (
    PassageNotFound,
    StructureNotReproducible,
    open_evidence,
    spec_for_types,
)
from dramatis.registry import RegistryError, as_json, build_registry
from dramatis.schema import DOCUMENT_VERSION
from dramatis.segmentation import segment_text
from dramatis.snapshot import canonical_json
from dramatis.store import Store

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7373

DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[2] / "web" / "dist"
"""Where the built client lives in a source checkout: `web/dist` beside `src`, which is
where `npm run build` writes it. Absent until that has been run."""

WEB_ROOT_ENV = "DRAMATIS_WEB_ROOT"


def web_root() -> Path:
    """Where to serve the built client from, read at call time rather than at import.

    The default is computed from this file's location, which is correct only in a source
    checkout: a wheel installed into `site-packages` puts `server.py` three directories
    below a `web/dist` that is not there. `DRAMATIS_WEB_ROOT` overrides it, so an installed
    layout — the Docker image most of all — can point at the client it placed, for the same
    reason `OLLAMA_HOST` is not hardcoded: a path that is right in one deployment is wrong in
    another, and the deployment is what knows.
    """
    override = os.environ.get(WEB_ROOT_ENV)
    return Path(override) if override else DEFAULT_WEB_ROOT


class ServerError(Exception):
    """The server could not start."""


def _missing(package: str) -> ServerError:
    return ServerError(
        f"the web server needs {package}, which is not installed. Install the extras with "
        "`pip install 'dramatis[serve]'`. Validating and analysing a project does not need "
        "them."
    )


def _load_framework():
    """The web framework, needed to build the application."""
    try:
        import fastapi
    except ImportError as error:
        raise _missing("fastapi") from error
    return fastapi


def _load_server():
    """The ASGI server, needed only to actually listen on a port.

    Kept separate from the framework so building the application — which is all a test
    needs — does not require something only a running server uses.
    """
    try:
        import uvicorn
    except ImportError as error:
        raise _missing("uvicorn") from error
    return uvicorn


def ensure_available() -> None:
    """Raise if the server cannot run, before a caller announces that it has.

    Callers print a banner naming the address they are about to listen on. Discovering the
    missing dependency inside ``serve`` meant printing that banner and then failing, so the
    last thing on screen said the server was up when it never started.
    """
    _load_framework()
    _load_server()


def configuration_of(run: dict[str, Any]) -> str:
    """A digest of everything that makes a run the same *reading* as another.

    A run identifier includes when it ran, deliberately: two executions of one configuration
    are two runs, because models are not deterministic. That is right for identity and wrong
    for comparison — asked whether two snapshots differ by text or by analysis, an identifier
    that is unique per execution answers "both" every time, which is precisely the answer
    Invariant 4 exists to prevent.

    So comparison uses the configuration: the model, the prompt actually sent, the pipeline,
    and the parameters the run was given. Everything except when somebody pressed go.
    """
    material = canonical_json(
        {
            "model": run.get("model"),
            "provider": run.get("provider"),
            "prompt_version": run.get("prompt_version"),
            "prompt_sha256": run.get("prompt_sha256"),
            "pipeline_version": run.get("pipeline_version"),
            "parameters": run.get("parameters", {}),
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    document = snapshot.document
    return {
        "id": snapshot.id,
        "work_id": snapshot.work_id,
        "text_revision_id": snapshot.text_revision_id,
        "analysis_run_id": snapshot.analysis_run_id,
        "label": snapshot.label,
        "created_at": snapshot.created_at,
        "sha256": snapshot.sha256,
        "characters": len(document.get("characters", [])),
        "relations": len(document.get("relations", [])),
    }


def create_app(store_path: Path | str):
    """Build the application. One store per server; opened per request, not held."""
    fastapi = _load_framework()
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    path = Path(store_path)
    app = fastapi.FastAPI(
        title="Dramatis",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    def open_store() -> Store:
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no project file at {path}")
        return Store(path).open()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "application_version": __version__,
            "schema_version": DOCUMENT_VERSION,
            "store": str(path),
            "store_present": path.is_file(),
        }

    @app.get("/api/works")
    def works() -> list[dict[str, Any]]:
        store = open_store()
        try:
            rows = store.connection.execute(
                "SELECT id, collection_id, title, creator FROM works ORDER BY title"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            store.close()

    @app.get("/api/registry")
    def registry(collection_id: str | None = None) -> JSONResponse:
        """The collection's cast, and which works each character appears in (**4.5**).

        A project holds one collection, so ``collection_id`` is optional and the only one
        there is used when it is omitted. It is still accepted, because a caller that names
        what it wants and gets a 404 is better off than one that silently reads something
        else.

        Calls no model and reaches no network: this is arithmetic over stored snapshots
        (Invariant 6).
        """
        store = open_store()
        try:
            collections = store.list_collections()
            if collection_id is None:
                if not collections:
                    raise HTTPException(status_code=404, detail="this project holds no collection")
                collection_id = str(collections[0]["id"])
            try:
                return JSONResponse(as_json(build_registry(store, collection_id)))
            except RegistryError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
        finally:
            store.close()

    @app.get("/api/snapshots")
    def snapshots(work_id: str | None = None) -> list[dict[str, Any]]:
        store = open_store()
        try:
            if work_id:
                found = store.list_snapshots(work_id)
            else:
                rows = store.connection.execute(
                    "SELECT id FROM snapshots ORDER BY created_at DESC, id"
                ).fetchall()
                found = [s for s in (store.get_snapshot(r["id"]) for r in rows) if s]
            return [_snapshot_summary(snapshot) for snapshot in found]
        finally:
            store.close()

    @app.get("/api/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str) -> JSONResponse:
        store = open_store()
        try:
            found = store.get_snapshot(snapshot_id)
            if found is None:
                raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id!r}")
            # The stored document, byte for byte. Anything else would be a second
            # representation of the same graph.
            return JSONResponse(found.document)
        finally:
            store.close()

    @app.get("/api/works/{work_id}/lineage")
    def lineage(work_id: str) -> JSONResponse:
        """A work's snapshots, with its two time axes kept apart.

        Invariant 4: a snapshot binds a *text revision* to an *analysis run*, and the two
        must never be collapsed, because the user has to be able to tell whether a graph
        changed because the work changed or because the analysis did. A flat list of
        snapshots collapses them — it can say a graph moved but not which axis moved it.

        So the axes are returned as two ordered lists and the snapshots reference both,
        rather than as snapshot rows with the lineage folded in. Which arrangement the
        client draws is its business; what the API refuses to do is hand back a shape that
        has already thrown the distinction away.
        """
        store = open_store()
        try:
            work = store.get_work(work_id)
            if work is None:
                raise HTTPException(status_code=404, detail=f"no work {work_id!r}")

            revisions = store.list_text_revisions(work_id)
            snapshots = store.list_snapshots(work_id)

            return JSONResponse(
                {
                    "work": {
                        "id": work["id"],
                        "title": work["title"],
                        "creator": work.get("creator"),
                        "collection_id": work["collection_id"],
                    },
                    "text_revisions": [
                        {
                            "id": revision.id,
                            "label": revision.label,
                            "created_at": revision.created_at,
                            "sha256": revision.sha256,
                            "documents": len(revision.document_ids),
                        }
                        for revision in revisions
                    ],
                    "analysis_runs": [
                        {
                            "id": run["id"],
                            "model": run["model"],
                            "provider": run.get("provider"),
                            "prompt_version": run["prompt_version"],
                            "started_at": run.get("started_at"),
                            # What makes this run the same *reading* as another, as opposed
                            # to the same execution. A run identifier deliberately includes
                            # when it ran, so no two are ever equal and comparing by it
                            # would report every pair of snapshots as differing on both
                            # axes — which is the one answer Invariant 4 exists to avoid.
                            "configuration": configuration_of(run),
                        }
                        for run in store.list_analysis_runs(work_id)
                    ],
                    "snapshots": [_snapshot_summary(snapshot) for snapshot in snapshots],
                }
            )
        finally:
            store.close()

    @app.get("/api/diff")
    def diff(before: str, after: str) -> JSONResponse:
        """What changed between two snapshots, and which axis it can be laid at.

        Attribution comes first in the payload because it decides what the rest is worth: a
        change is only evidence about the work if the analysis was held still, and only
        evidence about the analysis if the text was.
        """
        store = open_store()
        try:
            first = store.get_snapshot(before)
            if first is None:
                raise HTTPException(status_code=404, detail=f"no snapshot {before!r}")
            second = store.get_snapshot(after)
            if second is None:
                raise HTTPException(status_code=404, detail=f"no snapshot {after!r}")

            try:
                result = diff_snapshots(first.document, second.document)
            except DiffError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

            return JSONResponse(
                {
                    "before": result.before,
                    "after": result.after,
                    "attribution": result.attribution,
                    "weights_comparable": result.weights_comparable,
                    "weight_basis": result.weight_basis,
                    "warnings": list(result.warnings),
                    "characters": [
                        {
                            "id": change.id,
                            "name": change.name,
                            "kind": change.kind,
                            "counterparts": list(change.counterparts),
                        }
                        for change in result.characters
                    ],
                    "relations": [
                        {
                            "id": change.id,
                            "source": change.source,
                            "target": change.target,
                            "kinds": list(change.kinds),
                            "weight_before": change.weight_before,
                            "weight_after": change.weight_after,
                            "delta": change.delta,
                            "types_before": list(change.types_before),
                            "types_after": list(change.types_after),
                        }
                        for change in result.relations
                    ],
                }
            )
        finally:
            store.close()

    @app.get("/api/snapshots/{snapshot_id}/passage")
    def passage(
        snapshot_id: str, relation: str, evidence: int = 0, revision: str | None = None
    ) -> JSONResponse:
        """The source text a piece of evidence points at, with the quotation located.

        Evidence is addressed by its position in the stored array rather than by sending
        the quotation back as a query parameter. A locator and a quotation in a URL would
        put lines of an unpublished manuscript into every access log that sees the request,
        and the server already holds the document they would be quoting from.
        """
        store = open_store()
        try:
            found = store.get_snapshot(snapshot_id)
            if found is None:
                raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id!r}")

            relations = found.document.get("relations", [])
            match = next((r for r in relations if r.get("id") == relation), None)
            if match is None:
                raise HTTPException(
                    status_code=404, detail=f"snapshot {snapshot_id!r} has no relation {relation!r}"
                )

            pieces = match.get("evidence", [])
            if not 0 <= evidence < len(pieces):
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"relation {relation!r} has {len(pieces)} pieces of evidence, "
                        f"so there is no piece {evidence}"
                    ),
                )

            piece = pieces[evidence]
            locator = piece.get("locator", {})

            # A snapshot is bound to the revision it analysed, and that is the default. A
            # caller may name a later one to ask the question 2.4 exists for: is this
            # evidence still where it was, now that the text has been edited?
            against = revision or found.text_revision_id
            if store.get_text_revision(against) is None:
                raise HTTPException(status_code=404, detail=f"no text revision {against!r}")

            text = store.revision_text(against)
            work = store.get_work(found.work_id) or {}
            try:
                spec = spec_for_types(work.get("segment_types"))
            except StructureNotReproducible as error:
                raise HTTPException(status_code=501, detail=str(error)) from error
            segmentation = segment_text(text, spec)

            try:
                opened = open_evidence(
                    segmentation,
                    locator.get("path", []),
                    piece.get("selector", {}),
                    document_id=locator.get("document_id"),
                )
            except PassageNotFound as error:
                raise HTTPException(status_code=404, detail=str(error)) from error

            return JSONResponse(
                {
                    "document_id": opened.document_id,
                    "path": opened.path,
                    "text": opened.text,
                    # Offsets rather than marked-up text: the client decides how a highlight
                    # looks, and no manuscript ever passes through a markup step here.
                    "quotation": (
                        None if not opened.located else {"start": opened.start, "end": opened.end}
                    ),
                    "widened": opened.widened,
                    "text_revision_id": against,
                    # How much the highlight is worth. A fuzzy match rendered identically to
                    # a verbatim one is a citation the reader has no way to weigh.
                    "anchor": {
                        "method": opened.method,
                        "similarity": opened.similarity,
                        "ambiguous": opened.ambiguous,
                        "moved": opened.moved,
                        "stored_path": opened.stored_path,
                    },
                }
            )
        finally:
            store.close()

    # Resolved once, when the app is built, so a single request cannot see the client half
    # mounted. A server that starts without a built client stays a working API either way.
    root = web_root()
    if root.is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def client(full_path: str) -> Any:
            candidate = root / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html")
    else:

        @app.get("/")
        def unbuilt() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": (
                        "the web client has not been built. Run `npm ci && npm run build` "
                        "in web/, then reload. The API under /api is already serving."
                    )
                },
                status_code=503,
            )

    return app


def serve(
    store_path: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the server until interrupted."""
    uvicorn = _load_server()
    uvicorn.run(create_app(store_path), host=host, port=port, log_level="info")
