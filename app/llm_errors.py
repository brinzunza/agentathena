"""
What went wrong with a model call, and what to do about it.

A provider failure used to be one of five messages, or the raw text. But "try again in a moment" and "your key is
wrong" are different problems with different remedies: one should be retried without bothering anyone, the other
only the user can fix. `classify` sorts a failure into a `Failure` that carries the remedy:

  - `retries`: how long to wait before each automatic retry. Empty means retrying can't help.
  - `fallback`: whether answering with another provider could work (only used if the user set one up, see
    llm_config.fallback_provider: their documents go to that provider, so it is never switched on silently).
  - `code` and `message`: what the browser shows. Codes the browser already acts on are kept.

Nothing here talks to a model; it only reads the error it was given.
"""

import re
import sys
from dataclasses import dataclass
from typing import Optional

from redaction import redact

# Seconds to wait before each retry. Overload and rate limits clear in seconds; a dropped connection, a moment.
BUSY_RETRIES = (2.0, 6.0, 15.0)
BRIEF_RETRIES = (1.0, 3.0)


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    retries: tuple = ()
    fallback: bool = False

    @property
    def retryable(self) -> bool:
        return bool(self.retries)


def status_of(error: BaseException) -> Optional[int]:
    """The HTTP status a provider SDK attached to its error, if it did."""
    for holder in (error, getattr(error, "response", None)):
        status = getattr(holder, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def _has(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def classify(error: BaseException, provider: str = "the model", python: Optional[str] = None) -> Failure:
    """Sort a failed model call. `provider` is how the provider is named to the user, e.g. 'Claude'."""
    python = python or sys.executable
    text = str(error)
    lowered = text.lower()
    status = status_of(error)

    if isinstance(error, ImportError) or "no module named" in lowered:
        # Not a provider problem: the Python running the backend lacks a package (typically an old
        # virtual environment). "No module named 'x'" alone doesn't say which Python or what to do.
        package = getattr(error, "name", None) or (text.split("'")[1] if text.count("'") >= 2 else "a package")
        return Failure("missing_package",
                       f"the Python running this app doesn't have {package} installed ({python}). "
                       f"install it with: {python} -m pip install -r backend/requirements.txt, then restart the app.")

    if _has(lowered, "insufficient_quota", "no credits", "exceeded your current quota", "credit balance is too low"):
        return Failure("no_credits", f"your {provider} account has no credits left. add credits, or switch to another "
                                     "provider (or local Ollama) in settings.", fallback=True)
    if status == 401 or _has(lowered, "invalid_api_key", "incorrect api key", "invalid x-api-key", "authentication_error"):
        return Failure("invalid_key", f"{provider} rejected the API key. update it in settings, or switch to another "
                                      "provider (or local Ollama).", fallback=True)
    if _has(lowered, "failed to connect to ollama", "connection refused"):
        return Failure("ollama_unreachable",
                       "can't reach Ollama. open ai provider and press run ollama, or add an API key.", fallback=True)

    # Too much for the model to read: waiting won't help, and another provider would need the same prompt
    if _has(lowered, "context length", "context_length_exceeded", "maximum context", "prompt is too long",
            "too many tokens", "reduce the length"):
        return Failure("context_too_long", "this conversation is too long for the model to read. use /compact to "
                                           "shrink what it remembers, or start a new chat.")
    if status == 404 or _has(lowered, "not_found_error", "model_not_found") or \
            re.search(r"model ['\"]?[\w.:/-]+['\"]? not found", lowered):
        return Failure("model_not_found", f"{provider} doesn't have the model that is set. choose another in ai "
                                          "provider.", fallback=True)

    if status == 429 or _has(lowered, "rate limit", "rate_limit", "too many requests"):
        return Failure("rate_limited", f"{provider} is limiting how fast requests can be sent. wait a moment and try "
                                       "again.", BUSY_RETRIES, fallback=True)
    if status in (503, 529) or _has(lowered, "overloaded", "service unavailable", "temporarily unavailable"):
        return Failure("overloaded", f"{provider} is overloaded right now. try again in a moment.", BUSY_RETRIES,
                       fallback=True)
    if status in (500, 502, 504) or _has(lowered, "internal server error", "bad gateway", "gateway timeout"):
        return Failure("server_error", f"{provider} had an error on its side. try again in a moment.", BRIEF_RETRIES,
                       fallback=True)
    if isinstance(error, TimeoutError) or _has(lowered, "timed out", "timeout"):
        return Failure("timeout", f"{provider} took too long to answer. try again.", BRIEF_RETRIES, fallback=True)
    if _has(lowered, "connection error", "connecterror", "name or service not known", "network is unreachable",
            "temporary failure in name resolution") or isinstance(error, ConnectionError):
        return Failure("network", f"couldn't reach {provider}. check your internet connection.", BRIEF_RETRIES,
                       fallback=True)

    return Failure("error", redact(text))
