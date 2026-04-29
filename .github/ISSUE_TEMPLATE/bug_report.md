---
name: Bug report
about: Report a defect in the framework (broker, hindcast, gates, clients, …)
title: "bug: <one-line summary>"
labels: ["bug"]
---

## What happened

<concise description of the actual behaviour>

## What you expected

<what should have happened — invariant ID if applicable>

## Reproduction

```bash
# minimal commands or pytest invocation that reproduces the bug
uv run pytest -q tests/test_<file>.py::<test>
```

If reproduction needs code:

```python
# minimal snippet
```

## Environment

- GLOSTAT version: `python -c "import glostat; print(glostat.__version__)"`
- Python version: `python --version`
- OS: macOS / Linux / Windows + version
- `GLOSTAT_PHASE`: mvp / phase_2 / phase_3
- Network test mode: `NETWORK_TESTS` set?

## Logs / traceback

```text
<paste traceback here>
```

## Invariants potentially affected

- INV-GS-NNN: <if you suspect an invariant is being violated>

## Anything else
