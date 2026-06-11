"""PseudonymisedLM — the ONLY authorised path to an LLM provider (AD-010).

Slice 0: pass-through stub.  Real Presidio NER wiring is the Reasoning lane's task.
"""

from __future__ import annotations

# TODO(pii): wire real Presidio anonymiser/deanonymiser before any live LLM calls
import dspy


class PseudonymisedLM(dspy.LM):
    """Wraps a DSPy LM, stripping PII before the call and restoring it after.

    Slice 0 implementation is a no-op pass-through — no text is transformed.
    Every module that needs an LLM MUST obtain it from here, never from dspy.LM directly.
    """

    def __init__(self, model: str, **kwargs: object) -> None:
        super().__init__(model=model, **kwargs)  # type: ignore[arg-type]

    def __call__(  # type: ignore[override]
        self, prompt: str | None = None, **kwargs: object
    ) -> list[dict[str, str]]:
        # TODO(pii): anonymise prompt before forwarding; deanonymise response after
        return super().__call__(prompt, **kwargs)  # type: ignore[no-any-return, arg-type]
