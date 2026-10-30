# Easy Rent Bali — Option C: Incident Response, Network Failover & Fleet Triage

**Assessment:** Senior Network / Backend / IoT Engineer Technical Assessment  
**Option:** C — Incident Response, Network Failover & Fleet Triage  
**Language:** Python 3.11+ (standard library only)  
**Date:** 2026-10-30

---

## Assessment Scenario

Easy Rent Bali operates a vehicle rental fleet across multiple Bali locations. This assessment simulates a connectivity incident at the Kuta Hub site and requires responses across three distinct engineering areas:

1. **Network Architecture and WAN Failover** — Design a resilient network topology with dual-WAN failover, VLAN segmentation, QoS, and access control for the Kuta Hub–Renon HQ–Cloud Management site chain.
2. **Checkout Concurrency and Idempotency** — Implement and test an atomic vehicle checkout service that prevents double-booking under concurrent load and handles payment idempotency safely.
3. **Fleet Telemetry Triage and Alert Dispatch** — Process IoT telemetry from 8 rental vehicles, apply incident classification rules, and simulate structured alert dispatch with retry backoff.

Each component is documented in its own section below.

---

## Prerequisites

| Requirement | Detail |
|------------|--------|
| Python | 3.11 or newer (`py -3 --version` or `python3 --version`) |
| OS | Windows, macOS, or Linux |
| Third-party packages | **None required** — pure standard library |
| pytest (optional) | `pip install pytest` — only needed if you prefer the pytest runner |

> **SQLite note:** SQLite is bundled with Python. No database server setup is needed.  
> The `BEGIN EXCLUSIVE` transaction pattern used here serialises writers within a single Python process.  
> This is a **local atomic demo**, not a production multi-host distributed-lock service.  
> Production deployments require a distributed lock (Redis SETNX, Postgres advisory locks, or a saga orchestrator).

---

## Repository Map

```
asy-rent-bali-assessment/
├── docs/
│   ├── NETWORK_ARCHITECTURE.md   # Part 1: Topology, VLANs, dual-WAN, QoS, ACL, VPN
│   └── RCA.md                    # Part 2: Root cause analysis — concurrency & payment risks
├── data/
│   └── outage_telemetry.json     # Eight-vehicle IoT telemetry dataset
├── src/
│   ├── __init__.py
│   ├── patch/
│   │   ├── __init__.py
│   │   ├── checkout_handler.py   # Atomic checkout service with idempotency
│   │   └── test_concurrency.py   # Concurrency & idempotency tests (12 tests)
│   └── engine/
│       ├── __init__.py
│       ├── triage_engine.py      # Fleet triage rules + validation
│       ├── dispatcher.py         # Alert dispatcher + reconciliation output generator
│       └── test_triage_dispatcher.py  # Triage rule + dispatcher unit tests (47 tests)
├── output/
│   └── incident_reconciliation.json  # Generated fleet triage output
├── ai_session_logs/
│   ├── AI_SESSION_LOG_INDEX.md   # Index of recovered Antigravity session exports
│   ├── STEERING_AUDIT.md         # Defect log, corrective actions, and AI steering audit
│   └── raw_logs/
│       ├── SESSION_537b9856-48c9-45ab-8e01-b8997cc1fdb6.jsonl  # Session 1: initial implementation
│       ├── SESSION_5b5cd2a3-614d-401f-a918-fc67f5645c59.jsonl  # Session 2: independent audit
│       ├── SESSION_3ceed4af-f324-4121-ae78-7328e0774866.jsonl  # Session 3: targeted repairs
│       ├── SESSION_388ae5f2-eb60-4683-a3d0-9e9308c2a62e.jsonl  # Session 4: post-repair verification
│       ├── SESSION_c0d007ff-9d86-4cfb-af02-184bd8d00a1c.jsonl  # Session 5: README corrections
│       ├── SESSION_INDEX.csv     # Machine-readable manifest with SHA-256 hashes
│       └── SESSION_TEMPLATE.md   # Template for manually documenting uncaptured sessions
├── reference/
│   └── Technical Assessment - Easy Rent Bali.pdf  # Original assessment specification
├── README.md
└── requirements.txt              # No mandatory third-party dependencies
```

---

## Running the Tests

All commands are run from the repository root.

### Concurrency & Idempotency Tests (Part 2)

```powershell
# Windows (py launcher)
py -3 -m unittest src.patch.test_concurrency -v

# macOS / Linux
python3 -m unittest src.patch.test_concurrency -v
```

