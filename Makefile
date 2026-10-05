export DATABASE_URL ?= postgresql://app:app-dev-only@127.0.0.1:55207/app
PY = ./venv/bin/python
BASE = http://127.0.0.1:8207

bootstrap:      ## local virtualenv + database container
	python3 -m venv venv && ./venv/bin/pip install -q -r requirements-dev.txt
	docker compose up -d db
seed:           ## migrate and load the synthetic world (no-op if already seeded)
	$(PY) -m tu.seed --if-empty
dev:            ## API with reload on :8207, jobs run by a local worker
	($(PY) -m core.jobs tu.api &) && $(PY) -m uvicorn tu.api:app --reload --port 8207
test:
	$(PY) -m pytest --cov=tu --cov=core --cov-report=term-missing
demo:           ## the full stack in Docker, then the demo scenario against it
	docker compose up --build -d --wait && $(PY) -m core.scenario $(BASE)
load-test:      ## run `make demo` first
	$(PY) -m core.loadtest $(BASE) school-teacher-demo 32 10 "GET /v1/learners" "GET /v1/courses/5a1d3c0a-0000-4000-8000-0000000a19eb/knowledge-graph" 
reset:          ## drop all data (volume included)
	docker compose down -v
.PHONY: bootstrap seed dev test demo load-test reset
