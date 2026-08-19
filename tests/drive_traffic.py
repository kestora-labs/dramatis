"""Replaying recorded Google Drive traffic, and recording more of it.

Not a test module. The Drive parallel of `providers.cassette`, kept in the tests because
replaying HTTP has no use outside them — a user never wants a recorded Drive, only a real
one or none.

A traffic file is a list of exchanges keyed by `method` and the full URL. The address is the
whole key on purpose: a Drive request carries everything that determines its answer in its
query string, so an edited `q`, a changed `fields` list or a different export type simply
will not be found, and replay fails naming what it was asked for rather than serving an
answer to a question nobody asks any more. That is the same discipline, and the same failure
mode, as the model cassettes' request fingerprint.

**On what is in `tests/traffic/` today.** It is written to the documented shape of the Drive
v3 API rather than captured from a live account, and the file says so in its own `recorded`
field. `Recorder` is here so that replacing it with real traffic is one marked test run
against a real folder, and `TestTheTrafficIsHonestAboutItself` fails the moment a file claims
to be something it is not.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

TRAFFIC_VERSION = 1

TRAFFIC = Path(__file__).resolve().parent / "traffic"


class MissingExchange(AssertionError):
    """The code under test asked for something the traffic file does not hold."""


def _key(method: str, url: str) -> str:
    return f"{method} {url}"


class Traffic:
    """A file of recorded Drive exchanges."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.exchanges: dict[str, dict[str, Any]] = {}
        self.recorded = False
        self.note = ""

    @classmethod
    def load(cls, path: Path | str) -> Traffic:
        traffic = cls(path)
        if not traffic.path.is_file():
            raise MissingExchange(f"no traffic file at {traffic.path}")
        payload = json.loads(traffic.path.read_text(encoding="utf-8"))
        version = payload.get("traffic_version")
        if version != TRAFFIC_VERSION:
            raise MissingExchange(
                f"{traffic.path} is traffic version {version!r}, expected {TRAFFIC_VERSION}."
            )
        traffic.recorded = bool(payload.get("recorded"))
        traffic.note = str(payload.get("note") or "")
        traffic.exchanges = {
            _key(entry["method"], entry["url"]): entry for entry in payload.get("exchanges", [])
        }
        return traffic

    def save(self) -> None:
        payload = {
            "traffic_version": TRAFFIC_VERSION,
            "recorded": self.recorded,
            "note": self.note,
            "exchanges": sorted(self.exchanges.values(), key=lambda e: _key(e["method"], e["url"])),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def add(self, method: str, url: str, status: int, body: str) -> None:
        self.exchanges[_key(method, url)] = {
            "method": method,
            "url": url,
            "status": status,
            "body": body,
        }

    def __len__(self) -> int:
        return len(self.exchanges)


def _explain(traffic: Traffic, method: str, url: str) -> str:
    """Say what was asked for and what is nearest, not merely that nothing matched."""
    lines = [f"no recorded exchange for {method} {url} in {traffic.path.name}."]
    if not traffic.exchanges:
        lines.append("The file holds nothing. Re-record it.")
        return " ".join(lines)

    address, _, query = url.partition("?")
    nearest = [key for key in traffic.exchanges if key.startswith(f"{method} {address}")]
    if nearest:
        lines.append(f"The same address is recorded with a different query. Asked for: {query}")
        lines.append(f"Recorded: {sorted(nearest)[0].partition('?')[2]}")
    else:
        lines.append(f"That address is not recorded at all. Recorded: {sorted(traffic.exchanges)}")
    return " ".join(lines)


class Replay:
    """A `drive.Transport` served entirely from a traffic file. Reaches no network.

    Records what it was asked for, including the headers, so a test can assert that the
    credential was actually sent and that nothing was fetched for a document that was skipped.
    """

    def __init__(self, traffic: Traffic | Path | str) -> None:
        self.traffic = traffic if isinstance(traffic, Traffic) else Traffic.load(traffic)
        self.calls: list[dict[str, Any]] = []

    @property
    def urls(self) -> list[str]:
        return [call["url"] for call in self.calls]

    def __call__(self, method: str, url: str, headers: Any, timeout: float) -> bytes:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "timeout": timeout}
        )
        entry = self.traffic.exchanges.get(_key(method, url))
        if entry is None:
            raise MissingExchange(_explain(self.traffic, method, url))

        body = str(entry.get("body") or "")
        status = int(entry.get("status", 200))
        if status >= 400:
            # A real `urllib` error, so the source's status handling is exercised as written
            # rather than through a stand-in that happens to have the same attributes.
            raise urllib.error.HTTPError(
                url, status, "recorded", {}, io.BytesIO(body.encode("utf-8"))
            )
        return body.encode("utf-8")


class Recorder:
    """Wraps the real transport and writes every exchange to a traffic file.

    Reached only by a `live`-marked test, deliberately re-recording against a real Drive
    folder. Nothing in an ordinary run comes near it, because coming near it would mean
    contacting Google.

    The credential is never written down: only the method, the address, the status and the
    body are kept, and the body of a Drive listing carries names and identifiers rather than
    anything that could authorise a request. Record only from a folder you are willing to
    commit — a Doc's exported text is the user's corpus, exactly as a checkpoint's prompt is.
    """

    def __init__(self, traffic: Traffic, inner: Any = None) -> None:
        from dramatis.drive import _send

        self.traffic = traffic
        self.inner = inner or _send
        self.traffic.recorded = True

    def __call__(self, method: str, url: str, headers: Any, timeout: float) -> bytes:
        try:
            body = self.inner(method, url, headers, timeout)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.traffic.add(method, url, error.code, payload.decode("utf-8", "replace"))
            self.traffic.save()
            raise urllib.error.HTTPError(
                url, error.code, error.reason, error.headers, io.BytesIO(payload)
            ) from None
        self.traffic.add(method, url, 200, body.decode("utf-8"))
        self.traffic.save()
        return body
