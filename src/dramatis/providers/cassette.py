"""Recording and replaying real provider exchanges.

A cassette is a JSON file of request fingerprints paired with the responses a real
provider gave. Recording one costs a live call and money; replaying it is free, offline,
and deterministic.

The failure mode recordings are notorious for is going stale silently: someone edits a
prompt, the recording no longer corresponds to what the code now sends, and the suite
keeps passing against an answer to a question nobody asks any more. The fingerprint is
the fix — it covers every field that determines the response, so an edited prompt simply
will not be found, and replay fails naming what it was asked for.

Cassettes contain the full prompt text. Only ever commit cassettes recorded from
public-domain or synthetic fixtures; never from someone's unpublished work.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dramatis.providers import (
    ModelRequest,
    ModelResponse,
    Provider,
    StaleRecordingError,
)

CASSETTE_VERSION = 1


def _response_to_dict(response: ModelResponse) -> dict[str, Any]:
    return {
        "text": response.text,
        "model": response.model,
        "provider": response.provider,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "stop_reason": response.stop_reason,
    }


def _response_from_dict(payload: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        text=payload["text"],
        model=payload["model"],
        provider=payload["provider"],
        input_tokens=payload.get("input_tokens"),
        output_tokens=payload.get("output_tokens"),
        stop_reason=payload.get("stop_reason"),
    )


def _request_to_dict(request: ModelRequest) -> dict[str, Any]:
    return {
        "prompt": request.prompt,
        "system": request.system,
        "max_tokens": request.max_tokens,
        "effort": request.effort,
        "output_schema": request.output_schema,
        "metadata": dict(request.metadata),
    }


class Cassette:
    """A file of recorded interactions, keyed by request fingerprint."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.interactions: dict[str, dict[str, Any]] = {}
        self.recorded_at: str | None = None

    @classmethod
    def load(cls, path: Path | str) -> Cassette:
        cassette = cls(path)
        if not cassette.path.is_file():
            raise StaleRecordingError(
                f"no cassette at {cassette.path}. Record one with RecordingProvider, or "
                "use ScriptedProvider if the test does not need a real provider's shape."
            )
        payload = json.loads(cassette.path.read_text(encoding="utf-8"))
        version = payload.get("cassette_version")
        if version != CASSETTE_VERSION:
            raise StaleRecordingError(
                f"{cassette.path} is cassette version {version!r}, expected "
                f"{CASSETTE_VERSION}. Re-record it."
            )
        cassette.recorded_at = payload.get("recorded_at")
        cassette.interactions = {
            entry["fingerprint"]: entry for entry in payload.get("interactions", [])
        }
        return cassette

    def save(self) -> None:
        payload = {
            "cassette_version": CASSETTE_VERSION,
            "recorded_at": self.recorded_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "note": (
                "Recorded provider exchanges for offline replay. Contains full prompt text — "
                "only commit cassettes recorded from public-domain or synthetic fixtures."
            ),
            "interactions": sorted(self.interactions.values(), key=lambda e: e["fingerprint"]),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def add(self, request: ModelRequest, response: ModelResponse) -> None:
        fingerprint = request.fingerprint()
        self.interactions[fingerprint] = {
            "fingerprint": fingerprint,
            "request": _request_to_dict(request),
            "response": _response_to_dict(response),
        }

    def get(self, request: ModelRequest) -> ModelResponse | None:
        entry = self.interactions.get(request.fingerprint())
        return None if entry is None else _response_from_dict(entry["response"])

    def __len__(self) -> int:
        return len(self.interactions)


def _explain_miss(cassette: Cassette, request: ModelRequest) -> str:
    """Say what changed, not merely that nothing matched.

    A bare "not found" sends the reader looking for a missing file. Naming the field that
    differs from the nearest recording points straight at the edited prompt.
    """
    wanted = _request_to_dict(request)
    lines = [
        f"no recorded interaction in {cassette.path.name} for this request "
        f"(fingerprint {request.fingerprint()[:12]}).",
    ]

    if not cassette.interactions:
        lines.append("The cassette is empty. Re-record it.")
        return " ".join(lines)

    def distance(entry: dict[str, Any]) -> int:
        recorded = entry["request"]
        return sum(
            1 for key, value in wanted.items() if key != "metadata" and recorded.get(key) != value
        )

    nearest = min(cassette.interactions.values(), key=distance)
    differing = [
        key
        for key, value in wanted.items()
        if key != "metadata" and nearest["request"].get(key) != value
    ]

    if differing:
        lines.append(
            f"The closest recording differs in: {', '.join(sorted(differing))}. "
            "The recording is stale — re-record it with the live marker enabled."
        )
    else:
        lines.append("The closest recording appears identical; this may be a fingerprint bug.")
    return " ".join(lines)


class ReplayProvider:
    """Serves responses from a cassette. Never touches a network."""

    name = "replay"

    def __init__(self, cassette: Cassette | Path | str) -> None:
        self.cassette = cassette if isinstance(cassette, Cassette) else Cassette.load(cassette)
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        response = self.cassette.get(request)
        if response is None:
            raise StaleRecordingError(_explain_miss(self.cassette, request))
        return response


class RecordingProvider:
    """Wraps a live provider and writes every exchange to a cassette.

    Used only when deliberately re-recording. Nothing in the ordinary test run reaches it,
    because reaching it would mean making a live call.
    """

    name = "recording"

    def __init__(self, inner: Provider, cassette: Cassette | Path | str) -> None:
        self.inner = inner
        self.cassette = (
            cassette
            if isinstance(cassette, Cassette)
            else (Cassette.load(cassette) if Path(cassette).is_file() else Cassette(cassette))
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self.inner.complete(request)
        self.cassette.add(request, response)
        self.cassette.save()
        return response
