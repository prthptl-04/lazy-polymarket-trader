"""Evidence that carries its own source and timestamp.

The idea is QuantMind's (LLMQuant/quant-mind, MIT): typed knowledge carries
"its own text, an `as_of` timestamp and a light source ref", so an artifact is
self-contained and can be queried by time. None of its code is used; the
principle is what transfers, because this repo had a real hole shaped exactly
like it.

What was wrong:

- A stored deliberation kept the opinions, the consensus and the tally — but
  NOT what the seats were shown. You could see what the committee decided and
  never why. For a fund that is the wrong half to keep.
- Every evidence block was a bare `tuple[str, ...]`. No seat could tell a quote
  from four seconds ago from news from six hours ago, and an LLM will treat
  both as current unless told otherwise.
- `roundtable.replay` is explicitly limited to replaying DECISIONS because the
  evidence was never stored. Persisting it does not lift that limit — you still
  cannot know what a seat WOULD have said — but it is the precondition for
  ever doing so, and it makes every past decision auditable now.

Staleness is surfaced, never silently dropped. Evidence the fund could not
refresh is still the best it has; hiding it would leave a seat reasoning from
nothing while believing it reasoned from something.
"""

import time

import pytest

from roundtable.knowledge import STALE_AFTER_SECONDS, SourceRef
from roundtable.types import Candidate


NOW = 1_800_000_000.0


def test_a_source_knows_how_old_it_is():
    ref = SourceRef(kind="news", source="Massive /v2/reference/news", as_of=NOW - 3600)
    assert ref.age_seconds(NOW) == 3600
    assert ref.describe(NOW).endswith("(60m ago)")


def test_undated_evidence_is_reported_as_undated_not_as_fresh():
    """The dangerous default. An unknown timestamp rendered as 'now' is a lie
    the seats have no way to catch."""
    ref = SourceRef(kind="news", source="somewhere", as_of=None)
    assert ref.age_seconds(NOW) is None
    assert "age unknown" in ref.describe(NOW)
    assert ref.is_stale(NOW) is True, "unknown age must be treated as stale"


def test_stale_evidence_is_flagged_rather_than_dropped():
    old = SourceRef(kind="news", source="s", as_of=NOW - STALE_AFTER_SECONDS - 1)
    assert old.is_stale(NOW)
    assert "STALE" in old.describe(NOW)


def test_fresh_evidence_is_not_flagged():
    assert not SourceRef(kind="quote", source="s", as_of=NOW - 5).is_stale(NOW)


def test_computed_evidence_is_never_stale():
    """Technicals are derived from the bars in this cycle. They are exactly as
    old as the bars, which have their own ref — a second staleness warning on
    the derivation would be noise."""
    ref = SourceRef(kind="technicals", source="computed", as_of=None, derived=True)
    assert ref.is_stale(NOW) is False
    assert "computed" in ref.describe(NOW)


# ---------------------------------------------------------------- the block

def test_the_evidence_block_states_its_provenance():
    """A seat that cannot see where a number came from cannot discount it."""
    c = Candidate(symbol="AAPL", price=100.0,
                  sentiment_notes=("a headline",),
                  sources=(SourceRef("news", "Massive news API", NOW - 60),))
    block = c.evidence_block(now=NOW)
    assert "PROVENANCE" in block
    assert "Massive news API" in block


def test_a_stale_source_warns_inside_the_block_the_seats_read():
    c = Candidate(symbol="AAPL", price=100.0,
                  sentiment_notes=("a headline",),
                  sources=(SourceRef("news", "x", NOW - STALE_AFTER_SECONDS - 1),))
    assert "STALE" in c.evidence_block(now=NOW)


def test_no_sources_recorded_adds_no_section():
    """Backwards compatible: every existing caller passes no sources and must
    render exactly as before."""
    assert "PROVENANCE" not in Candidate(symbol="AAPL", price=100.0).evidence_block()


# ---------------------------------------------------------------- persistence

def test_a_stored_deliberation_can_show_what_the_seats_saw():
    """The audit hole this closes. Without it a resolved thesis records the
    verdict and destroys the evidence."""
    from roundtable.types import Thesis
    c = Candidate(symbol="AAPL", price=100.0,
                  sentiment_notes=("a headline",),
                  sources=(SourceRef("news", "Massive news API", NOW - 60),))
    t = Thesis(thesis_id="t1", symbol="AAPL", asset_class="equity", candidate=c)

    payload = t.as_payload()
    assert "a headline" in payload["evidence"]
    assert payload["sources"][0]["source"] == "Massive news API"
    assert payload["sources"][0]["as_of"] == NOW - 60


def test_a_thesis_without_a_candidate_still_serialises():
    """Resumed and abandoned theses are rebuilt from a row, not a candidate."""
    from roundtable.types import Thesis
    payload = Thesis(thesis_id="t", symbol="A", asset_class="equity").as_payload()
    assert payload["evidence"] is None and payload["sources"] == []


def test_the_dashboard_serves_the_evidence_with_the_transcript(tmp_path):
    """Reading a past decision must not mean reading only its conclusion."""
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore
    store = MemoryStore(db_path=str(tmp_path / "d.db"))
    store.save_deliberation("t1", "AAPL", "equity", "complete", {
        "opinions": [], "consensus": {}, "tally": {},
        "evidence": "INSTRUMENT: AAPL\nPROVENANCE:\n  - news: x (2m ago)",
        "sources": [{"kind": "news", "source": "x", "as_of": NOW, "derived": False}]})

    view = DashboardRuntime(memory=store).deliberation("t1")
    assert "PROVENANCE" in view["evidence"]
    assert view["sources"][0]["kind"] == "news"


def test_an_old_deliberation_without_evidence_still_renders(tmp_path):
    """Every row written before this existed has no evidence key. The view
    must degrade, not 500 — those rows are the fund's history."""
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore
    store = MemoryStore(db_path=str(tmp_path / "d.db"))
    store.save_deliberation("t1", "AAPL", "equity", "complete",
                            {"opinions": [], "consensus": {}, "tally": {}})
    view = DashboardRuntime(memory=store).deliberation("t1")
    assert view["evidence"] is None and view["sources"] == []
