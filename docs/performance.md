# Performance

Docker stack (one uvicorn process, one worker, Postgres 16) after the demo: 18 concepts, one learner, 127 attempts.
Reproduce with `make demo && make load-test`.

## Reads: learner list and knowledge graph (in rotation), closed loop, 10 s per level

| Concurrency | Requests | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| 1 | 3493 | 349.2 req/s | 2.8 ms | 4.0 ms | 4.3 ms | 0.00% |
| 8 | 5893 | 588.8 req/s | 13.0 ms | 17.1 ms | 19.1 ms | 0.00% |
| 32 | 4347 | 432.8 req/s | 55.1 ms | 62.6 ms | 67.6 ms | 0.00% |

Target from the blueprint: p95 under 400 ms for reads: met. This is a small data set and says little beyond that.

## Compute

| Work | Time |
|---|---|
| One recorded answer: load the learner's 18 mastery rows and 4 misconception rows, update, store | about 4 ms |
| A simulated session of 12 activities | about 40 ms |
| Experiment job: 300 students x 2 arms x 120 activities (72,000 simulated answers) | about 1.2 s |
| Evaluation: 400 students x 3 arms plus 600 placement quizzes | about 2 s |

The learner model is two small tables per learner and a pure function, so cost per answer does not grow with class size. The
presentation bandit reads its counts with one grouped query over `interventions` on every recommendation; at millions of rows
that would need a running tally instead.

Not measured: real classes. A term of real attempts, with a clock instead of a passed-in day, was not simulated.
