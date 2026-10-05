"""Integration, end-to-end and security tests against a real Postgres."""
import pytest

from core import db, jobs, scenario
from tu import curriculum as cu

from .conftest import bearer

ADMIN, TEACHER, STUDENT, OTHER = bearer("school", "admin"), bearer("school", "teacher"), bearer("school", "student"), bearer("other", "admin")
ZERO = "00000000-0000-0000-0000-000000000000"
SIGN = {k for k in cu.KEYS if "sub_negative" in cu.EXPOSES[k]}


def test_nothing_works_before_the_course_is_loaded(client):
    assert client.post("/v1/content/generate", headers=TEACHER, json={"concept": "int_add"}).json()["code"] == "no_course"


@pytest.fixture(scope="module")
def demo(client):
    results, _ = scenario.run(client, drain=jobs.drain)
    return results


def one(sql, params=()):
    with db.tx() as c:
        return c.execute(sql, params).fetchone()


def test_demo_journey(demo):
    assert demo["load"]["concepts"] == 18 == len(demo["graph"]["nodes"]) and len(demo["graph"]["edges"]) == demo["load"]["prerequisite_links"]
    diag, m0 = demo["diag"], demo["m0"]
    assert diag["activities"] == 18 and diag["sign_errors_diagnosed"] >= 4 and diag["simulator_truth"]["really_holds"] == ["sub_negative"]
    top = m0["misconceptions"][0]
    assert top["key"] == "sub_negative" and top["probability"] >= 0.9 and set(top["affects"]) == SIGN
    for r in m0["concepts"]:                                              # trust falls only where the concept depends on subtracting negatives
        assert (r["probability"] - r["trusted"] > 0.5 * r["probability"]) == (r["concept"] in SIGN)
    assert {n["key"] for n in demo["graph0"]["nodes"] if n["held_back_by"]} == SIGN
    item = demo["gen"]["items"][0]
    assert item["concept"] == "eq_add" and item["bugs"]["sub_negative"] != item["answer"]
    t1 = demo["tutor1"]
    assert not t1["correct"] and t1["diagnosis"] == "sub_negative" and not t1["answer_revealed"] and str(item["answer"]) not in t1["message"].split("Try")[0].replace("7 - (-3)", "").replace("7 + 3 = 10, not 4", "")
    assert "misconception:sub_negative" in t1["sources"] and cu.MISCONCEPTIONS["sub_negative"][1] in t1["message"]
    assert demo["att"]["diagnosis"] == "sub_negative" and not demo["att"]["correct"]
    assert (demo["next"]["activity"], demo["next"]["misconception"], demo["next"]["strategy"]) == ("remediate", "sub_negative", "worked_example") and "6 concepts depend" in demo["next"]["reason"]
    assert demo["d1"]["by_activity"].get("remediate", 0) >= 1 and demo["d4"]["misconceptions"]["sub_negative"] < 0.3 and demo["d4"]["simulator_truth"]["really_holds"] == []
    assert sum(demo[f"d{d}"]["by_activity"].get("review", 0) for d in (6, 8, 10, 12, 14)) >= 5
    last = demo["d14"]
    assert last["simulator_truth"]["really_mastered"] > 8 and abs(last["mastered_now"] - last["simulator_truth"]["really_mastered"]) <= 4 and demo["m14"]["mastered"] > demo["m0"]["mastered"]
    assert any(r["review_due_day"] and r["half_life_days"] > 6 for r in demo["m14"]["concepts"])
    t2 = demo["tutor2"]
    assert t2["diagnosis"] is None and t2["answer_revealed"] and t2["sources"] == ["concept:eq_add"] and demo["bad"]["code"] == "validation_failed"
    exp = demo["exp"]["result"]
    assert exp["adaptive"]["after"] > exp["fixed"]["after"] + 3 and exp["extra_concepts_mastered"]["ci95"][0] > 0 and "simulated" in exp["caveat"]
    assert exp["adaptive"]["before"] == exp["fixed"]["before"] and exp["extra_concepts_mastered"]["students_better_off"] > 250
    assert demo["audit"]["chain_valid"] and {"scenario.loaded", "experiment.run"} <= {e["action"] for e in demo["audit"]["events"]}


