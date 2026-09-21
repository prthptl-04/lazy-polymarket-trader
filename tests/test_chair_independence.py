"""The chair is told how independent the seats actually are.

Measured, not guessed. Across 16 live deliberations the mean pairwise Cohen's
kappa between seats was -0.022 over 21 pairs, with zero duplicate seats — the
committee is genuinely independent, and slightly anti-correlated if anything.

The chair did not know that, and repeatedly invented the opposite. From a real
transcript:

    the two bullish seats were shown to be reading the same single bar series
    and the same single-outlet headline feed, so their agreement is one framing
    counted twice rather than corroboration

Quant and Sentiment agree at kappa 0.04 — chance. The chair discarded a genuine
two-seat majority on a correlation that the fund's own measurement refutes.

`roundtable.agreement` has computed this from stored opinions since before the
committee ever ran; nothing consumed it. This wires it into the one place that
was guessing.

Gated on sample: below MIN_DELIBERATIONS_FOR_KAPPA the honest line is silence,
because a chair told "the seats are independent" on four debates would discount
a real correlation it should have caught.
"""

import pytest

from roundtable.engine import RoundTable


class _Store:
    def __init__(self, payload): self._p = payload
    def seat_agreement(self): return self._p


def _note(payload):
    return RoundTable(client=None, memory=None)._independence_note(payload)


def test_an_independent_committee_is_reported_as_independent():
    note = _note({"n_deliberations": 16, "mean_kappa": -0.022,
                  "duplicates": 0, "pairs": []})
    assert "independent" in note.lower()
    assert "-0.02" in note


def test_a_correlated_committee_is_reported_as_correlated():
    """The chair must still be able to discount echoed framing when it is real."""
    note = _note({"n_deliberations": 20, "mean_kappa": 0.78, "duplicates": 3,
                  "pairs": [{"a": "quant", "b": "sentiment", "kappa": 0.81}]})
    assert "0.78" in note
    assert "echo" in note.lower() or "one view" in note.lower()


def test_a_named_duplicate_pair_is_named():
    note = _note({"n_deliberations": 20, "mean_kappa": 0.4, "duplicates": 1,
                  "pairs": [{"a": "quant", "b": "sentiment", "kappa": 0.88}]})
    assert "quant" in note and "sentiment" in note


def test_too_few_debates_says_nothing_at_all():
    """A chair told "the seats are independent" on four debates would discount
    a real correlation it should have caught. Silence is the honest output."""
    assert _note({"n_deliberations": 4, "mean_kappa": 0.0, "duplicates": 0}) == ""


def test_a_missing_measurement_says_nothing():
    assert _note(None) == ""
    assert _note({}) == ""


def test_the_note_never_tells_the_chair_what_to_conclude():
    """It supplies a measurement. A line that said "therefore trust the
    majority" would be the analysis this seat exists to perform."""
    note = _note({"n_deliberations": 16, "mean_kappa": -0.022,
                  "duplicates": 0, "pairs": []})
    for banned in ("you should", "therefore trade", "trust the majority"):
        assert banned not in note.lower()
