-- Domain model. Names follow the blueprint's schema; additions are marked (+).
-- Learners are known only by an external id: no names, no contact details.

CREATE TABLE concepts (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, course_id uuid NOT NULL, name text NOT NULL, description text, difficulty real,
  key text NOT NULL, ordinal int NOT NULL, exposes jsonb NOT NULL DEFAULT '[]'::jsonb, UNIQUE (tenant_id, course_id, key));          -- (+)
SELECT enable_tenant_rls('concepts');

CREATE TABLE concept_edges (prerequisite_id uuid REFERENCES concepts ON DELETE CASCADE, concept_id uuid REFERENCES concepts ON DELETE CASCADE, weight real, PRIMARY KEY (prerequisite_id, concept_id),
  tenant_id uuid NOT NULL REFERENCES tenants);
SELECT enable_tenant_rls('concept_edges');

CREATE TABLE learners (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, external_id text NOT NULL, settings jsonb NOT NULL DEFAULT '{}'::jsonb, UNIQUE (tenant_id, external_id));
SELECT enable_tenant_rls('learners');

CREATE TABLE items (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, concept_id uuid NOT NULL REFERENCES concepts ON DELETE CASCADE, stem text NOT NULL, answer int NOT NULL, bugs jsonb NOT NULL,     -- (+) generated practice items
  checked boolean, created_at timestamptz NOT NULL DEFAULT now());
SELECT enable_tenant_rls('items');

CREATE TABLE attempts (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, learner_id uuid NOT NULL REFERENCES learners ON DELETE CASCADE, concept_id uuid NOT NULL REFERENCES concepts, item_id uuid NOT NULL REFERENCES items,
  score real NOT NULL, response_time_ms int, created_at timestamptz NOT NULL DEFAULT now(),
  response int NOT NULL, diagnosis text, day int NOT NULL, activity text);                                                              -- (+)
CREATE INDEX attempts_learner ON attempts (learner_id, created_at);
SELECT enable_tenant_rls('attempts');

CREATE TABLE mastery (learner_id uuid REFERENCES learners ON DELETE CASCADE, concept_id uuid REFERENCES concepts ON DELETE CASCADE, probability real NOT NULL, uncertainty real NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (learner_id, concept_id), tenant_id uuid NOT NULL REFERENCES tenants, seen int NOT NULL DEFAULT 0, reviews int NOT NULL DEFAULT 0, last_day int);   -- (+)
SELECT enable_tenant_rls('mastery');

CREATE TABLE misconceptions (learner_id uuid REFERENCES learners ON DELETE CASCADE, key text NOT NULL, probability real NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (learner_id, key),       -- (+)
  tenant_id uuid NOT NULL REFERENCES tenants);
SELECT enable_tenant_rls('misconceptions');

-- One row per recommended activity; `outcome` is filled by the learner's next answer on that concept, which is what the
-- strategy bandit learns from.
CREATE TABLE interventions (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, learner_id uuid NOT NULL REFERENCES learners ON DELETE CASCADE, concept_id uuid NOT NULL REFERENCES concepts, strategy text NOT NULL, outcome jsonb,
  kind text NOT NULL, context text, created_at timestamptz NOT NULL DEFAULT now());                                                     -- (+)
CREATE INDEX interventions_open ON interventions (learner_id, concept_id) WHERE outcome IS NULL;
SELECT enable_tenant_rls('interventions');

CREATE TABLE sim_students (learner_id uuid PRIMARY KEY REFERENCES learners ON DELETE CASCADE, tenant_id uuid NOT NULL REFERENCES tenants, hidden jsonb NOT NULL, steps int NOT NULL DEFAULT 0);    -- (+) the simulator's hidden truth for demo learners
SELECT enable_tenant_rls('sim_students');
