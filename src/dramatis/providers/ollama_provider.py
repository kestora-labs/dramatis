"""The Ollama provider: analysis without the manuscript leaving the machine.

The reason this exists is the reason people hold unpublished work back from tools like this
one. Invariant 7 permits egress to the user's chosen provider and nothing else; here the
chosen provider is a process on their own computer, so a full analysis completes with the
network cable out.

**No SDK, and no new dependency.** Ollama speaks JSON over HTTP and the standard library
posts JSON over HTTP. A local-only provider that first required a package from the internet
would be a joke at its own expense, and the smaller the transport the easier it is to check
that this module reaches exactly one host and no other.

**The host is checked and reported, never assumed.** `OLLAMA_HOST` can point anywhere, and a
remote Ollama is a perfectly ordinary thing to run — but it means the manuscript leaves the
machine, which is the one promise this provider exists to keep. `is_local` answers it and the
CLI says so out loud.

**Effort is not honoured, and that is stated rather than hidden.** Anthropic takes a
reasoning-effort dial; Ollama has none. Silently dropping it would let two runs record
different configurations while making identical calls — the mirror of the fault **D35** found
in `resolution_prompt_version`, where a run recorded what happened to it rather than what it
was asked to do. `honours_effort` is False, the CLI reports it, and the run parameters go on
recording what was asked because that is what they are for.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from dramatis.providers import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderUnavailable,
)

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1"
DEFAULT_TIMEOUT = 600.0
"""Ten minutes. A local model on consumer hardware is slow rather than broken, and a timeout
tuned for a hosted API would abandon a working machine part-way through a window."""

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})

Transport = Callable[[str, str, bytes | None, float], bytes]
"""How a request is sent: ``(method, url, payload, timeout) -> body``.

