"""The Anthropic provider.

The only provider that makes a network call, and the only place in Dramatis that does.
Invariant 7 says nothing else may egress; this module is where that is enforceable and
where a review should look.

The vendor SDK is an optional dependency, imported lazily. Reading, rendering, diffing,
and exporting an existing analysis must work with it absent (Invariant 6), so importing
this package must never require it.
"""

from __future__ import annotations

import os
from typing import Any

from dramatis.providers import (
    ModelRequest,
    ModelResponse,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

DEFAULT_MODEL = "claude-opus-5"

# Above this, the SDK refuses a non-streaming request it estimates will outlive the HTTP
# timeout. Streaming past it is not an optimisation — it is the only way the call returns.
STREAMING_THRESHOLD = 16_000


def _sdk_or_none() -> Any | None:
    """Return the vendor SDK if it is importable, else None."""
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic


def _load_sdk() -> Any:
    """Return the vendor SDK, or explain how to install it."""
    anthropic = _sdk_or_none()
    if anthropic is None:
        raise ProviderError(
            "the anthropic package is not installed. Install it with "
            "`pip install 'dramatis[anthropic]'`, or use a different provider. "
            "Reading and exporting existing analyses never needs it."
        )
    return anthropic


def _translate(error: Exception, model: str) -> ProviderError | None:
    """Map a vendor exception onto the provider-agnostic hierarchy.

    Returns None for anything that is not a vendor exception, so a genuine bug propagates
    unchanged rather than being relabelled as a provider failure.
    """
    anthropic = _sdk_or_none()
    if anthropic is None:
        return None

    if isinstance(error, anthropic.AuthenticationError):
        return ProviderAuthError(
            "the provider rejected the credential. Check ANTHROPIC_API_KEY, or run "
            "`ant auth login`."
        )
    if isinstance(error, anthropic.PermissionDeniedError):
        return ProviderAuthError(f"the credential lacks access to {model!r}")
    if isinstance(error, anthropic.NotFoundError):
        return ProviderError(f"unknown model {model!r}")
    if isinstance(error, anthropic.RateLimitError):
        retry_after = None
        response = getattr(error, "response", None)
        if response is not None:
            try:
                retry_after = float(response.headers.get("retry-after", ""))
            except (TypeError, ValueError, AttributeError):
                retry_after = None
        return ProviderRateLimited("the provider is rate limiting", retry_after)
    if isinstance(error, anthropic.APIConnectionError):
        return ProviderUnavailable(f"could not reach the provider: {error}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return ProviderUnavailable(f"provider error {error.status_code}")
        return ProviderError(f"provider rejected the request: {error}")
    return None


class AnthropicProvider:
    """Calls the Anthropic Messages API with the user's own credential.

    Dramatis ships no key and brokers no billing. The credential is resolved by the SDK
    from the environment, and is never read, stored, or logged here.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        timeout: float | None = None,
    ) -> None:
        self.model = model
        self._timeout = timeout
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        anthropic = _load_sdk()
        options: dict[str, Any] = {}
        if self._timeout is not None:
            options["timeout"] = self._timeout
        try:
            # No api_key argument: the SDK resolves ANTHROPIC_API_KEY, an auth token, or a
            # logged-in profile. Passing one here would invite callers to hardcode it.
            self._client = anthropic.Anthropic(**options)
        except Exception as error:  # pragma: no cover - constructor rarely fails
            raise ProviderAuthError(f"could not construct an Anthropic client: {error}") from error
        return self._client

    @staticmethod
    def credential_available() -> bool:
        """Whether some credential is present, without revealing or validating it.

        An unset ANTHROPIC_API_KEY does not mean there is no credential — the SDK also
        accepts an auth token or a logged-in profile — so this is a hint for skipping
        live tests, never an authorisation check.
        """
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def _build_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            kwargs["system"] = request.system

        # effort and format share one container; omit it entirely when neither is set.
        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if request.output_schema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }
        if output_config:
            kwargs["output_config"] = output_config

        # No temperature, top_p, or top_k: current models reject them outright.
        return kwargs

    def complete(self, request: ModelRequest) -> ModelResponse:
        # The SDK is loaded by _ensure_client only when no client was injected, so a test
        # supplying a fake never needs the vendor package installed.
        client = self._ensure_client()
        kwargs = self._build_kwargs(request)

        try:
            if request.max_tokens > STREAMING_THRESHOLD:
                with client.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
            else:
                message = client.messages.create(**kwargs)
        except Exception as error:
            translated = _translate(error, self.model)
            if translated is None:
                raise
            raise translated from error

        return self._to_response(message)

    def _to_response(self, message: Any) -> ModelResponse:
        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(message, "usage", None)
        return ModelResponse(
            text=text,
            model=getattr(message, "model", self.model),
            provider=self.name,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            stop_reason=getattr(message, "stop_reason", None),
        )
