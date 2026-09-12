"""Round-table deliberation.

Independent seats form their own theses in parallel, a mandated Devil's
Advocate attacks the majority, and a Chair synthesizes. The result is a
`Thesis` — an input to the trading pipeline, never an override of its gates.
"""

from roundtable.engine import RoundTable
from roundtable.seats import (
    ALL_SEATS,
    ANALYST,
    DEVILS_ADVOCATE,
    QUANT,
    RISK,
    ROUND_ONE_SEATS,
    ROUND_TWO_SEATS,
    SENTIMENT,
    Seat,
)
from roundtable.types import (
    Candidate,
    Consensus,
    SeatOpinion,
    Signal,
    Thesis,
)

__all__ = [
    "ALL_SEATS",
    "ANALYST",
    "Candidate",
    "Consensus",
    "DEVILS_ADVOCATE",
    "QUANT",
    "RISK",
    "ROUND_ONE_SEATS",
    "ROUND_TWO_SEATS",
    "RoundTable",
    "SENTIMENT",
    "Seat",
    "SeatOpinion",
    "Signal",
    "Thesis",
]
