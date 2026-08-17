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

from pathlib import Path
from typing import Any

from dramatis import __version__
from dramatis.passage import (
    PassageNotFound,
    StructureNotReproducible,
    open_evidence,
    spec_for_types,
)
from dramatis.schema import DOCUMENT_VERSION
from dramatis.segmentation import segment_text
from dramatis.store import Store

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7373

WEB_ROOT = Path(__file__).resolve().parents[2] / "web" / "dist"
"""Where the built client lives. Absent until `npm run build` has been run in web/."""


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

    if WEB_ROOT.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_ROOT / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def client(full_path: str) -> Any:
            candidate = WEB_ROOT / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_ROOT / "index.html")
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
