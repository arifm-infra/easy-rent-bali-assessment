# AI Session Logs — Steering Audit

**Assessment:** Easy Rent Bali — Option C: Incident Response, Network Failover & Fleet Triage  
**Date:** 2026-10-30  
**Candidate note:** This audit documents real AI-assisted development decisions, defects discovered during testing and review, and corrective actions taken. All entries are factual; no examples are fabricated.

---

## Purpose

The steering audit demonstrates:
1. That AI assistance was used transparently, not as a black box.
2. That AI-generated code and documentation was independently reviewed and tested.
3. That genuine defects were caught, documented, and fixed.
4. That the candidate exercised engineering judgment rather than blindly accepting AI output.

---

## How to Capture Raw Logs

When using Antigravity (or any AI assistant), save your actual prompt/response pairs to `ai_session_logs/raw_logs/` immediately after each significant iteration. Use the template in `ai_session_logs/raw_logs/SESSION_TEMPLATE.md`.

Name files chronologically: `SESSION_001.md`, `SESSION_002.md`, etc.

**Important:** Only record actual prompts and responses. Never fabricate or reconstruct conversations from memory.

---

## Audit Entry Format

Each entry must contain:
- **Iteration:** Sequential number.
- **Prompt summary:** What you asked the AI (paraphrased or verbatim).
- **AI output summary:** What the AI generated.
- **Defect identified:** The exact problem found during review/testing.
- **Evidence:** Failing test output, affected line numbers, or error message.
- **Corrective prompt:** What you asked the AI (or did manually) to fix it.
- **Verified resolution:** How you confirmed the fix worked (test name, output).

---

## Audit Entries

### Entry 1 — WAN2 Guest Bandwidth Cap: `≤ 128 kbps` Is Not Strictly Below 128 kbps

| Field | Value |
|-------|-------|
| **Iteration** | 1 |
| **Prompt summary** | "The assessment requires guest traffic to remain strictly below 128 kbps on WAN2. Inspect the implementation and correct it if the cap is not strictly compliant." |
| **AI output summary** | The AI initially generated `≤ 128 kbps` in three locations in `NETWORK_ARCHITECTURE.md` (line 198, Section 4.2 table row, and the implementation prose). This satisfies `≤ 128` but not `< 128` — at exactly 128 kbps the constraint would be violated. |
| **Defect identified** | `≤ 128 kbps` is ambiguous at the boundary. The PDF states `< 128 kbps`. A policing rule set to 128 kbps would allow exactly 128 kbps, which is not strictly less than the limit. |
| **Evidence** | `NETWORK_ARCHITECTURE.md` L198: `capped at ≤ 128 kbps via traffic policing` — fails the strict `< 128 kbps` requirement. `README.md` L214: `capped at ≤ 128 kbps` — same stale value. |
| **Corrective prompt** | "Correct all occurrences of `≤ 128 kbps` to a clearly compliant value such as 120 kbps. Update documentation and README to explain the correction is to guarantee strict compliance." |
| **Verified resolution** | `grep -r "128 kbps"` across the repo: all three `NETWORK_ARCHITECTURE.md` locations now read `≤ 120 kbps (strictly below the 128 kbps assessment limit)`. `README.md` updated to match. Full text scan confirmed — `120 kbps` is the implemented limit; `128 kbps` appears only as the reference to the PDF constraint. |

---

### Entry 2 — Failover Timing Arithmetic: 3-Miss × 1 s Exceeds the 3-Second Budget

| Field | Value |
|-------|-------|
| **Iteration** | 2 |
| **Prompt summary** | "Verify that the 1-second ping interval and 3-missed-ping threshold are consistent with the < 3-second failover design target. Calculate the detection timing." |
| **AI output summary** | The original document stated: failure threshold = 3 consecutive losses; ping interval = 1 s; design target = < 3 seconds; parenthetical: "(3 missed pings × 1s + switch overhead)". |
| **Defect identified** | Arithmetic contradiction. With a 1-second ping interval and 3-loss threshold, detection alone takes exactly 3 seconds (ping 1 at t=0 lost, ping 2 at t=1 lost, ping 3 at t=2 lost — third loss confirmed at t=3 s). Any routing-switch overhead then pushes the total past 3 seconds. The parenthetical "(3 missed pings × 1s + switch overhead)" therefore describes a scenario that **cannot** meet the `< 3 s` target. |
| **Evidence** | Python calculation (executed in session): `interval=1.0s × threshold=3 = detection_floor=3.0s`. Output: *"With 1s interval and 3-miss threshold, detection alone takes exactly 3s. Any routing/failover overhead pushes TOTAL past 3s. The current doc claim is TECHNICALLY INCORRECT for the stated parameters."* Affected lines: `NETWORK_ARCHITECTURE.md` L238–L240. |
| **Corrective prompt** | "Change the failure threshold to 2 consecutive losses. Add a timing rationale explaining: detection = 2 s (2 × 1 s pings), leaving < 1 s headroom for routing convergence = total < 3 s. Explicitly state that 3-miss at 1 s would exhaust the budget. Keep the design-target caveat that no hardware testing was done." |
| **Verified resolution** | `NETWORK_ARCHITECTURE.md` L238 now reads: `2 consecutive losses`. L240: `(2 missed pings × 1 s = 2 s detection + < 1 s routing switch = total < 3 s)`. L244–L245: prose timing rationale added. `README.md` assumption #9 updated to explain the 2-miss threshold and budget rationale. `grep` scan confirms no remaining `3 consecutive` in a failover-timing context. All 52 tests still pass (documentation-only change; no code modified). |

