---
name: Enhancement
about: Propose an improvement to the framework infrastructure
title: "enh: <one-line summary>"
labels: ["enhancement"]
---

## Problem

<what is the user / developer pain point this addresses?>

## Proposed change

<concrete description of the new behaviour or interface>

## Touched components

- [ ] Snapshot Broker (`src/glostat/data/snapshot_broker.py`)
- [ ] Data Router (`src/glostat/data/data_router.py`)
- [ ] Hindcast Harness (`src/glostat/replay/validation_harness.py`)
- [ ] Kill Criteria (`src/glostat/replay/kill_criteria.py`)
- [ ] Compliance Gate (`src/glostat/risk/compliance_gate.py`)
- [ ] Experts (`src/glostat/experts/*`)
- [ ] CLI (`src/glostat/cli*.py`)
- [ ] Other: ___

## Invariant impact

- New invariants required: INV-GS-NNN — <summary>
- Existing invariants affected: <IDs>
- Does this weaken any default `PassCriteria`? Yes / No (justify if yes)

## Backwards compatibility

- Public API change? Yes / No
- Config schema change? Yes / No
- Snapshot-broker on-disk format change? Yes / No

## Alternatives considered

<what other shapes did you consider, and why did you reject them?>

## Test plan

- Unit tests:
- Integration / hindcast smoke:
- Network test (if data-source touching):
