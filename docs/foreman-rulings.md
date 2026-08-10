# Foreman rulings — append-only

One line per ruling: date · id · the ruling · where it happened. Cite by id.
This file is the succession document (charter principle 7): a fresh Foreman
resumes from here.

| id | date | ruling | record |
|----|------|--------|--------|
| R1 | 2026-08-10 | Issues close when their PR lands, not before | pipeOS#13 reopen comment |
| R2 | 2026-08-10 | SHIP-authored fixes route to Foreman/BUILD to land, authorship preserved byte-identical | PR pipeOS#31 flow |
| R3 | 2026-08-10 | Merge gate: green CI on exact head + two TEST verdicts; Foreman (now BUILD) merges | thread 70 |
| R4 | 2026-08-10 | #650/#648 conflict: tooling belongs to BUILD, SHIP owns the spec; PAT fence stays intact | pipe#650 comment |
| R5 | 2026-08-10 | Claim key format: the mandate's `gh:pipe#<n>` / `gh:pipeOS#<n>` — repo-qualified, one key per item | thread 71, after box1's dual-claim proof |
| R6 | 2026-08-10 | Trivial-delta amendment: test-only or reviewer-dictated deltas need ONE delta-LGTM from a non-author TEST box, not a re-quorum | thread 71 |
| R7 | 2026-08-10 | TEST-author gate: when a TEST box authors, the gate is other-TEST verdict + SHIP diff-read (explicitly not a verdict) + green CI | thread 71 §293.2 |
| R8 | 2026-08-10 | Sam: #658 finding 5 — POT governs both eviction and reload; paid slots survive restarts | pipe#658 comment |
| R9 | 2026-08-10 | Sam: #641 trade approved — one-time prekeys withheld offline, signed prekey persists with rotation + retained predecessor; cost documented at the grant site | pipe#641 comment |
| R10 | 2026-08-10 | Sam: #647 closure was his; Variant B stands as #644/#659's basis | thread 71 |
| R11 | 2026-08-10 | Sam: claim capability allowlisted fleet-wide for the pipebox agent | thread 71 |
| R12 | 2026-08-10 | Sam: #538 own-nick machine tokens yes · #649 remove all four caps for observability · #514 dropped · #510 closed not-our-direction · #612 owner-parked, blocker unstated · #651 boot server = Sam's desktop · #458 = content marketing, spec proposes channels · #498 un-parked, accountability deferred | issues, per-item comments |
| R13 | 2026-08-10 | Sam: BUILD self-merges on fully satisfied gates; one-line board note per merge; first misuse revokes | DISPATCH v2 thread |
| R14 | 2026-08-10 | Dispatch threads rotate before the reply-ring cap; watcher change-detection must not rest on count | DISPATCH v2 thread, after the thread-71 deafness |
| R15 | 2026-08-10 | Open question, unruled: does *assigned-to-you* conflict a TEST box out of verdicting when someone else authored? Surfaced by PR #665; charter needs an answer | PR pipe#665 comment |
| R16 | 2026-08-10 | Resolves R15: AUTHORSHIP OVERRIDES ASSIGNMENT — a box is conflicted out of verdicting any commit containing a substantive change it authored, mechanically, regardless of assignment; substantive = R6 trivial-delta boundary; mixed-authorship conflicts attach per-commit, whole-PR when unseverable | pipeOS#39 comment |
| R17 | 2026-08-10 | Comment-drift (pipe#668): case-by-case PINNING where a property is cheap and security- or data-integrity-facing; NO blanket "name your proving test" convention — per box3, it would not have caught any of the four known cases and costs friction on every comment | pipe#668 comment |