---

## Additional Implementation Notes (Factual, From Build Session)

The following were observed and corrected during the initial build session. **These are
condensed summaries; no verbatim prompt/response transcripts exist for entries 3–5 because
the raw Antigravity session logs were not captured at the time. The supporting evidence
(failing test output and affected lines) is real; the prompts are paraphrased from memory
and cannot be verified as literal AI interactions. See the raw\_logs/ directory note below.**

3. **Idempotency store race window** — The original design stored the idempotency record in a separate statement *after* the `COMMIT`. This created a window where a concurrent retry could see no idempotency record and re-execute the payment. Corrected by moving the idempotency `INSERT` inside the same `BEGIN EXCLUSIVE … COMMIT` block as the reservation. Verified by `test_no_duplicate_side_effects_on_replay` — only 1 reservation row created across 3 replays.

4. **`OFFLINE_TELEMETRY_DROP` boundary** — The rule uses *strictly greater than* 900 seconds. A naive `>= 900` would incorrectly tag vehicles pinging exactly at the 15-minute boundary. Tests `test_rented_exactly_threshold_no_trigger` (last_ping=900 → no tag) and `test_rented_over_threshold_triggers` (last_ping=901 → tagged) verify the strict comparison.

5. **Windows file-lock on SQLite cleanup** — Initial concurrency tests produced `7 ERRORs: PermissionError: [WinError 32]` because `os.unlink(db_path)` was called while the SQLite connection was still open. Windows holds a file lock until the connection is explicitly closed. Fixed by adding `_cleanup(svc, db_path)` which calls `svc._conn.close()` before `os.unlink()`. Evidence: first run `Ran 8 tests … FAILED (errors=7)`; after fix `Ran 8 tests … OK`.

---

## Repair Session — 2026-10-30 (Independent Audit Findings)

This section documents defects identified and corrected during the post-build independent
Claude Opus audit. These are **code defects in the repository**; they were found by
independent review, not by tracing AI-generated code to a specific session log.
No raw session transcripts are available from the original build session, so it cannot be
determined whether any of these defects were introduced by AI suggestion or by manual
authoring. All claims are limited to what the repository evidence supports.

### Repair 1 — VLAN 50 DHCP Subnet Mask (Critical)

| Field | Value |
|-------|-------|
| **File / line** | `docs/NETWORK_ARCHITECTURE.md` line 202 |
| **Defect** | DHCP range example used `255.252.0.0` (/14) instead of `255.255.252.0` (/22) for the `172.16.48.0/22` network. |
| **Evidence** | `python -c "import ipaddress; print(ipaddress.IPv4Network('172.16.48.0/22').netmask)"` → `255.255.252.0`. Old value `255.252.0.0` = /14, spanning a far larger range. |
| **Correction** | Changed `255.252.0.0` → `255.255.252.0` in the dnsmasq example. Repo-wide scan confirmed no other occurrences. Canonical network note (`172.16.48.0/22`) and assessment traceability note (`172.16.50.0/22`) preserved. |
| **AI attribution** | Cannot be verified — no raw session log for the file's creation. |

### Repair 2 — Misleading SQLite `isolation_level` Comment (Medium)

| Field | Value |
|-------|-------|
| **File / line** | `src/patch/checkout_handler.py` line 79 |
| **Defect** | `isolation_level=None` sets autocommit **ON** in Python's sqlite3. Comment said "autocommit off". |
| **Correction** | Comment updated: "autocommit ON; transactions are managed explicitly via BEGIN EXCLUSIVE / COMMIT / ROLLBACK". No logic changed. |
| **AI attribution** | Cannot be verified — no raw session log. |

### Repair 3 — Conflict-Path Idempotency Persistence (Medium)

