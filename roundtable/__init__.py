"""Round-table deliberation.

Independent seats form their own theses in parallel, a mandated Devil's
Advocate attacks the majority, and a Chair synthesizes. The result is a
`Thesis` — an input to the trading pipeline, never an override of its gates.
"""

from roundtable.calibration import (
    Scorecard,
    SeatScore,
    ShrinkFit,
    fit_confidence_shrink,
    score_seats,
)
from roundtable.engine import RoundTable
from roundtable.corroboration import CorroborationReport, compare
from roundtable.corroborator import Corroborator
from roundtable.seats import (
    ALL_SEATS,
    ANALYST,
    CORROBORATOR,
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
    "CORROBORATOR",
    "CorroborationReport",
    "Corroborator",
    "compare",
    "Scorecard",
    "SeatScore",
    "ShrinkFit",
    "fit_confidence_shrink",
    "score_seats",
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
