import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checker import check  # noqa: E402
from data import make_broadcasts  # noqa: E402
from review import FLAG_GAP, build_table, draft_from_table  # noqa: E402
from run_demo import corrupt, naive_cause  # noqa: E402

DATA = make_broadcasts()


def _reviews(data):
    for b in data:
        h = [x for x in data if x.day < b.day]
        t = build_table(b, h)
        yield b, h, t, draft_from_table(b, t)


def test_original_drafts_pass_checker():
    assert all(not check(d, t) for _, _, t, d in _reviews(DATA))


def test_wrong_number_is_always_rejected():
    rng = random.Random(3)
    assert all(check(corrupt(d, rng), t) for _, _, t, d in _reviews(DATA))


def test_causal_wording_is_rejected():
    b, _, t, d = next(r for r in _reviews(DATA) if r[3]["verdict"] == "조건")
    d["sentences"][1]["text"] = d["sentences"][1]["text"].replace("설명한다", "때문이다")
    assert any("인과" in e for e in check(d, t))


def test_thin_comparison_is_held():
    first = DATA[0]
    t = build_table(first, [])
    assert draft_from_table(first, t)["verdict"] == "보류"


def test_beats_naive_baseline_over_seeds():
    ours = naive = n = 0
    for seed in (1, 2, 3, 4, 5, 7):
        for b, h, t, d in _reviews(make_broadcasts(seed=seed)):
            if abs(t["gap"]) < FLAG_GAP or d["verdict"] == "보류":
                continue
            n += 1
            ours += d["factor"] == b.true_cause
            naive += naive_cause(b, h) == b.true_cause
    assert ours / n > 0.5 and ours > naive * 1.4
