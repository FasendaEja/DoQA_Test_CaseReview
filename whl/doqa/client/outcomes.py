"""Wire enums of the Direct Autotest API."""

from typing import Optional


class Outcome:
    """Result/step status wire values."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BROKEN = "broken"


class StepKind:
    """Definition-step kind wire values."""

    STEP = "step"
    BEFORE = "before"
    AFTER = "after"


class LinkType:
    """Link type wire values."""

    RELATED = "related"
    DEFECT = "defect"
    REQUIREMENT = "requirement"
    BLOCKED_BY = "blocked_by"
    REPOSITORY = "repository"

    _ALL = (RELATED, DEFECT, REQUIREMENT, BLOCKED_BY, REPOSITORY)

    @classmethod
    def parse(cls, value: Optional[str]) -> Optional[str]:
        """Wire value for ``value`` (case-insensitive, ``-`` == ``_``); unknown -> None."""
        if value is None:
            return None
        v = value.strip().lower().replace("-", "_")
        return v if v in cls._ALL else None