def test_a_retried_answer_counts_once_and_closes_the_open_recommendation(client, demo):
    learner = demo["load"]["learner_id"]
    rec = client.post("/v1/recommendations/next", headers=STUDENT, json={"learner_id": learner, "day": 15}).json()
    assert one("SELECT count(*) AS n FROM interventions WHERE learner_id = %s AND outcome IS NULL", [learner])["n"] >= 1
    body, key = {"learner_id": learner, "item_id": rec["item"]["item_id"], "response": 123456, "day": 15}, {**STUDENT, "Idempotency-Key": "answer-1"}
    before = one("SELECT count(*) AS n FROM attempts WHERE learner_id = %s", [learner])["n"]
    first, again = client.post("/v1/attempts", headers=key, json=body), client.post("/v1/attempts", headers=key, json=body)
    assert first.status_code == 201 and again.json() == first.json() and again.headers["Idempotent-Replay"] == "true" and not first.json()["correct"]
    assert one("SELECT count(*) AS n FROM attempts WHERE learner_id = %s", [learner])["n"] == before + 1
    assert one("SELECT count(*) AS n FROM interventions i JOIN concepts k ON k.id = i.concept_id WHERE i.learner_id = %s AND k.key = %s AND i.outcome IS NULL", [learner, rec["concept"]])["n"] == 0
    assert first.json()["mastery_after"] <= first.json()["mastery_before"] + 0.21


def test_generated_content_is_reproducible_and_validated(client, demo):
    a = client.post("/v1/content/generate", headers=TEACHER, json={"concept": "order_ops", "count": 5, "seed": 42}).json()["items"]
    b = client.post("/v1/content/generate", headers=TEACHER, json={"concept": "order_ops", "count": 5, "seed": 42}).json()["items"]
    assert [(x["stem"], x["answer"]) for x in a] == [(x["stem"], x["answer"]) for x in b] and all(x["independently_checked"] for x in a) and a[0]["item_id"] != b[0]["item_id"]
    assert client.post("/v1/content/generate", headers=TEACHER, json={"concept": "area", "target_misconception": "sub_negative"}).json()["code"] == "unknown_concept_or_misconception"
    assert client.post("/v1/content/generate", headers=TEACHER, json={"concept": "calculus"}).status_code == 422


def test_learners_are_known_by_external_id_only(client, demo):
    rows = client.get("/v1/learners", headers=TEACHER).json()["items"]
    assert rows and set(rows[0]) == {"id", "external_id", "attempts"}


@pytest.mark.parametrize("headers,method,path,body,expected", [
    ({}, "GET", "/v1/learners", None, 401),
    (TEACHER, "GET", "/v1/learners", None, 200),
    (STUDENT, "GET", "/v1/learners", None, 403),
    (STUDENT, "POST", "/v1/content/generate", {"concept": "int_add"}, 403),
    (STUDENT, "POST", f"/v1/simulations/learners/{ZERO}/session", {}, 403),
    (STUDENT, "POST", "/v1/experiments/adaptive-vs-fixed", {}, 403),
    (TEACHER, "POST", "/v1/scenarios/classroom", None, 403),
    (TEACHER, "GET", "/v1/audit", None, 403),
    (STUDENT, "GET", f"/v1/learners/{ZERO}/mastery", None, 404),
    (STUDENT, "POST", "/v1/recommendations/next", {"learner_id": ZERO}, 404),
    (STUDENT, "POST", "/v1/attempts", {"learner_id": ZERO, "item_id": ZERO, "response": 1}, 404),
    (STUDENT, "POST", "/v1/attempts", {"learner_id": ZERO, "item_id": ZERO, "response": 1.5}, 422),
    (STUDENT, "POST", "/v1/tutor/respond", {"learner_id": ZERO, "item_id": ZERO, "response": 1, "attempt_no": 99}, 422),
    (TEACHER, "POST", "/v1/experiments/adaptive-vs-fixed", {"students": 100000}, 422),
    (TEACHER, "GET", f"/v1/courses/{ZERO}/knowledge-graph", None, 404),
])
def test_authorization_and_validation(client, demo, headers, method, path, body, expected):
    assert client.request(method, path, headers=headers, json=body).status_code == expected


def test_one_school_cannot_see_or_touch_anothers_learners(client, demo):
    learner, item = demo["load"]["learner_id"], demo["gen"]["items"][0]["item_id"]
    mine = one("SELECT count(*) AS n FROM attempts WHERE learner_id = %s", [learner])["n"]
    assert client.get(f"/v1/learners/{learner}/mastery", headers=OTHER).status_code == 404
    theirs = client.post("/v1/scenarios/classroom", headers=OTHER).json()            # their load deletes "everything": only their own rows
    assert theirs["learner_id"] != learner and theirs["concepts"] == 18
    assert client.post("/v1/attempts", headers=OTHER, json={"learner_id": theirs["learner_id"], "item_id": item, "response": 1}).status_code == 404       # not their item
    assert client.post("/v1/attempts", headers=OTHER, json={"learner_id": learner, "item_id": item, "response": 1}).status_code == 404
    assert client.post(f"/v1/simulations/learners/{learner}/session", headers=OTHER, json={}).status_code == 404
    assert client.get(f"/v1/learners/{theirs['learner_id']}/mastery", headers=OTHER).json()["attempts"] == 0
    assert one("SELECT count(*) AS n FROM attempts WHERE learner_id = %s", [learner])["n"] == mine and client.get(f"/v1/learners/{learner}/mastery", headers=TEACHER).json()["attempts"] == mine
