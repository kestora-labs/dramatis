"""Provider-agnostic model access.

Dramatis orchestrates a model the user brings. This package defines the contract every
provider satisfies and the transport-independent request and response types, so nothing
above it imports a vendor SDK or knows which vendor is in use.

Three provider kinds exist, and the split is deliberate:

* a **live** provider, which calls a real API with the user's own key;
* a **scripted** provider, which returns canned responses and is what most tests use;
* **recording** and **replay** providers, which capture real exchanges to a cassette and
  play them back, so a small number of tests can assert against a real provider's actual
  response shape without a live call.

Scripted fakes never go stale but prove nothing about the provider. Recordings prove the
shape but go stale silently when a prompt changes. The fix for the second is the request
fingerprint: a replay whose recording does not match the request it is asked to serve
fails loudly, naming what changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Effort",
    "ModelRequest",
    "ModelResponse",
    "Provider",
    "ProviderError",
    "ProviderAuthError",
    "ProviderRateLimited",
    "ProviderUnavailable",
    "StaleRecordingError",
]

Effort = str  # "low" | "medium" | "high" | "xhigh" | "max" — provider-defined vocabulary


class ProviderError(Exception):
    """A model call failed. Callers handle this, never a vendor SDK's exception."""


class ProviderAuthError(ProviderError):
    """No usable credential, or the credential was rejected."""


class ProviderRateLimited(ProviderError):
    """The provider is throttling. Retryable."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(ProviderError):
    """The provider is unreachable or erroring. Retryable."""


class StaleRecordingError(ProviderError):
    """A replayed cassette does not contain the request it was asked to serve."""


@dataclass(frozen=True)
class ModelRequest:
    """One model call, expressed without reference to any provider's wire format.

    Deliberately carries no sampling parameters. ``temperature``, ``top_p`` and ``top_k``
    are rejected outright by current frontier models, and a field that only works on older
    ones would be a trap. Where those knobs would once have been used to trade determinism
    against variety, ``effort`` and the prompt do the work instead.
    """

    prompt: str
    system: str | None = None
    max_tokens: int = 8192
    effort: Effort | None = "medium"
    output_schema: dict[str, Any] | None = None
    """A JSON Schema the response must satisfy. Providers that support constrained
    decoding enforce it; the caller validates regardless."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Free-form labels for the request — not sent to the provider, recorded in cassettes
    so a human can tell which pipeline step an interaction belongs to."""

    def fingerprint(self) -> str:
        """A stable hash of everything that determines the response.

        ``metadata`` is excluded: it labels the call for humans and does not change what
        the model is asked. Everything else is included, so editing a prompt, a schema, or
        the token budget produces a different fingerprint — which is what makes a stale
        recording fail loudly instead of quietly serving the wrong answer.
        """
        payload = {
            "prompt": self.prompt,
            "system": self.system,
            "max_tokens": self.max_tokens,
            "effort": self.effort,
            "output_schema": self.output_schema,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    """What a provider returned, plus what a snapshot needs in order to be citable."""

    text: str
    model: str
    """The model identifier the provider reports having served, not the one requested."""

    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None

    @property
    def refused(self) -> bool:
        """Whether the provider declined the request on policy grounds.

        A refusal is a successful call with no usable content, not an error. Code that
        reads ``text`` without checking this will treat a decline as an empty extraction.
        """
        return self.stop_reason == "refusal"

    @property
    def truncated(self) -> bool:
        """Whether the reply stopped because it ran out of budget rather than finishing.

        A truncated reply is not a malformed one. Under constrained decoding it is a valid
        prefix of a valid answer, which is exactly what makes the distinction worth drawing:
        the text is well-formed as far as it goes, so the only thing that knows it is
        incomplete is this field.
        """
        return self.stop_reason == "max_tokens"

    def json(self) -> Any:
        """Parse the response text as JSON, raising ProviderError if it is not.

        Truncation is checked before parsing, and reported as itself. Left to the parser it
        surfaces as "expected JSON but got '{\"groups\":[{...'" — which reads as a model
        emitting nonsense, and sends the reader to the prompt rather than to the budget that
        actually ran out.
        """
        if self.truncated:
            raise ProviderError(
                f"{self.provider}/{self.model} reached its output token limit before "
                f"finishing, so the reply is cut off after {len(self.text)} characters "
                "rather than malformed. Raise max_tokens for this call."
            )
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as error:
            preview = self.text[:200]
            raise ProviderError(
                f"expected JSON from {self.provider}/{self.model} but got: {preview!r}"
            ) from error


@runtime_checkable
class Provider(Protocol):
    """Anything that can answer a ModelRequest."""

    name: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


def describe_run(
    provider: Provider, response: ModelResponse, prompt_version: str
) -> dict[str, Any]:
    """Build the analysis-run metadata a snapshot records, per Invariant 4.

    A citable result names the model that served it, the prompt version, and the
    parameters — not the model that was asked for, which may differ.
    """
    return {
        "provider": response.provider or provider.name,
        "model": response.model,
        "prompt_version": prompt_version,
    }
