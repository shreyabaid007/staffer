End-of-session handoff. Do all of these steps, then show the diff of `docs/progress.md`.

1. Run `git log --oneline` since the last entry in the Session log of `docs/progress.md` and summarise what changed.
2. Run `make check` and record whether it is green or red.
3. Rewrite these sections of `docs/progress.md` from the current repo state:
   - **Current status** (build phase, active slice, harness result, main branch state)
   - **Works end-to-end right now**
   - **In flight**
   - **Next up**
   - **Blockers / needs a human**
   - **Watch-outs / gotchas**
   - **Active specs**
   - **Decisions** (update the ADR range if new ones were added)
4. Prepend a dated line to the **Session log** section summarising this session.
5. Change **no other file**.
6. Show the full diff of `docs/progress.md` so I can review before committing.
