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
from dramatis.schema import DOCUMENT_VERSION
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
