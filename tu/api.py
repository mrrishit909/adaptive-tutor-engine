"""Public API (modular monolith). Blueprint services map to: curriculum-service (knowledge graph), content-service (item
generation), assessment-service (attempts), mastery-service (learner model), recommendation-service (next activity),
tutor-service (feedback from the content bank), analytics-service (the adaptive-versus-fixed experiment) and
safety-service (the tutor can only say what the course content says; answers must be whole numbers).

    uvicorn tu.api:app          python -m core.jobs tu.api      # the worker
"""
import math
import random
import statistics
import uuid

from fastapi import Depends, Header
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from core import audit, db, jobs
from core.app import Ctx, Problem, create_app, run

from . import curriculum as cu
from . import engine, world

LEARN = {"learn:use", "jobs:read"}
TEACH = LEARN | {"class:read", "content:write", "simulate:run"}
PERMISSIONS = {"student": LEARN, "teacher": TEACH, "admin": TEACH | {"scenario:control", "audit:read"}}
app = create_app("adaptive-tutor-engine", PERMISSIONS)
auth = app.state.auth
IdemKey = Header(None, alias="Idempotency-Key")
COURSE = uuid.UUID("5a1d3c0a-0000-4000-8000-0000000a19eb")


def concepts(c):
    rows = c.execute("SELECT id, key, name FROM concepts ORDER BY ordinal").fetchall()
    if not rows:
        raise Problem(409, "no_course", "load the course first: POST /v1/scenarios/classroom")
    return {r["key"]: r["id"] for r in rows}


def load(c, learner_id, lock=False):
    """The learner's state as the engine uses it."""
    if not c.execute("SELECT 1 FROM learners WHERE id = %s" + (" FOR UPDATE" if lock else ""), [learner_id]).fetchone():
        raise Problem(404, "learner_not_found")
    state = engine.new_state()
    for r in c.execute("SELECT k.key, m.probability, m.seen, m.reviews, m.last_day FROM mastery m JOIN concepts k ON k.id = m.concept_id WHERE m.learner_id = %s", [learner_id]):
        state["p"][r["key"]], state["seen"][r["key"]], state["reviews"][r["key"]], state["last"][r["key"]] = r["probability"], r["seen"], r["reviews"], r["last_day"]
    for r in c.execute("SELECT key, probability FROM misconceptions WHERE learner_id = %s", [learner_id]):
        state["m"][r["key"]] = r["probability"]
    return state


def save(c, t, learner_id, state, ids, keys=None):
    for k in keys or cu.KEYS:
        p = state["p"][k]
        c.execute("""INSERT INTO mastery (learner_id, concept_id, probability, uncertainty, tenant_id, seen, reviews, last_day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                     ON CONFLICT (learner_id, concept_id) DO UPDATE SET probability = excluded.probability, uncertainty = excluded.uncertainty, seen = excluded.seen, reviews = excluded.reviews, last_day = excluded.last_day, updated_at = now()""",
                  [learner_id, ids[k], p, math.sqrt(p * (1 - p)), t, state["seen"][k], state["reviews"][k], state["last"][k]])
    for m, p in state["m"].items():
        c.execute("INSERT INTO misconceptions (learner_id, key, probability, tenant_id) VALUES (%s,%s,%s,%s) ON CONFLICT (learner_id, key) DO UPDATE SET probability = excluded.probability, updated_at = now()", [learner_id, m, p, t])


def bandit(c):
    return {(r["strategy"], r["context"]): [r["wins"], r["n"]] for r in c.execute("""SELECT strategy, context, count(*) FILTER (WHERE (outcome->>'correct')::boolean) AS wins, count(*) AS n FROM interventions
                                                                                    WHERE outcome IS NOT NULL AND kind = 'learn' GROUP BY 1, 2""")}


def store_item(c, t, ids, item):
    iid = uuid.uuid7()
    c.execute("INSERT INTO items (id, tenant_id, concept_id, stem, answer, bugs, checked) VALUES (%s,%s,%s,%s,%s,%s,%s)", [iid, t, ids[item["concept"]], item["stem"], item["answer"], Jsonb(item["bugs"]), cu.check(item)])
    return iid


