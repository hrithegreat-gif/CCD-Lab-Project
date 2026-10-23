# Detection migration evidence

2026-10-23: pre-migration cached real sentiment preparation passed; full baseline
`python -m pytest tests/ -q -p no:cacheprovider`: 296 passed in 8.30 seconds.
These are historical baseline results, not detection acceptance.

The owner intentionally removed the previous docs and deferred the final report.
Detection export, inference, container, scaling and AWS acceptance are pending.

Migration phase 1: optional adapters and their tests removed; remaining full suite: 232 passed in 3.53 seconds. The reduction is the explicitly removed adapter coverage; core safety/lifecycle coverage remains.
