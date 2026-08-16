"""A provider that returns canned responses.

What most of the test suite uses. It never touches a network, never goes stale, and costs
nothing — but it proves nothing about how a real provider behaves. That is what the
cassette providers are for.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from dramatis.providers import ModelRequest, ModelResponse, ProviderError

Responder = Callable[[ModelRequest], ModelResponse | str]


class ScriptedProvider:
    """Answers from a fixed script, in order, or from a callable.

    Passing a list yields each entry once, in sequence; running out is an error rather
    than a silent repeat, so a test that makes more calls than it scripted fails loudly.
    """

    name = "scripted"

    def __init__(
        self,
        responses: Iterable[ModelResponse | str | dict[str, Any]] | Responder,
        *,
        model: str = "scripted/none",
    ) -> None:
        self.model = model
        self.calls: list[ModelRequest] = []
        self._responder: Responder | None = None
        self._queue: list[ModelResponse | str | dict[str, Any]] = []

        if callable(responses):
            self._responder = responses
        else:
            self._queue = list(responses)

    def _coerce(self, value: ModelResponse | str | dict[str, Any]) -> ModelResponse:
        if isinstance(value, ModelResponse):
            return value
        text = value if isinstance(value, str) else json.dumps(value)
        return ModelResponse(text=text, model=self.model, provider=self.name)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)

        if self._responder is not None:
            return self._coerce(self._responder(request))

        if not self._queue:
            raise ProviderError(
                f"scripted provider ran out of responses after {len(self.calls)} call(s); "
                "the code under test made more model calls than the test scripted"
            )
        return self._coerce(self._queue.pop(0))

    @property
    def call_count(self) -> int:
        return len(self.calls)
