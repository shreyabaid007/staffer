<!-- Keep this short. Delete sections that don't apply. -->

## What & why


## Checklist
- [ ] `make check` green (includes `tests/docs` doc invariants + frozen-contract snapshot).
- [ ] New behaviour has a test; no eval invariant relaxed to pass.
- [ ] **Decisions:** any real decision recorded in `docs/decision.md` as a **new/superseding** entry (never a silent edit). On a branch, used an `AD-XXX` placeholder — real id assigned at merge.
- [ ] **If ADRs changed:** refreshed the `docs/progress.md` index via `/handoff-index` (the AD-range is machine-checked; the prose sections are not).
- [ ] **Lane file** `docs/progress.<lane>.md` updated via `/handoff` for the next session.
- [ ] **Doc hygiene:** no config values or counts restated in living prose (cite the key); design docs (`ee-*-architecture.md`) still accurate or annotated.
- [ ] **Frozen contract:** if `dsm/models.py` changed, regenerated the snapshot (`make contract-snapshot`) and the change is ADR-backed.
