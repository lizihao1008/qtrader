"""Talking to the local model, and failing safely when that does not work.

Every failure mode the brief names — timeout, service unavailable, malformed
output, schema violation — arrives here as the same thing: no usable verdict.
The caller then abstains. There is no path where a broken model produces a
trade, and no retry loop that could turn a 10-second timeout into a minute of
blocked decisions.

`Reply` always carries the measured latency, including for failures, because the
backtest replays it and a failure still consumed wall-clock time.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .schema import VERDICT_SCHEMA, Verdict

#: `Qwen3.6` needs think=False: with reasoning on it spends the whole token
#: budget in `thinking` and returns empty content. Measured ~8 s/call this way.
DEFAULT_MODEL = "Qwen3.6"
DEFAULT_TIMEOUT = 45.0


@dataclass(frozen=True)
class Reply:
    """One model call: what came back, how long it took, and what went wrong."""

    verdict: Verdict | None
    latency_s: float
    error: str = ""
    raw: str = ""
    model: str = ""
    options: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict is not None


class OllamaClient:
    """Schema-constrained calls to a local ollama model."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        temperature: float = 0.0,
        num_predict: int = 400,
        host: str | None = None,
    ):
        self.model = model
        self.timeout = float(timeout)
        self.options = {"temperature": temperature, "num_predict": num_predict}
        self._host = host
        self._client = None

    def _connect(self):
        if self._client is None:
            from ollama import Client  # imported lazily: the package is optional

            self._client = Client(host=self._host, timeout=self.timeout)
        return self._client

    def ask(self, prompt: str, image: bytes = b"") -> Reply:
        """One call. Never raises — every failure becomes a Reply with an error."""
        message = {"role": "user", "content": prompt}
        if image:
            message["images"] = [image]

        started = time.perf_counter()
        try:
            response = self._connect().chat(
                model=self.model,
                messages=[message],
                format=VERDICT_SCHEMA,
                think=False,
                options=self.options,
            )
            latency = time.perf_counter() - started
            body = (response.message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 — every transport failure is one case
            return Reply(None, time.perf_counter() - started,
                         error=f"{type(exc).__name__}: {exc}",
                         model=self.model, options=dict(self.options))

        if not body:
            return Reply(None, latency, error="empty response body",
                         model=self.model, options=dict(self.options))
        try:
            verdict = Verdict.parse(json.loads(body))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return Reply(None, latency, error=f"{type(exc).__name__}: {exc}", raw=body,
                         model=self.model, options=dict(self.options))

        return Reply(verdict, latency, raw=body, model=self.model,
                     options=dict(self.options))


class ScriptedClient:
    """A stand-in for tests and dry runs: no ollama, no model, no network."""

    def __init__(self, replies, *, latency_s: float = 0.0, model: str = "scripted"):
        self._replies = list(replies)
        self._latency = latency_s
        self.model = model
        self.calls: list[tuple[str, int]] = []

    def ask(self, prompt: str, image: bytes = b"") -> Reply:
        self.calls.append((prompt, len(image)))
        payload = self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        if isinstance(payload, Reply):
            return payload
        if isinstance(payload, Exception):
            return Reply(None, self._latency, error=str(payload), model=self.model)
        try:
            return Reply(Verdict.parse(payload), self._latency, raw=json.dumps(payload),
                         model=self.model)
        except (ValueError, TypeError) as exc:
            return Reply(None, self._latency, error=str(exc), model=self.model)