**Expected output:** `Ran 12 tests in ~0.1s   OK`

Tests covered:

| Test class | Tests |
|---|---|
| `TestFiveSimultaneousRequests` | `test_five_concurrent_exactly_one_success` — 5 simultaneous bookings for same vehicle; assert 1×200, 4×409 |
| `TestIdempotencyReplay` | `test_same_key_same_payload_replays` — idempotency replay with `replayed=True` flag |
| `TestIdempotencyReplay` | `test_no_duplicate_side_effects_on_replay` — only 1 reservation row created across 3 replays |
| `TestKeyPayloadConflict` | `test_changed_payload_same_key_returns_409` — key reuse with different payload → 409 |
| `TestVehicleAlreadyReserved` | `test_competing_booking_409` — sequential competing bookings → second gets 409 |
| `TestConflictReplay` | `test_conflict_result_is_replayed` — conflict 409 replayed correctly on retry |
| `TestConflictReplay` | `test_conflict_replay_does_not_repeat_authorization` — replay creates no new rows |
| `TestConflictReplay` | `test_conflict_key_with_different_payload_returns_key_conflict_409` — tampered payload → key-conflict 409 |
| `TestConflictReplay` | `test_conflict_persistence_failure_returns_503` — failed idempotency persist → 503 (not swallowed) |
| `TestMissingFields` | `test_missing_vehicle_id` — missing field → 400 |
| `TestMissingFields` | `test_empty_payload` — empty payload → 400 |
| `TestReferencePayload` | `test_reference_payload_succeeds` — reference payload from assessment spec → 200 |

### Triage Engine & Dispatcher Tests (Part 3)

```powershell
py -3 -m unittest src.engine.test_triage_dispatcher -v

# or
python3 -m unittest src.engine.test_triage_dispatcher -v
```

**Expected output:** `Ran 47 tests in ~0.01s   OK`

Tests covered:

| Test class | Coverage area |
|---|---|
| `TestCriticalSecurityAlert` (7) | available+moving, restricted zone, case-insensitivity, stationary/rented non-triggers |
| `TestOfflineTelemetryDrop` (6) | strict `>900` boundary, active status trigger, available vehicles excluded |
| `TestMaintenanceRequired` (7) | exact thresholds (11.5 V, 15%), both conditions combined, boundary non-triggers |
| `TestMultipleTags` (2) | all 3 tags simultaneously, clear vehicle has no tags |
| `TestValidation` (5) | missing fields, wrong types, non-dict record, non-list fleet |
| `TestReferenceDataset` (9) | all 8 reference vehicles verified individually + all-eight-records present |
| `TestDispatcherBackoff` (6) | immediate success, retry-then-succeed, all retries exhausted, exponential delays, sender exception, no real sleeps |
| `TestDispatcherCapBound` (3) | delay cap enforced at max jitter (0.9999), exact 1.0 jitter, zero jitter |
| `TestFleetDispatch` (2) | vehicles without incidents included, reconciliation structure |

### Run All Tests Together

```powershell
py -3 -m unittest src.patch.test_concurrency src.engine.test_triage_dispatcher -v

# or with discover (from repo root):
py -3 -m unittest discover -s . -p "test_*.py" -v

# or with pytest if installed:
py -3 -m pytest src/ -v
```

**Expected total:** `Ran 59 tests — OK` (12 concurrency + 47 triage/dispatcher)

---

## Generating the Reconciliation Output

```powershell
py -3 -m src.engine.dispatcher

# or
python3 -m src.engine.dispatcher
```

This reads `data/outage_telemetry.json`, runs triage, dispatches (simulated) alerts, and writes `output/incident_reconciliation.json`.

**Console output (verified):**
```
Loading telemetry from ...\data\outage_telemetry.json …
Dispatching alerts …
Reconciliation written to ...\output\incident_reconciliation.json
  Vehicles total:         8
  Vehicles with incidents:6
  Vehicles clear:         2
  Tag counts:             {'CRITICAL_SECURITY_ALERT': 2, 'MAINTENANCE_REQUIRED': 3, 'OFFLINE_TELEMETRY_DROP': 1}
```

> **Note:** All alert delivery is simulated. The reconciliation JSON contains `"simulated": true` on every alert payload and a note confirming no real notification was sent.

---

## Expected Key Results

### Concurrency Test

| Metric | Expected |
|--------|---------:|
| Total requests | 5 (simultaneous, same vehicle) |
| Successful checkouts (HTTP 200) | **1** |
| Conflict responses (HTTP 409) | **4** |
| Duplicate reservations in DB | 0 |
| Duplicate payment captures | 0 |

