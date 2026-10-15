"""Accumulate token usage and cost in workflow graph state and per-stage pipeline summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class UsageMetadata:
    """Token usage and cost metadata."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0

    def __add__(self, other: UsageMetadata) -> UsageMetadata:
        """Return combined totals without mutating either operand."""
        return UsageMetadata(
            total_input_tokens=self.total_input_tokens + other.total_input_tokens,
            total_output_tokens=self.total_output_tokens + other.total_output_tokens,
            total_cost=self.total_cost + other.total_cost,
        )

    def with_added(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
    ) -> UsageMetadata:
        """Return a copy with additional usage totals."""
        return self + UsageMetadata(
            total_input_tokens=int(input_tokens or 0),
            total_output_tokens=int(output_tokens or 0),
            total_cost=float(cost or 0.0),
        )

    def to_dict(self) -> dict[str, int | float]:
        """Return the values as a dictionary."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageMetadata:
        """Convert serialized usage fields to numeric totals, defaulting missing fields to zero."""
        return cls(
            total_input_tokens=int(data.get("total_input_tokens", 0)),
            total_output_tokens=int(data.get("total_output_tokens", 0)),
            total_cost=float(data.get("total_cost", 0.0)),
        )

    @classmethod
    def from_state(cls, state: Any) -> UsageMetadata:
        """Read usage from a mapping or graph-state attributes."""
        if isinstance(state, dict):
            return cls.from_dict(state)
        return cls(
            total_input_tokens=int(getattr(state, "total_input_tokens", 0) or 0),
            total_output_tokens=int(getattr(state, "total_output_tokens", 0) or 0),
            total_cost=float(getattr(state, "total_cost", 0.0) or 0.0),
        )

    def format(self) -> str:
        """Return a formatted summary string."""
        summary = (
            f"Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}"
        )
        if self.total_cost > 0:
            summary = f"{summary}, Cost: ${self.total_cost:.4f}"
        return summary


EMPTY_USAGE = UsageMetadata()
type UsageTotals = dict[str, int | float]


def build_usage_update(
    state: Any,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float = 0.0,
) -> UsageTotals:
    """Build updated workflow usage fields by adding a delta to existing state."""
    return (
        UsageMetadata.from_state(state)
        .with_added(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        .to_dict()
    )