def record(c, ctx, ids, learner_id, state, item, item_id, response, day, ms=None, activity=None):
    """One answer: update the learner model, close the open recommendation for that concept, store the attempt."""
    key = item["concept"]
    before, eff_before = state["p"][key], engine.effective(state, key)
    correct, diagnosis = engine.update(state, item, response, day)
    c.execute("UPDATE interventions SET outcome = %s WHERE learner_id = %s AND concept_id = %s AND outcome IS NULL", [Jsonb({"correct": correct, "day": day}), learner_id, ids[key]])
    aid = uuid.uuid7()
    c.execute("INSERT INTO attempts (id, tenant_id, learner_id, concept_id, item_id, score, response_time_ms, response, diagnosis, day, activity) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              [aid, ctx.tenant_id, learner_id, ids[key], item_id, float(correct), ms, response, diagnosis, day, activity])
    return {"attempt_id": aid, "concept": key, "correct": correct, "diagnosis": diagnosis, "diagnosis_text": cu.MISCONCEPTIONS[diagnosis][0] if diagnosis else None, "mastery_before": round(before, 3), "mastery_after": round(state["p"][key], 3),
            "trusted_mastery_before": round(eff_before, 3), "trusted_mastery_after": round(engine.effective(state, key), 3), "misconceptions": {m: round(p, 3) for m, p in state["m"].items() if p >= 0.1}}


def recommend(c, ctx, ids, learner_id, state, day, rng):
    act = engine.next_activity(state, day, rng, bandit(c))
    item = cu.generate(act["concept"], rng, target=act["misconception"])
    item_id = store_item(c, ctx.tenant_id, ids, item)
    c.execute("INSERT INTO interventions (id, tenant_id, learner_id, concept_id, strategy, kind, context) VALUES (%s,%s,%s,%s,%s,%s,%s)", [uuid.uuid7(), ctx.tenant_id, learner_id, ids[act["concept"]], act["strategy"], act["type"], act.get("context")])
    return act, item, item_id


def view(state, day):
    """Per-concept picture of a learner: raw mastery, what can be trusted, what is due."""
    rows = []
    for k in cu.KEYS:
        p, eff = state["p"][k], engine.effective(state, k)
        hl = engine.half_life(state, k)
        due = None if state["last"][k] is None or eff <= engine.RECALL_DUE else state["last"][k] + math.ceil(hl * math.log2(eff / engine.RECALL_DUE))
        rows.append({"concept": k, "name": cu.NAME[k], "probability": round(p, 3), "uncertainty": round(math.sqrt(p * (1 - p)), 3), "trusted": round(eff, 3), "recall_today": round(engine.recall(state, k, day), 3), "attempts": state["seen"][k],
                     "half_life_days": hl, "review_due_day": due, "held_back_by": [m for m in cu.EXPOSES[k] if state["m"][m] >= 0.3], "ready": engine.ready(state, k),
                     "status": "mastered" if eff >= engine.MASTERED else "held back" if any(state["m"][m] >= 0.3 for m in cu.EXPOSES[k]) else "learning" if state["seen"][k] else "not started"})
    return rows


# ---------------------------------------------------------------- scenario + curriculum-service

@app.post("/v1/scenarios/classroom", status_code=201, tags=["scenario"], summary="(+) Load the pre-algebra course and one simulated learner who subtracts negatives wrongly. Resets this tenant's course data")
def classroom(ctx: Ctx = Depends(auth("scenario:control")), idem: str | None = IdemKey):
    def work(c):
        for table in ("sim_students", "interventions", "misconceptions", "mastery", "attempts", "items", "learners", "concept_edges", "concepts"):
            c.execute(f"DELETE FROM {table}")                         # row-level security limits this to the tenant
        ids = {k: uuid.uuid5(ctx.tenant_id, f"concept-{k}") for k in cu.KEYS}
        for i, (k, name, _, exposes) in enumerate(cu.CONCEPTS):
            c.execute("INSERT INTO concepts (id, tenant_id, course_id, name, description, difficulty, key, ordinal, exposes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", [ids[k], ctx.tenant_id, COURSE, name, cu.HINT[k], round(i / len(cu.KEYS), 2), k, i, Jsonb(exposes)])
        for k, pre in cu.PREREQS.items():
            for p in pre:
                c.execute("INSERT INTO concept_edges VALUES (%s,%s,1.0,%s)", [ids[p], ids[k], ctx.tenant_id])
        lid = uuid.uuid5(ctx.tenant_id, "learner-S-001")
        c.execute("INSERT INTO learners (id, tenant_id, external_id, settings) VALUES (%s,%s,'S-001',%s)", [lid, ctx.tenant_id, Jsonb({"simulated": True})])
        hidden = world.student(random.Random(1), holds={"sub_negative"}, mastered={"int_add", "int_mul", "order_ops", "eq_mul", "coordinates", "perimeter", "area", "percent"})
        c.execute("INSERT INTO sim_students (learner_id, tenant_id, hidden) VALUES (%s,%s,%s)", [lid, ctx.tenant_id, Jsonb({**hidden, "mastered": sorted(hidden["mastered"]), "holds": sorted(hidden["holds"])})])
        save(c, ctx.tenant_id, lid, engine.new_state(), ids)
        audit.record(c, ctx, "scenario.loaded", "course", COURSE, {"concepts": len(ids)})
        return 201, {"course_id": COURSE, "concepts": len(ids), "prerequisite_links": sum(len(p) for p in cu.PREREQS.values()), "misconceptions_modelled": list(cu.MISCONCEPTIONS), "learner_id": lid, "learner": "S-001",
                     "note": "S-001 is simulated. Its hidden state (what it really knows) is never read by the tutor, only by the simulator that answers for it."}
    return run(ctx, idem, {}, work)


@app.get("/v1/courses/{course_id}/knowledge-graph", tags=["curriculum"], summary="Concepts and prerequisite links, optionally coloured with one learner's mastery")
def graph(course_id: uuid.UUID, learner_id: uuid.UUID | None = None, day: int = 0, ctx: Ctx = Depends(auth("learn:use"))):
    with db.tx(ctx.tenant_id) as c:
        nodes = c.execute("SELECT id, key, name, description, exposes FROM concepts WHERE course_id = %s ORDER BY ordinal", [course_id]).fetchall()
        if not nodes:
            raise Problem(404, "course_not_found")
        edges = c.execute("SELECT p.key AS source, k.key AS target FROM concept_edges e JOIN concepts p ON p.id = e.prerequisite_id JOIN concepts k ON k.id = e.concept_id WHERE k.course_id = %s", [course_id]).fetchall()
        overlay = {r["concept"]: r for r in view(load(c, learner_id), day)} if learner_id else {}
    depth = {}
    for k in cu.KEYS:
        depth[k] = 1 + max((depth[p] for p in cu.PREREQS[k]), default=-1)
    return jsonable_encoder({"course_id": course_id, "nodes": [{**n, "depth": depth[n["key"]], **({k: overlay[n["key"]][k] for k in ("probability", "trusted", "status", "held_back_by")} if overlay else {})} for n in nodes], "edges": edges})


# ---------------------------------------------------------------- content-service

class GenerateIn(BaseModel):
    concept: str = Field(description="Concept key, e.g. int_sub")
    count: int = Field(3, ge=1, le=20)
    seed: int = Field(0, ge=0, le=10**9)
    target_misconception: str | None = None


@app.post("/v1/content/generate", status_code=201, tags=["content"], summary="Generate practice items for a concept from its template. Answers are computed, and re-computed independently where the item can be parsed back")
def generate(body: GenerateIn, ctx: Ctx = Depends(auth("content:write")), idem: str | None = IdemKey):
    def work(c):
        ids = concepts(c)
        if body.concept not in ids or (body.target_misconception and body.target_misconception not in cu.EXPOSES[body.concept]):
            raise Problem(422, "unknown_concept_or_misconception", f"concepts: {cu.KEYS}; this concept can expose: {cu.EXPOSES.get(body.concept, [])}")
        rng, out = random.Random(f"{body.concept}-{body.seed}"), []
        for _ in range(body.count):
            item = cu.generate(body.concept, rng, target=body.target_misconception)
            if cu.check(item) is False:                            # the two computations disagree: never served
                raise Problem(500, "item_failed_validation", item["stem"])
            out.append({"item_id": store_item(c, ctx.tenant_id, ids, item), **item, "independently_checked": cu.check(item)})
        return 201, {"concept": body.concept, "items": out, "method": "parametric templates; no language model"}
    return run(ctx, idem, body, work)


# ---------------------------------------------------------------- assessment-service + mastery-service

class AttemptIn(BaseModel):
    learner_id: uuid.UUID
    item_id: uuid.UUID
    response: int = Field(ge=-10**6, le=10**6, description="Answers are whole numbers; anything else is refused")
    response_time_ms: int | None = Field(None, ge=0, le=3600000)
    day: int = Field(0, ge=0, le=3650, description="Day of the course, for spacing. A real deployment would take this from the clock")


def get_item(c, item_id):
    r = c.execute("SELECT i.id, k.key AS concept, i.stem, i.answer, i.bugs FROM items i JOIN concepts k ON k.id = i.concept_id WHERE i.id = %s", [item_id]).fetchone()
    if not r:
        raise Problem(404, "item_not_found")
    return {**r, "bugs": {m: int(v) for m, v in r["bugs"].items()}}


@app.post("/v1/attempts", status_code=201, tags=["assessment"], summary="Record one answer. The learner model is updated and a wrong answer is checked against what each misconception would have produced")
def attempt(body: AttemptIn, ctx: Ctx = Depends(auth("learn:use")), idem: str | None = IdemKey):
    def work(c):
        ids, state, item = concepts(c), load(c, body.learner_id, lock=True), get_item(c, body.item_id)
        out = record(c, ctx, ids, body.learner_id, state, item, body.item_id, body.response, body.day, body.response_time_ms)
        save(c, ctx.tenant_id, body.learner_id, state, ids, [item["concept"]])
        return 201, out
    return run(ctx, idem, body, work)


@app.get("/v1/learners", tags=["mastery"], summary="(+) Learners in this school, by external id only")
def learners(ctx: Ctx = Depends(auth("class:read"))):
    with db.tx(ctx.tenant_id) as c:
        return jsonable_encoder({"items": c.execute("SELECT l.id, l.external_id, (SELECT count(*) FROM attempts a WHERE a.learner_id = l.id) AS attempts FROM learners l ORDER BY l.external_id").fetchall()})


@app.get("/v1/learners/{learner_id}/mastery", tags=["mastery"], summary="Every concept for one learner: mastery, how far it can be trusted, recall today, when a review is due, and suspected misconceptions")
def mastery(learner_id: uuid.UUID, day: int = 0, ctx: Ctx = Depends(auth("learn:use"))):
    with db.tx(ctx.tenant_id) as c:
        state = load(c, learner_id)
        n = c.execute("SELECT count(*) AS n, count(*) FILTER (WHERE score = 1) AS right FROM attempts WHERE learner_id = %s", [learner_id]).fetchone()
    rows = view(state, day)
    return {"learner_id": learner_id, "day": day, "attempts": n["n"], "correct": n["right"], "concepts": rows, "mastered": sum(r["status"] == "mastered" for r in rows), "held_back": [r["concept"] for r in rows if r["status"] == "held back"],
            "misconceptions": [{"key": m, "probability": round(p, 3), "what": cu.MISCONCEPTIONS[m][0], "affects": [k for k in cu.KEYS if m in cu.EXPOSES[k]]} for m, p in sorted(state["m"].items(), key=lambda kv: -kv[1])],
            "model": {"version": engine.VERSION, "assumed": {"learn": engine.P_LEARN, "slip": engine.P_SLIP, "guess": engine.P_GUESS, "half_life_days": engine.HALF_LIFE0}}}


# ---------------------------------------------------------------- recommendation-service + tutor-service

class NextIn(BaseModel):
    learner_id: uuid.UUID
    day: int = Field(0, ge=0, le=3650)


@app.post("/v1/recommendations/next", status_code=201, tags=["recommendations"], summary="The next activity for a learner, the item to show, and the reason it was chosen")
def next_activity(body: NextIn, ctx: Ctx = Depends(auth("learn:use")), idem: str | None = IdemKey):
    def work(c):
        ids, state = concepts(c), load(c, body.learner_id, lock=True)
        n = c.execute("SELECT count(*) AS n FROM interventions WHERE learner_id = %s", [body.learner_id]).fetchone()["n"]
        act, item, item_id = recommend(c, ctx, ids, body.learner_id, state, body.day, random.Random(f"{body.learner_id}-{n}"))
        return 201, {"activity": act["type"], "concept": act["concept"], "concept_name": cu.NAME[act["concept"]], "misconception": act["misconception"], "strategy": act["strategy"], "reason": act["reason"],
                     "item": {"item_id": item_id, "stem": item["stem"]}, "policy": "fix a suspected misconception first, then review what is fading, then teach the ready concept with the most to gain"}
    return run(ctx, idem, body, work)


class TutorIn(BaseModel):
    learner_id: uuid.UUID
    item_id: uuid.UUID
    response: int = Field(ge=-10**6, le=10**6)
    attempt_no: int = Field(1, ge=1, le=5)


@app.post("/v1/tutor/respond", tags=["tutor"], summary="Feedback on an answer, taken only from the course content. The answer itself is not given away on a first wrong try")
def tutor(body: TutorIn, ctx: Ctx = Depends(auth("learn:use"))):
    with db.tx(ctx.tenant_id) as c:
        load(c, body.learner_id)
        item = get_item(c, body.item_id)
    if body.response == item["answer"]:
        return {"correct": True, "message": f"Correct. {cu.HINT[item['concept']]}", "sources": [f"concept:{item['concept']}"], "answer_revealed": False}
    m = next((k for k, v in item["bugs"].items() if v == body.response), None)
    rule, sources = (cu.MISCONCEPTIONS[m][1], [f"misconception:{m}", f"concept:{item['concept']}"]) if m else (cu.HINT[item["concept"]], [f"concept:{item['concept']}"])
    message = ("Not yet. " + rule + (" For example: " + cu.MISCONCEPTIONS[m][2] if m else "") + " Try this one again.") if body.attempt_no == 1 else f"Not yet. {rule} Here the answer is {item['answer']}."
    return {"correct": False, "diagnosis": m, "message": message, "sources": sources, "answer_revealed": body.attempt_no > 1, "policy": "hint first; the answer is shown only from the second wrong try; every sentence comes from the content bank"}


# ---------------------------------------------------------------- simulated sessions + analytics-service

class SessionIn(BaseModel):
    activities: int = Field(8, ge=1, le=60)
    day: int = Field(0, ge=0, le=3650)
    diagnostic: bool = Field(False, description="One item per concept in course order instead of the tutor's own choices")


@app.post("/v1/simulations/learners/{learner_id}/session", tags=["simulation"], summary="(+) Let the simulated learner work through a session: the tutor chooses, the simulator answers, each answer is recorded like any other")
def session(learner_id: uuid.UUID, body: SessionIn, ctx: Ctx = Depends(auth("simulate:run")), idem: str | None = IdemKey):
    def work(c):
        ids, state = concepts(c), load(c, learner_id, lock=True)
        sim = c.execute("SELECT hidden, steps FROM sim_students WHERE learner_id = %s FOR UPDATE", [learner_id]).fetchone()
        if not sim:
            raise Problem(409, "not_a_simulated_learner", "only learners created by the scenario can be simulated")
        st = {**sim["hidden"], "mastered": set(sim["hidden"]["mastered"]), "holds": set(sim["hidden"]["holds"])}
        rng = random.Random(f"{learner_id}-{sim['steps']}")
        world.forget(st, body.day, rng)
        log = []
        for k in range(len(cu.KEYS) if body.diagnostic else body.activities):
            if body.diagnostic:
                act = {"type": "diagnostic", "concept": cu.KEYS[k], "misconception": None, "strategy": "practice", "reason": "one item per concept to place the learner"}
                item = cu.generate(cu.KEYS[k], rng, target=(cu.EXPOSES[cu.KEYS[k]] or [None])[0])
                item_id = store_item(c, ctx.tenant_id, ids, item)
            else:
                act, item, item_id = recommend(c, ctx, ids, learner_id, state, body.day, rng)
            resp = world.respond(st, item, rng)
            out = record(c, ctx, ids, learner_id, state, item, item_id, resp, body.day, activity=act["type"])
            world.practise(st, act, item, body.day, rng)
            log.append({"activity": act["type"], "concept": item["concept"], "strategy": act["strategy"], "stem": item["stem"], "response": resp, "answer": item["answer"], "correct": out["correct"], "diagnosis": out["diagnosis"], "reason": act["reason"]})
        save(c, ctx.tenant_id, learner_id, state, ids)
        c.execute("UPDATE sim_students SET hidden = %s, steps = steps + %s WHERE learner_id = %s", [Jsonb({**st, "mastered": sorted(st["mastered"]), "holds": sorted(st["holds"])}), len(log), learner_id])
        kinds = {}
        for x in log:
            kinds[x["activity"]] = kinds.get(x["activity"], 0) + 1
        rows = view(state, body.day)
        return 200, {"day": body.day, "activities": len(log), "correct": sum(x["correct"] for x in log), "by_activity": kinds, "sign_errors_diagnosed": sum(x["diagnosis"] == "sub_negative" for x in log), "log": log,
                     "mastered_now": sum(r["status"] == "mastered" for r in rows), "held_back_now": [r["concept"] for r in rows if r["status"] == "held back"], "misconceptions": {m: round(p, 3) for m, p in state["m"].items()},
                     "simulator_truth": {"really_mastered": len(st["mastered"]), "really_holds": sorted(st["holds"])}}
    return run(ctx, idem, body, work)


class ExperimentIn(BaseModel):
    students: int = Field(300, ge=20, le=600)
    days: int = Field(30, ge=5, le=60)


@app.post("/v1/experiments/adaptive-vs-fixed", status_code=202, tags=["analytics"], summary="(+) Teach the same simulated students two ways and compare what they know afterwards (job)")
def experiment(body: ExperimentIn, ctx: Ctx = Depends(auth("class:read")), idem: str | None = IdemKey):
    return run(ctx, idem, body, lambda c: (202, jobs.enqueue(c, ctx, "experiment.run", body.model_dump())))


@jobs.handler("experiment.run")
def experiment_job(c, job):
    n, days = job["payload"]["students"], job["payload"]["days"]
    learnt = {}
    arms = {"adaptive": world.run("adaptive", n, days, bandit=learnt), "fixed": world.run("fixed", n, days)}

    def arm(rows):
        return {k: round(statistics.mean(r[k] for r in rows), 2) for k in rows[0]}
    gain = [a["after"] - f["after"] for a, f in zip(arms["adaptive"], arms["fixed"])]
    later = [a["later"] - f["later"] for a, f in zip(arms["adaptive"], arms["fixed"])]
    ci = lambda xs: [round(statistics.mean(xs) - 1.96 * statistics.stdev(xs) / math.sqrt(len(xs)), 2), round(statistics.mean(xs) + 1.96 * statistics.stdev(xs) / math.sqrt(len(xs)), 2)]      # noqa: E731
    audit.record(c, Ctx(job["tenant_id"], uuid.UUID(job["payload"]["actor_id"]), "worker", "system"), "experiment.run", "course", COURSE, {"students": n, "days": days, "engine": engine.VERSION})
    return {"students": n, "days": days, "activities_per_day": 4, "concepts": len(cu.KEYS), "adaptive": arm(arms["adaptive"]), "fixed": arm(arms["fixed"]),
            "extra_concepts_mastered": {"at_the_end": round(statistics.mean(gain), 2), "ci95": ci(gain), "two_weeks_later": round(statistics.mean(later), 2), "ci95_later": ci(later), "students_better_off": sum(g > 0 for g in gain), "students_worse_off": sum(g < 0 for g in gain)},
            "control": f"every concept in course order, {world.FIXED_PER_CONCEPT} new items each, with a mixed review every fourth activity",
            "strategies_learnt": [{"strategy": s, "context": ctx_, "tries": v[1], "next_answer_correct": round(v[0] / v[1], 3)} for (s, ctx_), v in sorted(learnt.items(), key=lambda kv: (kv[0][1], -kv[1][1]))],
            "caveat": "The students are simulated, and they learn and forget in the way the tutor's model assumes (with different numbers). This shows the policy does what it is designed to do in that world; it is not evidence about real learners."}
