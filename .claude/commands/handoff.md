End-of-session handoff. Updates **one lane file**. Do all steps, then show the diff.

## 1. Resolve the lane
Read the lane (A | B | C) from `.claude/lane` — its contents are the lane letter. Ignore any command argument; the lane always comes from this file. If `.claude/lane` is missing or does not contain a valid lane letter, **stop and ask the human** — do not guess.

Set `LANE_FILE = docs/progress.<lane>.md`.

## 2. Read only what you need
Read **only** the index `docs/progress.md` and your `LANE_FILE`. Do not read other lane files.

## 3. Gather state
1. Run `git log --oneline` since the date of the newest entry in your `LANE_FILE` **Session log** and summarise what changed in your lane.
2. Run `make check` and record whether it is **GREEN** or **RED**.

## 4. Write only your lane file
In `LANE_FILE` only:
- Rewrite the **In flight**, **Next up**, and **Blockers / needs a human** sections from current state.
- Prepend **one** dated line to **Session log (append-only — newest first)** summarising this session and noting GREEN/RED. Never edit or delete existing log lines — append-only.

## 5. Touch no other file
Change **no file other than your `LANE_FILE`** — not the index, not another lane file. The shared index `docs/progress.md` is refreshed only at merge to `main`, by whoever merges, via `/handoff-index`. Then show the diff of `LANE_FILE`.
