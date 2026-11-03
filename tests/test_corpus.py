import datetime as dt

from lostfound.config import SIM_NOW
from lostfound.corpus import generate_corpus


def _corpus(seed=1, n=60):
    return generate_corpus(n_items=n, seed=seed, render_images=False)


def test_generation_is_deterministic():
    a, b = _corpus(), _corpus()
    assert [r.id for r in a.reports] == [r.id for r in b.reports]
    assert [r.description for r in a.reports] == [r.description for r in b.reports]
    assert a.truth == b.truth


def test_corpus_invariants():
    c = _corpus(n=80)
    assert c.truth
    ids = {r.id for r in c.reports}
    for lost_id, found_id in c.truth.items():
        assert lost_id in ids and found_id in ids
        assert lost_id.startswith("LR") and found_id.startswith("FR")
    for r in c.reports:
        assert r.description
        assert r.node_key
        assert dt.datetime.fromisoformat(r.created_at) <= SIM_NOW + dt.timedelta(days=1)


def test_lost_reports_have_an_interval_and_precede_the_find():
    c = _corpus(n=80)
    lost = {r.id: r for r in c.reports if r.kind == "lost"}
    found = {r.id: r for r in c.reports if r.kind == "found"}
    for lost_id, found_id in c.truth.items():
        lr, fr = lost[lost_id], found[found_id]
        assert lr.lost_from and lr.lost_to
        assert dt.datetime.fromisoformat(lr.lost_from) <= dt.datetime.fromisoformat(fr.created_at)


def test_both_directions_of_the_problem_exist():
    c = _corpus(n=120)
    claimed = set(c.truth)
    found_ids = set(c.truth.values())
    unclaimed = [r for r in c.reports if r.kind == "found" and r.id not in found_ids]
    never_found = [r for r in c.reports if r.kind == "lost" and r.id not in claimed]
    assert unclaimed, "the corpus needs found items nobody claims"
    assert never_found, "the corpus needs lost items that were never found"
