"""The learner model and the teaching policy. Mastery of each concept is a probability updated after every answer
(Bayesian Knowledge Tracing); each misconception is a second probability updated from whether a wrong answer is exactly
what that misconception would produce; forgetting is an exponential decay whose half-life doubles with each spaced,
successful review. The policy picks the next activity from those estimates."""
import random

from . import curriculum as cu

VERSION = "tu-1.0"
P_INIT, P_LEARN, P_SLIP, P_GUESS = 0.2, 0.2, 0.1, 0.05      # assumed, not fitted: start, learn per practice, careless error, lucky guess
BUG_IF_HELD, BUG_IF_NOT = 0.85, 0.02                        # chance the answer is exactly the misconception's answer
READY, MASTERED, RECALL_DUE, SUSPECT = 0.6, 0.95, 0.85, 0.6
HALF_LIFE0 = 6.0                                            # days; doubles with each successful review at least a day apart
IMPACT = 0.7                                                # how far a held misconception pulls down concepts that depend on that skill
STRATEGIES = ["worked_example", "practice", "hint_first"]
DEPENDENTS = {k: sum(k in cu.PREREQS[o] for o in cu.KEYS) for k in cu.KEYS}


def new_state():
    return {"p": dict.fromkeys(cu.KEYS, P_INIT), "seen": dict.fromkeys(cu.KEYS, 0), "reviews": dict.fromkeys(cu.KEYS, 0), "last": dict.fromkeys(cu.KEYS), "m": dict.fromkeys(cu.MISCONCEPTIONS, 0.05)}


def effective(state, key):
    """Mastery as it should be trusted: the concept's own estimate, pulled down by any suspected misconception it depends on."""
    out = state["p"][key]
    for m in cu.EXPOSES[key]:
        out *= 1 - IMPACT * state["m"][m]
    return out


def half_life(state, key):
    return HALF_LIFE0 * 2 ** state["reviews"][key]


def recall(state, key, day):
    """Chance the learner can still do it today."""
    if state["last"][key] is None:
        return effective(state, key)
    return effective(state, key) * 0.5 ** (max(0, day - state["last"][key]) / half_life(state, key))


def predict(state, key, day):
    """Chance the next answer on this concept is right."""
    r = recall(state, key, day)
    return r * (1 - P_SLIP) + (1 - r) * P_GUESS


def ready(state, key):
    return all(effective(state, p) >= READY for p in cu.PREREQS[key])


def update(state, item, response, day):
    """Fold one answer into the learner state. -> (correct, diagnosis: the misconception whose answer this was, or None)"""
    key, correct = item["concept"], response == item["answer"]
    p = state["p"][key]
    q = p * (1 - P_SLIP) + (1 - p) * P_GUESS                 # chance of a right answer from someone without the misconception
    diagnosis = None
    for m, wrong in item["bugs"].items():
        held, free = (BUG_IF_HELD, BUG_IF_NOT) if response == wrong else (0.10, q) if correct else (0.05, max(0.01, 0.98 - q))
        prior = state["m"][m]
        state["m"][m] = min(0.99, max(0.01, prior * held / (prior * held + (1 - prior) * free)))       # never certain: a belief at exactly 1 could not be revised
        if response == wrong and (diagnosis is None or state["m"][m] > state["m"][diagnosis]):
            diagnosis = m
    post = p * (1 - P_SLIP) / (p * (1 - P_SLIP) + (1 - p) * P_GUESS) if correct else p * P_SLIP / (p * P_SLIP + (1 - p) * (1 - P_GUESS))
    state["p"][key] = min(0.999, post + (1 - post) * P_LEARN * (1.0 if ready(state, key) else 0.3))
    if correct and state["last"][key] is not None and day - state["last"][key] >= 1:
        state["reviews"][key] += 1                           # a successful recall after a gap strengthens the memory
    elif not correct:
        state["reviews"][key] = max(0, state["reviews"][key] - 1)
    state["last"][key], state["seen"][key] = day, state["seen"][key] + 1
    return correct, diagnosis


def choose_strategy(bandit, context, rng):
    """Thompson sampling over how to present the activity; bandit: {(strategy, context): [successes, tries]}."""
    return max(STRATEGIES, key=lambda s: rng.betavariate(1 + bandit.get((s, context), [0, 0])[0], 1 + bandit.get((s, context), [0, 0])[1] - bandit.get((s, context), [0, 0])[0]))


def next_activity(state, day, rng=None, bandit=None):
    """-> {type, concept, misconception, strategy, reason}. Order of priority: fix a suspected misconception; review
    what is about to be forgotten; otherwise teach the ready concept with the most to gain."""
    rng = rng or random.Random(0)
    suspects = [m for m in cu.MISCONCEPTIONS if state["m"][m] >= SUSPECT]
    if suspects:
        m = max(suspects, key=state["m"].get)
        key = next(k for k in cu.KEYS if m in cu.EXPOSES[k])
        return {"type": "remediate", "concept": key, "misconception": m, "strategy": "worked_example",
                "reason": f"{state['m'][m]:.0%} likely to hold the misconception \"{cu.MISCONCEPTIONS[m][0]}\"; {sum(m in cu.EXPOSES[k] for k in cu.KEYS)} concepts depend on fixing it"}
    due = [k for k in cu.KEYS if state["seen"][k] and state["p"][k] >= 0.6 and state["last"][k] != day and recall(state, k, day) < RECALL_DUE]
    if due:
        key = min(due, key=lambda k: recall(state, k, day))
        return {"type": "review", "concept": key, "misconception": None, "strategy": "practice",
                "reason": f"last practised {day - state['last'][key]} days ago; estimated recall has fallen to {recall(state, key, day):.0%}"}
    todo = [k for k in cu.KEYS if state["p"][k] < MASTERED and ready(state, k)]
    if not todo:
        blocked = [k for k in cu.KEYS if state["p"][k] < MASTERED]
        if not blocked:
            key = min(cu.KEYS, key=lambda k: recall(state, k, day))
            return {"type": "review", "concept": key, "misconception": None, "strategy": "practice", "reason": "every concept is mastered; this is the one most at risk of being forgotten"}
        todo = [min(blocked, key=cu.KEYS.index)]
    key = max(todo, key=lambda k: ((1 - state["p"][k]) * (1 + 0.15 * DEPENDENTS[k]), -cu.KEYS.index(k)))
    context = "new" if state["seen"][key] < 3 else "practised"         # how often the learner has met this concept, which the tutor can see
    return {"type": "learn", "concept": key, "misconception": None, "strategy": choose_strategy(bandit or {}, context, rng), "context": context,
            "reason": f"prerequisites are in place and mastery is {state['p'][key]:.0%}" + (f"; {DEPENDENTS[key]} later concepts need it" if DEPENDENTS[key] else "")}
