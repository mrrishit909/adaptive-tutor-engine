"""Simulated students and the adaptive-versus-fixed experiment. A simulated student has a hidden set of mastered
concepts and held misconceptions and answers items accordingly. The tutor never sees that hidden state: it only sees
answers. The parameters here are the simulator's truth and differ from the tutor's assumed ones on purpose."""
import random

from . import curriculum as cu
from . import engine

TRUE = {"learn": 0.24, "learn_unready": 0.02, "slip": 0.07, "guess": 0.03, "bug": 0.85, "half_life": 5.0, "fix_targeted": 0.55, "fix_by_feedback": 0.04}
# how much each way of presenting an activity helps, for a concept that is new to the student or already practised
BOOST = {("worked_example", "new"): 1.6, ("hint_first", "new"): 1.25, ("practice", "new"): 1.0, ("worked_example", "practised"): 0.8, ("hint_first", "practised"): 1.0, ("practice", "practised"): 1.3}
FIXED_PER_CONCEPT = 5


def student(rng, holds=None, mastered=None):
    st = {"mastered": set(), "holds": set(holds) if holds is not None else {m for m in cu.MISCONCEPTIONS if rng.random() < 0.3}, "reviews": dict.fromkeys(cu.KEYS, 0), "last": dict.fromkeys(cu.KEYS, 0), "tries": dict.fromkeys(cu.KEYS, 0)}
    for k in cu.KEYS:
        ok = all(p in st["mastered"] for p in cu.PREREQS[k]) and not set(cu.EXPOSES[k]) & st["holds"]
        if (mastered is not None and k in mastered) or (mastered is None and ok and rng.random() < (0.6 if not cu.PREREQS[k] else 0.12)):
            st["mastered"].add(k)
    return st


def respond(st, item, rng):
    for m, wrong in item["bugs"].items():
        if m in st["holds"] and rng.random() < TRUE["bug"]:
            return wrong
    if rng.random() < (1 - TRUE["slip"] if item["concept"] in st["mastered"] else TRUE["guess"]):
        return item["answer"]
    while True:
        guess = item["answer"] + rng.choice([-10, -3, -2, -1, 1, 2, 3, 10])
        if guess not in item["bugs"].values():
            return guess


def practise(st, activity, item, day, rng):
    """What one activity (with feedback) does to the hidden student."""
    key = item["concept"]
    for m in sorted(st["holds"] & set(item["bugs"])):                  # sorted: set order varies between Python processes, and the run must be reproducible
        if rng.random() < (TRUE["fix_targeted"] if activity["type"] == "remediate" and activity["misconception"] == m else TRUE["fix_by_feedback"]):
            st["holds"].discard(m)
    if key in st["mastered"]:
        if day - st["last"][key] >= 1:
            st["reviews"][key] += 1
    else:
        ok = all(p in st["mastered"] for p in cu.PREREQS[key]) and not set(cu.EXPOSES[key]) & st["holds"]
        boost = BOOST[activity["strategy"], "new" if st["tries"][key] < 3 else "practised"]
        if rng.random() < (TRUE["learn"] * boost if ok else TRUE["learn_unready"]):
            st["mastered"].add(key)
    st["last"][key], st["tries"][key] = day, st["tries"][key] + 1


def forget(st, day, rng):
    for k in sorted(st["mastered"]):
        if day - st["last"][k] >= 1 and rng.random() < 1 - 0.5 ** (1 / (TRUE["half_life"] * 2 ** st["reviews"][k])):
            st["mastered"].discard(k)
            st["reviews"][k] = max(0, st["reviews"][k] - 1)


def score(st, rng):
    right = 0
    for k in cu.KEYS:
        item = cu.generate(k, rng)
        right += respond(st, item, rng) == item["answer"]
    return right


def run(policy, n=300, days=30, per_day=4, seed=0, bandit=None, log=None):
    """Teach n simulated students for `days` days. policy: 'adaptive' or 'fixed'.
    -> per-student dicts: mastered before, after, two weeks later with no practice; exam scores; misconceptions left"""
    out, bandit = [], {} if bandit is None else bandit
    for s in range(n):
        rng = random.Random(f"{seed}-{s}")                        # the same students face both policies
        st, state, step = student(rng), engine.new_state(), 0
        act_rng = random.Random(f"{seed}-{s}-{policy}")
        before, pre = len(st["mastered"]), 0
        for k in cu.KEYS:                                          # a pre-test, one item per concept; the adaptive tutor learns from it
            item = cu.generate(k, act_rng)
            resp = respond(st, item, act_rng)
            pre += resp == item["answer"]
            engine.update(state, item, resp, 0)
        pending, new_steps = {}, 0
        for day in range(1, days + 1):
            forget(st, day, act_rng)
            for _ in range(per_day):
                if policy == "adaptive":
                    act = engine.next_activity(state, day, act_rng, bandit)
                else:                                              # the control: every concept in order, with a mixed review every fourth activity
                    covered = cu.KEYS[:min(len(cu.KEYS), new_steps // FIXED_PER_CONCEPT + 1)]
                    if step % 4 == 3 and len(covered) > 1:
                        act = {"type": "review", "concept": act_rng.choice(covered[:-1]), "misconception": None, "strategy": "practice"}
                    else:
                        act = {"type": "learn", "concept": cu.KEYS[(new_steps // FIXED_PER_CONCEPT) % len(cu.KEYS)], "misconception": None, "strategy": "practice"}
                        new_steps += 1
                item = cu.generate(act["concept"], act_rng, target=act["misconception"])
                resp = respond(st, item, act_rng)
                guess = engine.predict(state, act["concept"], day)
                correct, _ = engine.update(state, item, resp, day)
                if act["concept"] in pending:                      # a strategy is judged by the next answer on that concept, not the one it was shown with
                    b = bandit.setdefault(pending.pop(act["concept"]), [0, 0])
                    b[0], b[1] = b[0] + correct, b[1] + 1
                if policy == "adaptive" and act["type"] == "learn":
                    pending[act["concept"]] = (act["strategy"], act["context"])
                if log is not None:
                    log.append((s, guess, correct))
                practise(st, act, item, day, act_rng)
                step += 1
        after, post, held = len(st["mastered"]), score(st, act_rng), len(st["holds"])
        for day in range(days + 1, days + 15):
            forget(st, day, act_rng)
        believed = sum(engine.effective(state, k) >= engine.MASTERED for k in cu.KEYS)
        flagged = {m for m in cu.MISCONCEPTIONS if state["m"][m] >= engine.SUSPECT}
        out.append({"believed": believed, "before": before, "after": after, "later": len(st["mastered"]), "pre_score": pre, "post_score": post, "later_score": score(st, act_rng), "misconceptions_left": held})
    return out
