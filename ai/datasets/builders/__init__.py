"""Task builders.

Each module exposes ``build(executor) -> list[SFTSample]`` (or, for
``domain``, ``list[RawDocument]``). ``ai.datasets.build`` calls them in the
order listed in ``BUILDERS`` and records per-builder counts in the manifest.
"""

from ai.datasets.builders import analysis, comparison, domain, explanations, refusal, tool_calling

#: (name, module). Order is stable so a rebuild of the same version reproduces
#: the same sample ids in the same sequence.
BUILDERS = (
    ("tool_calling", tool_calling),
    ("explanations", explanations),
    ("analysis", analysis),
    ("comparison", comparison),
    ("refusal", refusal),
)

__all__ = ["BUILDERS", "analysis", "comparison", "domain", "explanations", "refusal", "tool_calling"]
