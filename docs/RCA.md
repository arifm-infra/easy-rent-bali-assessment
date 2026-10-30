# Root Cause Analysis — Checkout Concurrency Failure

**Document type:** Engineering post-incident / design RCA  
**Revision:** 1.0  
**Date:** 2026-10-30  
**Scope:** Double-charge risk from network timeouts + blind retries; deadlock risk from unordered locking; safe authorization/capture ordering.

---

## 1. Executive Summary

A combination of (a) network timeout-triggered blind retries and (b) unsynchronized concurrent booking requests can cause:
- **Duplicate payment captures** for a single booking session.
- **Deadlocks** between competing transactions.
- **Payments taken before a vehicle reservation is confirmed** — the most serious business risk.

The `checkout_handler.py` patch resolves all three by introducing idempotency keys, atomic SQLite reservation checks, and an explicit authorize-then-reserve-then-capture ordering.

---

## 2. Root Cause 1 — Blind Retry After Network Timeout Causes Double Charge

### Scenario

```
Client ──► Server: POST /checkout  (payment + reservation)
         ... 28 seconds pass (network congestion) ...
Client ──► Server: POST /checkout  (RETRY — client sees timeout)
         ... Server-1 reply arrives late and is ignored ...
Server-1 processes: capture(card) → SUCCESS  ← charge 1
Server-2 processes: capture(card) → SUCCESS  ← charge 2 (DUPLICATE)
```

### Why it happens

1. Client sets a 30-second HTTP timeout.
2. The first request succeeds on the server but the response is lost in transit (or arrives after the client already timed out).
3. The client's retry logic re-sends an **identical** request body without an idempotency key.
4. The server treats each request as independent and executes payment capture twice.

### Consequences

- Customer charged twice for one booking.
- Reservation may exist for only one of the two captures, causing accounting inconsistency.
- PCI-DSS compliance breach (duplicate authorization).

### Fix

- **Require a client-generated idempotency key** in every checkout request.
- **Persist** the key, a hash of the request payload, and the outcome in a durable store **before** returning the response.
- On a replayed key + identical payload: return the stored result without re-executing side effects.
- On a replayed key + different payload: return `409 Conflict` immediately.
- The server must be **idempotent by default**, not idempotent as an opt-in.

---

## 3. Root Cause 2 — Inconsistent Lock Ordering Causes Deadlock

### Scenario

Two concurrent requests compete for vehicles A and B simultaneously:

```
Tx-1: LOCK vehicle_A → waiting for vehicle_B
Tx-2: LOCK vehicle_B → waiting for vehicle_A
       ↑ DEADLOCK
```

### Why it happens

When transactions acquire row-level or application-level locks in **different orders** depending on request content, a circular wait can form. Neither transaction can complete; both eventually time out, leaving the database in a clean state but the client with an error — and potentially a partially-applied side effect (e.g., payment authorized but not captured, or captured but reservation never written).

### Fix

- **Canonical lock ordering:** Always acquire vehicle/resource locks in a deterministic order (e.g., ascending `vehicle_id`). For single-vehicle bookings, a `SELECT … FOR UPDATE` (or SQLite's exclusive transaction) on the vehicle row serializes access naturally.
- **Timeout + rollback:** Set a short lock-wait timeout (e.g., 5 seconds); on timeout, roll back the entire transaction and return a retriable error to the caller.
- In SQLite (used in the demo), `BEGIN EXCLUSIVE` serializes writers; the WAL journal mode and `timeout` parameter handle concurrent access without application-level ordering.

---

## 4. Root Cause 3 — Payment Capture Before Confirmed Reservation

### Scenario

```
1. Capture payment  ← SUCCESS: card charged
2. Create reservation ← FAILS (vehicle gone, DB error, timeout)
3. No rollback of capture → customer charged, no car assigned
```

### Why it happens

An optimistic implementation executes payment capture first (to "secure" funds) before confirming that the vehicle is still available and the reservation record is durable. Any failure between steps 1 and 2 leaves the business holding funds with no corresponding booking.

### Fix — Safe Authorization/Capture Ordering

The correct sequence (implemented in `checkout_handler.py`):

```
1. AUTHORIZE payment (hold funds; do not settle)
   └─ On failure → return error; no charge taken; stop.

2. RESERVE vehicle atomically (CHECK + INSERT in one transaction)
   └─ On failure (vehicle taken, DB error) →
       VOID / CANCEL the authorization
       Return 409 Conflict or 5xx error; no charge taken.

3. CAPTURE payment (settle the authorized hold)
   └─ On failure → COMPENSATE:
       Mark reservation as PENDING_PAYMENT_FAILURE
       Trigger async retry or human review
       Do NOT silently drop the failure

4. CONFIRM reservation (status → CONFIRMED)
   Return 200 OK with booking reference.
```

**Key invariant:** A customer is never charged unless both authorization and reservation succeed. Capture happens **after** durable reservation write.

### Compensation on Reservation Failure

If the reservation write fails after a successful authorization:
1. Immediately void/reverse the payment authorization (before returning error to client).
2. Log a compensation event with `authorization_id`, `error_reason`, and timestamp.
3. If void also fails: escalate to a human-review queue with all context (double-entry accounting for reconciliation).

---

## 5. Summary of Fixes in `checkout_handler.py`

| Risk | Mitigation |
|------|-----------|
| Blind retry / double charge | Idempotency key + payload hash; stored result replay |
| Double reservation | `BEGIN EXCLUSIVE` + uniqueness constraint on active vehicle reservations |
| Lock-order deadlock | Single-vehicle checkout; SQLite exclusive lock serializes writers |
| Payment before reservation | Authorize → Reserve → Capture ordering; void on reserve failure |
| Lost compensation | Compensation logged; simulated payment void on failure path |

---

## 6. Limitations of the Demo Implementation

- SQLite provides **process-local** atomicity. In a multi-host production system, a distributed lock service (e.g., Redis SETNX, Postgres advisory locks, or a saga orchestrator) is required.
- Payment operations are **simulated**; no real payment gateway is contacted.
- The idempotency store is in the same SQLite database; in production it should be in a separate, high-durability store.
- `BEGIN EXCLUSIVE` in SQLite serializes all writers; this is acceptable for a local demo but would be a throughput bottleneck in production.