| Field | Value |
|-------|-------|
| **File / lines** | `src/patch/checkout_handler.py` lines 272–281 (pre-repair) |
| **Defect** | After a vehicle-conflict 409, the handler attempted to persist the idempotency record in a bare `INSERT OR IGNORE` with no explicit transaction and `except Exception: pass`, silently discarding any persistence error. A failed persist meant the caller would re-execute authorization on retry instead of replaying the stored conflict. |
| **Correction** | Wrapped persistence in an explicit `BEGIN EXCLUSIVE … COMMIT` with rollback on failure. Errors now return HTTP 503 with `error: idempotency_persistence_failed` instead of being swallowed. |
| **AI attribution** | Cannot be verified — no raw session log. |

### Repair 4 — Conflict Replay Test Coverage (Medium)

| Field | Value |
|-------|-------|
| **File** | `src/patch/test_concurrency.py` |
| **Defect** | No test verified that a conflict 409 result was replayed on retry with the same key+payload, or that persistence failure returned a non-swallowed error. |
| **Correction** | Added `TestConflictReplay` class with four focused tests: conflict replay (`replayed=True`), no new reservation rows on replay, key-conflict on changed payload, and 503 on persistence failure (monkey-patched). Five-simultaneous-request test preserved and still passes. |

### Repair 5 — Dispatcher Jitter Docstring and Cap Semantics (Low)

| Field | Value |
|-------|-------|
| **File / lines** | `src/engine/dispatcher.py` lines 100–106 (pre-repair) |
| **Defect** | (a) Docstring described bidirectional jitter `(1 ± …)` but implementation was additive-only `(expo + expo * jitter_fraction * rand)`. (b) The `cap` clamp was applied only to the exponential base (`expo = min(…, cap)`), so post-jitter totals could exceed `cap`. |
| **Correction** | (a) Docstring rewritten to accurately describe additive-only, non-negative jitter and that `cap` is the hard upper bound including jitter. (b) Return value changed to `min(expo + jitter, cap)`. |
| **Tests added** | `TestDispatcherCapBound` in `test_triage_dispatcher.py` — deterministic tests with injected `rand_fn` at max (0.9999, 1.0) and zero jitter. |
| **AI attribution** | Cannot be verified — no raw session log. |

---

## Raw Session Logs Status

`ai_session_logs/raw_logs/` contains:
- Five original Antigravity conversation exports in `.jsonl` format:
  - `SESSION_537b9856-48c9-45ab-8e01-b8997cc1fdb6.jsonl` (Session 1: initial Option C implementation)
  - `SESSION_5b5cd2a3-614d-401f-a918-fc67f5645c59.jsonl` (Session 2: independent audit)
  - `SESSION_3ceed4af-f324-4121-ae78-7328e0774866.jsonl` (Session 3: targeted repairs)
  - `SESSION_388ae5f2-eb60-4683-a3d0-9e9308c2a62e.jsonl` (Session 4: post-repair verification)
  - `SESSION_c0d007ff-9d86-4cfb-af02-184bd8d00a1c.jsonl` (Session 5: README test-count documentation correction)
- `SESSION_INDEX.csv` — technical manifest detailing conversation IDs, filenames, timestamps, file sizes, and SHA-256 checksums.
- `SESSION_TEMPLATE.md` — template for manually documenting any uncaptured sessions if needed.

See `ai_session_logs/AI_SESSION_LOG_INDEX.md` for the chronological sequence, time ranges, and content summary of each session.

**Completeness caveat:**
While these five exports capture key stages of the project (initial build, audit, repair, verification, and documentation correction), having these five exports does not prove that every conversation or prompt/response across the entire project lifecycle was captured or recovered. All claims should remain strictly bounded by what the evidence supports.

Entries 1 and 2 in the audit above have real, independently verifiable evidence (grep output, Python calculations, specific line numbers, and before/after test counts). They are documented to the full required format.

Entries 3–5 are condensed because verbatim prompt/response transcripts from the earliest initial build iterations were not captured at the time. The code defects and their corrections are real, but the prompts/responses are paraphrased from memory and cannot be verified as literal AI interactions.

---

## Sign-off Checklist

- [x] Entry 1 (WAN2 bandwidth cap) — real defect, real evidence, real fix, verified
- [x] Entry 2 (failover timing arithmetic) — real defect, real evidence, real fix, verified
- [x] Entries 3–5 — additional real defects from build session documented (condensed; raw logs not captured — see Raw Session Logs Status section above)
- [x] Raw session logs captured in `raw_logs/` — five original Antigravity JSONL exports preserved with `SESSION_INDEX.csv` manifest and `AI_SESSION_LOG_INDEX.md` (completeness caveat documented)
- [x] Repair session findings documented (2026-10-30) — VLAN mask, isolation_level comment, conflict-path idempotency, cap semantics; AI attribution cannot be verified for any item
- [x] All tests passing after repairs — `59 passed, 2 subtests passed in 0.32s` (executed 2026-10-30T04:21:48+07:00; +7 new tests added)
- [x] No fabricated test results, prompts, or AI outputs — all evidence above is from actual command execution