### Triage Summary (8-Vehicle Fleet)

| Vehicle | Plate | Status | Tags |
|---------|-------|--------|------|
| iot_car_01 | DK 1234 AB | rented | *(none — CLEAR)* |
| iot_car_02 | DK 5678 CD | available | `CRITICAL_SECURITY_ALERT` (moving 52 km/h while available) |
| iot_car_03 | DK 9012 EF | rented | `MAINTENANCE_REQUIRED` (battery 11.2 V < 11.5 V) |
| iot_car_04 | DK 3456 GH | rented | `MAINTENANCE_REQUIRED` (fuel 9% < 15%) |
| iot_car_05 | DK 7890 IJ | rented | `CRITICAL_SECURITY_ALERT` (in RESTRICTED_OFFROAD_BATUKARU) |
| iot_car_06 | DK 2345 KL | rented | `OFFLINE_TELEMETRY_DROP` (1320 s > 900 s threshold) |
| iot_car_07 | DK 6789 MN | available | *(none — CLEAR)* |
| iot_car_08 | DK 4567 OP | available | `MAINTENANCE_REQUIRED` (battery 11.3 V < 11.5 V; fuel 11% < 15%) |

**Summary:** 6 vehicles with incidents, 2 clear.  
Tag counts: `CRITICAL_SECURITY_ALERT ×2`, `MAINTENANCE_REQUIRED ×3`, `OFFLINE_TELEMETRY_DROP ×1`

---

## Part Summaries

### Part 1 — Network Architecture (`docs/NETWORK_ARCHITECTURE.md`)

This is a **technical design document** — it describes a designed network architecture and is not a deployed or tested physical configuration. No routers, switches, firewalls, or WAN links were physically provisioned or tested.

- ASCII topology: Kuta Hub → HQ Renon → Cloud Management with WAN1 (fiber) + WAN2 (LTE)
- Five VLANs (10/20/30/40/50) with subnets, gateways, DHCP pools, QoS, and purpose
- Default-deny inter-VLAN policy matrix with only justified explicit flows
- VLAN 50 subnet notation issue documented (`172.16.50.0/22` → canonical `172.16.48.0/22`)
- Dual-WAN health-check logic: **2-ping failure threshold** (2 s detection at 1 s interval), 5-ping recovery hysteresis, < 3 s failover design target — see timing rationale in `NETWORK_ARCHITECTURE.md §4.1`
- WAN2 policy: guest VLAN 50 hard-capped at **≤ 120 kbps** (strictly below the 128 kbps assessment limit); POS and IoT prioritised
- Site-to-site VPN via WireGuard/IPSec IKEv2

### Part 2 — Checkout Concurrency & RCA

- `docs/RCA.md`: explains blind retry → double charge; lock ordering → deadlock; capture-before-reserve risk; safe Authorize→Reserve→Capture ordering with void on failure
- `src/patch/checkout_handler.py`: SQLite-backed, thread-safe checkout; idempotency key + payload hash; exclusive transaction; **payment processing is fully simulated** — no real payment provider is contacted
- `src/patch/test_concurrency.py`: **12 tests** across 7 test classes; includes 5-concurrent-request test (confirms exactly 1 success + 4 conflicts), idempotency replay, conflict-path persistence, and field validation

### Part 3 — Fleet Triage & Dispatcher

- `data/outage_telemetry.json`: 8-vehicle dataset as specified
- `src/engine/triage_engine.py`: validates records; applies 3 incident tags with strict thresholds; case-insensitive restricted zone detection
- `src/engine/dispatcher.py`: structured alert payloads; bounded exponential backoff + jitter; injectable sender/sleeper/rand for test isolation; generates `output/incident_reconciliation.json`; **all alert delivery is simulated — no real notifications are sent**
- `src/engine/test_triage_dispatcher.py`: **47 tests** across 9 test classes covering all rules, boundaries, multiple tags, validation, retry logic, jitter cap, and fleet dispatch structure

---

## Design Assumptions

