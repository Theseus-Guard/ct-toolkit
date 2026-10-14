"""
Computational Theseus Toolkit
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Identity Continuity Guardrails for LLM Systems.

# Quick start:
#     from ct_toolkit import TheseusWrapper
    import openai

    client = TheseusWrapper(openai.OpenAI())
    response = client.chat("Merhaba!")
    print(response.content)
    print(response.divergence_score)
"""
from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig, CTResponse, CTStreamChunk
from ct_toolkit.core.kernel import ConstitutionalKernel
from ct_toolkit.core.compatibility import CompatibilityLayer
from ct_toolkit.core.exceptions import (
    AxiomaticViolationError,
    PlasticConflictError,
    IncompatibleProfileError,
    CriticalDivergenceError,
    ChainIntegrityError,
)

__version__ = "0.4.0"

__all__ = [
    "TheseusWrapper",
    "WrapperConfig",
    "CTResponse",
    "CTStreamChunk",
    "ConstitutionalKernel",
    "CompatibilityLayer",
    "AxiomaticViolationError",
    "PlasticConflictError",
    "IncompatibleProfileError",
    "CriticalDivergenceError",
    "ChainIntegrityError",
    "__version__",
]
