"""Unit and property tests for the curriculum, the learner model and the policy. No database."""
import random

import pytest

from tu import curriculum as cu
from tu import engine, world

ITEM = {"concept": "int_sub", "stem": "5 - (-3) = ?", "answer": 8, "bugs": {"sub_negative": 2}}


@pytest.mark.parametrize("key", cu.KEYS)
def test_every_concept_generates_checked_items_whose_buggy_answers_are_wrong(key):
    rng = random.Random(key)
    for _ in range(300):
        item = cu.generate(key, rng)
        assert cu.check(item) is not False and all(v != item["answer"] for v in item["bugs"].values()) and set(item["bugs"]) <= set(cu.EXPOSES[key])
    for m in cu.EXPOSES[key]:
        assert m in cu.generate(key, rng, target=m)["bugs"]


def test_the_course_graph_is_acyclic_and_complete():
    seen = set()
    for key, _, pre, exposes in cu.CONCEPTS:
        assert set(pre) <= seen and set(exposes) <= set(cu.MISCONCEPTIONS) and key in cu.HINT          # prerequisites always come earlier
        seen.add(key)
    assert all(any(m in c[3] for c in cu.CONCEPTS) for m in cu.MISCONCEPTIONS) and cu.check({"stem": "2 + 3 x 4 = ?", "answer": 20}) is False


def test_answers_move_mastery_and_a_buggy_answer_points_at_the_misconception():
    s = engine.new_state()
    assert engine.update(s, ITEM, 2, 0) == (False, "sub_negative") and s["m"]["sub_negative"] > 0.6 and s["p"]["int_sub"] < engine.P_INIT + 0.05
    s2 = engine.new_state()
    assert engine.update(s2, ITEM, 7, 0) == (False, None) and s2["m"]["sub_negative"] < 0.05           # a wrong answer that is not the misconception's
    s3 = engine.new_state()
    assert engine.update(s3, ITEM, 8, 0) == (True, None) and s3["p"]["int_sub"] > 0.8 and s3["m"]["sub_negative"] < 0.05


def test_a_belief_is_never_so_certain_that_it_cannot_be_revised():
    s = engine.new_state()
    for _ in range(30):
        engine.update(s, ITEM, 2, 0)
    assert s["m"]["sub_negative"] == 0.99
    for _ in range(6):
        engine.update(s, ITEM, 8, 1)
    assert s["m"]["sub_negative"] < engine.SUSPECT                       # the learner got over it, and the tutor notices


def test_a_misconception_lowers_trust_only_in_concepts_that_depend_on_it():
    s = engine.new_state()
    s["p"] = dict.fromkeys(cu.KEYS, 0.9)
    s["m"]["sub_negative"] = 0.99
    hit = {k for k in cu.KEYS if engine.effective(s, k) < 0.5}
    assert hit == {"int_sub", "like_terms", "eq_add", "eq_two_step", "eq_both_sides", "slope"} and engine.effective(s, "coordinates") > 0.89
    assert not engine.ready(s, "eq_add") and engine.ready(s, "ratio")


def test_memory_fades_and_spaced_success_doubles_the_half_life():
    s = engine.new_state()
    engine.update(s, ITEM, 8, 0)
    engine.update(s, ITEM, 8, 0)                                         # the same day: no credit for spacing
    assert s["reviews"]["int_sub"] == 0 and engine.half_life(s, "int_sub") == engine.HALF_LIFE0
    assert engine.recall(s, "int_sub", 6) == pytest.approx(engine.effective(s, "int_sub") / 2) and engine.recall(s, "int_sub", 0) > engine.recall(s, "int_sub", 3)
    engine.update(s, ITEM, 8, 2)
    assert s["reviews"]["int_sub"] == 1 and engine.half_life(s, "int_sub") == 2 * engine.HALF_LIFE0
    engine.update(s, ITEM, 7, 5)
    assert s["reviews"]["int_sub"] == 0


def test_policy_fixes_misconceptions_first_then_reviews_then_teaches_what_is_ready():
    s = engine.new_state()
    s["m"]["left_to_right"] = 0.9
    act = engine.next_activity(s, 0)
    assert (act["type"], act["misconception"], act["concept"]) == ("remediate", "left_to_right", "order_ops")
    s["m"]["left_to_right"] = 0.05
    s["p"]["int_add"], s["seen"]["int_add"], s["last"]["int_add"] = 0.99, 3, 0
    assert engine.next_activity(s, 0)["type"] == "learn"                # practised today: nothing is due
    late = engine.next_activity(s, 9)
    assert (late["type"], late["concept"]) == ("review", "int_add") and "9 days ago" in late["reason"]
    s["last"]["int_add"] = 9
    act = engine.next_activity(s, 9)
    assert act["type"] == "learn" and all(engine.effective(s, p) >= engine.READY for p in cu.PREREQS[act["concept"]]) and act["concept"] in ("int_sub", "int_mul", "coordinates", "perimeter")


def test_the_bandit_follows_the_evidence():
    rng = random.Random(0)
    stats = {("worked_example", "new"): [80, 100], ("practice", "new"): [20, 100], ("hint_first", "new"): [30, 100]}
    picks = [engine.choose_strategy(stats, "new", rng) for _ in range(200)]
    assert picks.count("worked_example") > 190 and len({engine.choose_strategy({}, "new", rng) for _ in range(60)}) == 3      # with no evidence it tries everything


def test_simulated_students_are_reproducible_and_the_tutor_beats_the_fixed_sequence():
    a, b = world.run("adaptive", n=40, seed=3), world.run("adaptive", n=40, seed=3)
    fixed = world.run("fixed", n=40, seed=3)
    assert a == b and [r["before"] for r in a] == [r["before"] for r in fixed]                          # the same students in both arms
    assert sum(r["after"] for r in a) > sum(r["after"] for r in fixed) + 40 and sum(r["misconceptions_left"] for r in a) < sum(r["misconceptions_left"] for r in fixed)


def test_a_student_with_the_sign_error_gives_exactly_that_wrong_answer():
    st = world.student(random.Random(0), holds={"sub_negative"}, mastered={"int_add"})
    rng = random.Random(1)
    answers = [world.respond(st, ITEM, rng) for _ in range(200)]
    assert 150 < answers.count(2) < 190 and "int_sub" not in st["mastered"]