1. **SQLite as demo store:** `BEGIN EXCLUSIVE` serialises all writers within a single Python process. In production, use a distributed lock or a database with row-level locking (Postgres, MySQL InnoDB).
2. **Idempotency keys:** Client-generated UUID recommended. The service stores key + payload hash + result atomically inside the same transaction as the reservation.
3. **Payment simulation:** `_simulate_authorize`, `_simulate_capture`, `_simulate_void` are stubs. No real payment provider is contacted at any point.
4. **Triage thresholds:** Strict comparisons (`> 900`, `< 11.5`, `< 15`) — values exactly at the boundary do not trigger alerts.
5. **Restricted zone detection:** Any zone whose uppercased name contains the substring `"RESTRICTED"` triggers `CRITICAL_SECURITY_ALERT`. `RESTRICTED_OFFROAD_BATUKARU` is the documented test case.
6. **Available vehicles and offline detection:** Silent available vehicles are expected; `OFFLINE_TELEMETRY_DROP` only applies to `rented` or `active` status.
7. **Dispatcher mock sender:** The default sender always returns `True` (success). Tests inject failing senders to verify backoff/retry logic. No real network calls are made.
8. **VLAN 50 subnet:** The assessment's `172.16.50.0/22` is not a canonical /22 address. The implementation documents the correction to `172.16.48.0/22` with a preserved note in `docs/NETWORK_ARCHITECTURE.md`.
9. **Dual-WAN failover timings:** Failure threshold is **2 consecutive missed pings** at a 1-second interval (2 s detection window), which allows < 1 s for routing convergence within the **< 3-second design target**. A 3-miss threshold at 1-second intervals would exhaust the budget on detection alone. All timing values are **design targets** requiring hardware verification, not measured results.
10. **Windows file cleanup:** SQLite connections must be closed before `os.unlink()` on Windows. Tests use `_cleanup(svc, db_path)` which closes the connection first.

---

## Limitations

- **No HTTP server:** The checkout handler exposes a service class, not an HTTP endpoint. Wrap with FastAPI or Flask for a real web service.
- **No distributed locking:** SQLite `BEGIN EXCLUSIVE` does not scale across multiple processes or hosts.
- **No real alerts:** All notification delivery is simulated. The reconciliation JSON explicitly documents this.
- **No real payment processing:** Payment authorization, capture, and void are fully simulated stubs.
- **No auth/TLS:** The checkout service has no authentication layer; production deployments must add it.
- **Idempotency store durability:** The idempotency store is in the same SQLite file as reservations. In production, use a separate high-durability store (e.g., Redis with AOF persistence).
- **No saga rollback:** Compensation on payment capture failure is logged but not retried asynchronously; production systems need a saga orchestrator or outbox pattern.
- **Network design only:** `docs/NETWORK_ARCHITECTURE.md` is a design document. No physical network infrastructure was deployed or tested.

---

## AI Session Logs and Steering Audit

The `ai_session_logs/` directory contains the recovered Antigravity conversation exports and the steering audit.

### Recovered Session Exports

Five Antigravity conversations have been exported as JSONL files and are preserved in `ai_session_logs/raw_logs/`. These are the original exported transcripts — do not modify or reconstruct them.

| Session | File | Period (UTC) | Stage |
|---------|------|-------------|-------|
| 1 | `SESSION_537b9856….jsonl` | 2026-10-29 20:37–21:01 | Initial implementation |
| 2 | `SESSION_5b5cd2a3….jsonl` | 2026-10-29 21:05–21:13 | Independent audit |
| 3 | `SESSION_3ceed4af….jsonl` | 2026-10-29 21:17–21:23 | Targeted repairs |
| 4 | `SESSION_388ae5f2….jsonl` | 2026-10-29 21:28–21:31 | Post-repair verification |
| 5 | `SESSION_c0d007ff….jsonl` | 2026-10-29 21:33–21:33 | README corrections |

See `ai_session_logs/AI_SESSION_LOG_INDEX.md` for the full session index with ordering context.  
See `ai_session_logs/raw_logs/SESSION_INDEX.csv` for SHA-256 hashes of each exported file.

> **Completeness note:** These five exports are the sessions that have been recovered. It has not been proven that every project conversation in Antigravity was captured. Do not claim completeness beyond what the evidence supports.

### SESSION_TEMPLATE.md

`ai_session_logs/raw_logs/SESSION_TEMPLATE.md` is provided for documenting any sessions that were **not** captured as automatic exports. Use it only when no JSONL export exists for a conversation. Do not recreate already-exported sessions as template files.

### Steering Audit

`ai_session_logs/STEERING_AUDIT.md` documents:

- Real defects found during review and testing, with evidence (failing output, line numbers, before/after states).
- Corrective prompts and verified resolutions.
- Honest attribution — where raw session logs are unavailable, prompts are noted as paraphrased or unverifiable.

**Do not fabricate** AI prompts, responses, defect reports, or test results. The audit is evaluated on honesty. Empty or partial entries are preferable to invented content.
