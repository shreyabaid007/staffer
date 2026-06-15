# Tasks — 001 Gates & Rank

> Ordered, atomic, independently testable. Each maps to acceptance criteria in requirements.md.
> One task = one commit (imperative, referencing the spec).

## Task list

### T-001: Create test fixtures (ROLE-01, ROLE-02, ROLE-03)

**File:** `tests/fixtures/__init__.py`

Create importable fixture functions returning `(list[Candidate], TargetProfileScorecard)` for each role:

- **ROLE-01** (partial exclusion): Aarav (RollingOff, 2026-08-01, Chennai) excluded on availability; Karan (FreeNow, Chennai), Vivaan (RollingOff 2026-07-10, Chennai), Rahul (FreeNow, Chennai), Vikram (NewJoiner 2026-07-14, Chennai, unverified) pass. Role: Chennai, co_location=True, start=2026-07-01, window=14d.
- **ROLE-02** (location filter): Chennai co-location role. Adds Deepa (Pune, not remote), Nikhil (Bangalore, not remote) excluded; Priya (Pune, remote_eligible=True) passes.
- **ROLE-03** (total exclusion): Mumbai co-location role, start=2026-07-01, window=14d. Sanjay (RollingOff 2026-07-16, Mumbai, +1d overshoot), Meera (NewJoiner 2026-08-15, Mumbai, +31d overshoot), Arjun (FreeNow, Pune, not remote), Kavita (FreeNow, Kolkata, not remote). All fail. Expected near-miss order: Sanjay, Meera, Arjun (capped at 3).

**Acceptance:** fixtures import cleanly; `make check` green (no test failures from fixture import).

**Criteria:** E-R01, E-R02, E-R03 (data foundation).

---

### T-002: Implement location gate

**File:** `dsm/match/gates.py`

Replace the stub `filter_candidates` with real location gate logic:
- `co_location_required=True` → city match (case-insensitive) OR `remote_eligible=True`.
- `co_location_required=False` → all pass.
- Excluded candidates get `Exclusion(reason=LOCATION_MISMATCH, detail=...)` with both cities in detail.

Do NOT implement availability gate yet — candidates that pass location proceed to a temporary pass-all availability check.

**Acceptance:** `tests/match/test_gates.py` has location-specific tests covering G-LOC-1 through G-LOC-4. `make check` green.

**Criteria:** G-LOC-1, G-LOC-2, G-LOC-3, G-LOC-4, G-OUT-1.

---

### T-003: Implement availability gate

**File:** `dsm/match/gates.py`

Add availability gate after location gate:
- `FreeNow` → always pass.
- `RollingOff` → `expected_date <= deadline`. Confidence is ignored per AD-022.
- `NewJoiner` → `join_date <= deadline`.
- Deadline = `scorecard.start_date + timedelta(days=scorecard.availability_window_days)`.
- If candidate already failed location, skip availability (G-OUT-2).
- Excluded candidates get `Exclusion(reason=AVAILABILITY_MISMATCH, detail=...)` with both dates in detail.

**Acceptance:** `tests/match/test_gates.py` has availability-specific tests covering G-AVL-1 through G-AVL-6, plus:
- Boundary: exactly +14d (window_days=14) → passes.
- Boundary: +15d → excluded.
- RollingOff at each confidence level (high/medium/low) gates identically.
- Both-gates-fail: only location exclusion recorded.

**Criteria:** G-AVL-1, G-AVL-2, G-AVL-3, G-AVL-4, G-AVL-5, G-AVL-6, G-OUT-2.

---

### T-004: Implement rank sort + tie-break + top-k

**File:** `dsm/match/rank.py`

Replace the stub `rank_assessments` with:
- Sort by `combined_score` desc → `hard_skill_coverage` desc → `desired_skill_coverage` desc → `candidate.email` asc.
- Slice to `top_k` (from function argument, default 5).
- Populate `config_snapshot` with weights, top_k, model IDs.
- Empty assessments → `ShortlistResult(ranked_assessments=[], total_eligible=0, ...)`.

**Acceptance:** `tests/match/test_rank.py` covers:
- Basic sort order (different combined_scores).
- Tie-break on each level (same combined_score, different hard_skill_coverage; same both, different desired; same all three, email alphabetical).
- Top-k truncation (6 candidates, top_k=5 → 5 returned).
- Empty input → empty ShortlistResult.
- Determinism: run same input twice, assert identical output.

**Criteria:** R-SORT-1, R-TIE-1, R-TOP-1, R-OUT-1.

---

### T-005: Implement orchestrator no-match path

**File:** `dsm/cli/commands.py`

Add `build_near_misses(candidates, scorecard, exclusion_log)` helper and integrate into `match`:
- When `eligible_pool.candidates` is empty, build `NoMatchResult`.
- Recompute gaps from structured data (candidate + scorecard), NOT from `Exclusion.detail`.
- Order per AD-063(b): availability misses first (smallest overshoot), then location misses (remote_eligible=True preferred).
- Cap at 3 per AD-063(d).
- Render `NoMatchResult` as JSON to CLI.

**Acceptance:** `tests/cli/test_no_match.py` covers:
- ROLE-03 fixture → NoMatchResult with near_misses = [Sanjay, Meera, Arjun] in that order.
- Near-miss gap_summary strings are human-readable.
- Cap: fixture with >3 exclusions → only 3 near-misses.
- Orchestrator does NOT call rank when pool is empty.

**Criteria:** O-NM-1, O-NM-2, O-NM-3, O-NM-4, O-NM-5, E-R03.

---

### T-006: Wire CLI end-to-end + ROLE-01/02 integration tests

**File:** `dsm/cli/commands.py`, `tests/cli/test_match_e2e.py`

Update `dsm match` to:
- Accept `--role-id` and load fixtures for ROLE-01/02/03 (or fall back to stub for unknown IDs).
- Show real gate exclusions in output.
- Show real ranking over stubbed assessments (scoring is still stubbed).
- Show no-match path for ROLE-03.

**Acceptance:** Integration tests verify:
- ROLE-01: Aarav excluded with detail containing both dates; 4 candidates ranked.
- ROLE-02: Deepa + Nikhil excluded on location; Priya + Chennai candidates ranked.
- ROLE-03: NoMatchResult rendered with 3 near-misses.
- `make check` green.

**Criteria:** E-R01, E-R02, E-R03 (end-to-end).

---

### T-007: Final verification + harness

**Files:** all modified files.

- Run `make check` — must be fully green (format, lint, typecheck, all tests, import contracts).
- Verify import-linter: `gates.py` imports nothing from `pii/`, `index/`, or LLM code.
- Verify all EARS criteria from requirements.md pass.

**Acceptance:** `make check` green. All 22 acceptance criteria (G-LOC-*, G-AVL-*, G-OUT-*, R-*, O-NM-*, E-R*) covered by tests.

**Criteria:** all.