Injected by tests so nothing here needs a running Ollama. **The method is part of the
signature** because Ollama cares: `/api/chat` is a POST and `/api/tags` is a GET, and a fake
that ignored the verb hid a live bug where `available()` posted to `/api/tags`, got a 405,
and reported a perfectly healthy server as absent — which would have made the live test skip
itself forever on exactly the machines that could run it.
"""


def default_host() -> str:
    """Where Ollama is expected, honouring ``OLLAMA_HOST`` as the tool itself does.

    Ollama accepts a bare ``host:port`` as well as a URL; a scheme is added when missing so
    the two spellings reach the same place instead of one of them failing to parse.
    """
    configured = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not configured:
        return DEFAULT_HOST
    if "://" not in configured:
        configured = f"http://{configured}"
    return configured.rstrip("/")


def _send(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked by the caller
        url,
        data=payload,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return bytes(response.read())


class OllamaProvider:
    """Calls a local Ollama server. No key, no account, no egress beyond the chosen host."""

    name = "ollama"

    honours_effort = False
    """Ollama exposes no reasoning-effort dial.

    Read by the CLI so a run at ``--effort max`` against this provider is told that the
    setting reached nothing. The parameter is still recorded: run parameters describe what a
    run was configured to do, and a provider ignoring one does not unmake the choice.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        host: str | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
    ) -> None:
        # None means unspecified rather than "no model", for the reason the Anthropic
        # provider gives: callers forward an optional setting they did not choose, and a
        # plain default in the signature would be overwritten by it.
        self.model = model or DEFAULT_MODEL
        self.host = (host or default_host()).rstrip("/")
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self._transport = transport or _send

        scheme = urlparse(self.host).scheme
        if scheme not in ("http", "https"):
            raise ProviderError(
                f"{self.host!r} is not an http(s) address, so there is nothing to call. "
                "Set OLLAMA_HOST to something like http://127.0.0.1:11434."
            )

    @property
    def is_local(self) -> bool:
        """Whether the chosen host is this machine.

        The whole claim of this provider is that the text does not leave, and that claim is
        false against a remote Ollama. False here is not an error — running a model on a
        machine down the hall is reasonable — but it is something the user is told rather
        than left to infer from an environment variable they set months ago.
        """
        hostname = urlparse(self.host).hostname
        return hostname is not None and hostname in LOOPBACK

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def _call(
        self, path: str, body: dict[str, Any] | None = None, *, method: str = "POST"
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        try:
            raw = self._transport(method, self._url(path), payload, self.timeout)
        except urllib.error.HTTPError as error:
            raise self._from_status(error) from error
        except urllib.error.URLError as error:
            raise ProviderUnavailable(
                f"could not reach Ollama at {self.host}: {error.reason}. Is it running? "
                "Start it with `ollama serve`, or set OLLAMA_HOST to where it is."
            ) from error
        except TimeoutError as error:
            raise ProviderUnavailable(
                f"Ollama at {self.host} did not answer within {self.timeout:g}s. A local "
                "model on modest hardware can be slow; raise the timeout or use a smaller "
                "model."
            ) from error
        except OSError as error:
            raise ProviderUnavailable(f"could not reach Ollama at {self.host}: {error}") from error

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError(f"Ollama returned something that is not JSON: {error}") from error

        if not isinstance(decoded, dict):
            raise ProviderError(f"Ollama returned {type(decoded).__name__}, expected an object")
        if decoded.get("error"):
            raise self._from_message(str(decoded["error"]))
        return decoded

    def _from_status(self, error: urllib.error.HTTPError) -> ProviderError:
        detail = ""
        try:
            body = error.read().decode("utf-8", "replace")
            detail = str(json.loads(body).get("error", body))
        except Exception:  # pragma: no cover - the body is a courtesy, not a contract
            detail = detail or ""

        if error.code == 404:
            # A 404 from the chat endpoint means the model, not the route: Ollama answers
            # /api/chat whenever it is running at all. Decided on the status rather than on
            # the body, which may be empty, so the advice does not depend on the wording of
            # a message that varies between releases.
            return self._missing_model()
        if error.code >= 500:
            return ProviderUnavailable(f"Ollama error {error.code}: {detail}")
        return ProviderError(f"Ollama rejected the request ({error.code}): {detail}")

    def _missing_model(self) -> ProviderError:
        """The overwhelmingly common first-run failure, and `ollama pull` is all of the fix.

        Worth its own sentence: a bare 404 sends somebody to a search engine to find out
        that they have installed the tool but not a model.
        """
        return ProviderError(
            f"Ollama has no model named {self.model!r}. Install it with "
            f"`ollama pull {self.model}`, or choose one you already have with --model."
        )

    def _from_message(self, detail: str) -> ProviderError:
        if "not found" in detail.lower() or "try pulling" in detail.lower():
            return self._missing_model()
        return ProviderError(f"Ollama rejected the request: {detail}")

    def models(self) -> list[str]:
        """Every model installed on the server, newest first as Ollama reports them.

        Useful for telling "Ollama is not running" apart from "Ollama is running and empty",
        which are different problems with different fixes, and for a live test that should
        run against whatever the machine actually has rather than a name compiled in.
        """
        payload = self._call("/api/tags", method="GET")
        found = payload.get("models") or []
        return [
            str(entry["model"]) for entry in found if isinstance(entry, dict) and entry.get("model")
        ]

    def available(self) -> bool:
        """Whether the server answers at all, for skipping live tests.

        A hint, never an authorisation check: this provider has nothing to authorise.
        """
        try:
            # GET, because that is what Ollama serves here. Posting returns 405 and would
            # report a running server as absent.
            self._call("/api/tags", method="GET")
        except ProviderError:
            return False
        return True

    def _build_body(self, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # One reply, not a stream of fragments. Streaming would buy a progress bar and
            # cost the reassembly of every partial message.
            "stream": False,
            "options": {"num_predict": request.max_tokens},
        }
        if request.output_schema:
            # Ollama constrains decoding to a JSON Schema given here, the same job
            # `output_config.format` does on Anthropic. The caller validates regardless.
            body["format"] = request.output_schema
        # request.effort is deliberately not sent: see `honours_effort`. There is no field
        # for it, and inventing one that the server ignores would be worse than omitting it.
        return body

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = self._call("/api/chat", self._build_body(request))
        message = payload.get("message") or {}
        text = str(message.get("content") or "")

        return ModelResponse(
            text=text,
            model=str(payload.get("model") or self.model),
            provider=self.name,
            input_tokens=payload.get("prompt_eval_count"),
            output_tokens=payload.get("eval_count"),
            stop_reason=_stop_reason(payload.get("done_reason")),
        )


def _stop_reason(done_reason: Any) -> str | None:
    """Translate Ollama's ``done_reason`` into the vocabulary `ModelResponse` reads.

    ``length`` becomes ``max_tokens`` because that is what `ModelResponse.truncated` looks
    for, and a truncated reply reported as a finished one arrives at `response.json()` as
    "the model emitted malformed JSON" — sending the reader to the prompt rather than to the
    budget that actually ran out.

    Ollama has no refusal signal, so `ModelResponse.refused` is never true here. A local
    model that declines returns prose, which fails validation as it should.
    """
    if done_reason is None:
        return None
    return {"length": "max_tokens"}.get(str(done_reason), str(done_reason))
