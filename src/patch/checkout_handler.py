"""
checkout_handler.py — Atomic vehicle checkout with idempotency.

Design assumptions (documented in docs/RCA.md):
- SQLite is used for a local atomic demo; it serialises writers via BEGIN EXCLUSIVE.
  In production, a distributed lock service (Redis SETNX, Postgres advisory locks,
  or a saga orchestrator) is required for multi-host deployments.
- Payment operations are fully simulated; no real payment provider is contacted.
- Idempotency keys are per-request client tokens (UUID recommended).
  Same key + same payload  → replay stored result, no side effects.
  Same key + diff payload  → 409 Conflict immediately.
- Only one CONFIRMED reservation may own a vehicle at any time.
  Competing bookings for an already-reserved vehicle → 409 Conflict.
- Safe ordering: Authorize → Reserve → Capture (void auth on reserve failure).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

@dataclass
class CheckoutResponse:
    status: int          # HTTP-style status code
    body: dict[str, Any]


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id   TEXT PRIMARY KEY,
    vehicle_id       TEXT NOT NULL,
    customer_id      TEXT NOT NULL,
    booking_id       TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL DEFAULT 'CONFIRMED',
    payment_auth_id  TEXT,
    payment_status   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Partial unique index: only one CONFIRMED reservation per vehicle
CREATE UNIQUE INDEX IF NOT EXISTS uix_vehicle_confirmed
    ON reservations (vehicle_id)
    WHERE status = 'CONFIRMED';

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key  TEXT PRIMARY KEY,
    payload_hash     TEXT NOT NULL,
    response_status  INTEGER NOT NULL,
    response_body    TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply schema."""
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=10,
        isolation_level=None,   # autocommit ON; transactions are managed explicitly via BEGIN EXCLUSIVE / COMMIT / ROLLBACK
    )
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Payment simulation
# ---------------------------------------------------------------------------

def _simulate_authorize(customer_id: str, amount_idr: int = 500_000) -> dict:
    """Simulate payment authorization (no real provider contacted)."""
    return {
        "auth_id": f"AUTH_{uuid.uuid4().hex[:12].upper()}",
        "customer_id": customer_id,
        "amount_idr": amount_idr,
        "simulated": True,
        "result": "authorized",
    }


def _simulate_capture(auth_id: str) -> dict:
    """Simulate payment capture."""
    return {"auth_id": auth_id, "simulated": True, "result": "captured"}


def _simulate_void(auth_id: str) -> dict:
    """Simulate authorization void (used when reservation fails)."""
    return {"auth_id": auth_id, "simulated": True, "result": "voided"}


# ---------------------------------------------------------------------------
# Payload hash
# ---------------------------------------------------------------------------

def _payload_hash(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Checkout service
# ---------------------------------------------------------------------------

class CheckoutService:
    """
    Thread-safe checkout service backed by a shared SQLite connection.

    Thread safety: SQLite WAL + BEGIN EXCLUSIVE serialises concurrent writers.
    A threading.Lock guards the connection reference itself so that concurrent
    callers share one connection object without race conditions on Python-level
    attributes.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def checkout(self, idempotency_key: str, payload: dict) -> CheckoutResponse:
        """
        Execute a checkout or replay a previous result.

        payload must contain: booking_id, vehicle_id, customer_id
        """
        # Validate required fields
        missing = [k for k in ("booking_id", "vehicle_id", "customer_id") if k not in payload]
        if missing:
            return CheckoutResponse(
                status=400,
                body={"error": "missing_fields", "missing": missing},
            )

        p_hash = _payload_hash(payload)

        with self._lock:
            return self._checkout_locked(idempotency_key, payload, p_hash)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _checkout_locked(
        self, idempotency_key: str, payload: dict, p_hash: str
    ) -> CheckoutResponse:
        conn = self._conn

        # ── 1. Idempotency check ──────────────────────────────────────
        row = conn.execute(
            "SELECT payload_hash, response_status, response_body "
            "FROM idempotency_keys WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()

        if row is not None:
            stored_hash, stored_status, stored_body_json = row
            if stored_hash != p_hash:
                return CheckoutResponse(
                    status=409,
                    body={
                        "error": "idempotency_key_conflict",
                        "detail": "Same idempotency key used with a different payload.",
                    },
                )
            # Same key + same payload → replay
            return CheckoutResponse(
                status=stored_status,
                body=json.loads(stored_body_json) | {"replayed": True},
            )

        # ── 2. Authorize payment (before touching reservations) ───────
        auth = _simulate_authorize(payload["customer_id"])

        # ── 3. Reserve vehicle atomically ─────────────────────────────
        reservation_id = f"res_{uuid.uuid4().hex[:16]}"
        try:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.execute(
                    """
                    INSERT INTO reservations
                        (reservation_id, vehicle_id, customer_id, booking_id,
                         status, payment_auth_id, payment_status)
                    VALUES (?, ?, ?, ?, 'CONFIRMED', ?, 'authorized')
                    """,
                    (
                        reservation_id,
                        payload["vehicle_id"],
                        payload["customer_id"],
                        payload["booking_id"],
                        auth["auth_id"],
                    ),
                )
                # ── 4. Capture payment (only after durable reservation write) ──
                capture = _simulate_capture(auth["auth_id"])

                conn.execute(
                    "UPDATE reservations SET payment_status = 'captured' "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                )

                response = CheckoutResponse(
                    status=200,
                    body={
                        "result": "confirmed",
                        "reservation_id": reservation_id,
                        "booking_id": payload["booking_id"],
                        "vehicle_id": payload["vehicle_id"],
                        "customer_id": payload["customer_id"],
                        "payment_auth_id": auth["auth_id"],
                        "payment_capture": capture,
                        "replayed": False,
                    },
                )

                # ── 5. Persist idempotency record inside same transaction ─
                conn.execute(
                    "INSERT INTO idempotency_keys "
                    "(idempotency_key, payload_hash, response_status, response_body) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        idempotency_key,
                        p_hash,
                        response.status,
                        json.dumps(response.body),
                    ),
                )

                conn.execute("COMMIT")
                return response

            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                # Void the authorization since reservation failed
                _simulate_void(auth["auth_id"])
                detail = str(exc)
                if "uix_vehicle_confirmed" in detail or "UNIQUE constraint" in detail:
                    conflict_body = {
                        "error": "vehicle_already_reserved",
                        "detail": f"Vehicle {payload['vehicle_id']} already has an active reservation.",
                        "payment_auth_voided": auth["auth_id"],
                    }
                else:
                    conflict_body = {
                        "error": "reservation_conflict",
                        "detail": detail,
                        "payment_auth_voided": auth["auth_id"],
                    }
                conflict_resp = CheckoutResponse(status=409, body=conflict_body)
                # Persist the conflict idempotency record in an explicit transaction
                # so that a retry with the same key and payload replays the stored 409
                # rather than re-running authorization.  Errors are reported, not
                # silently swallowed.
                try:
                    conn.execute("BEGIN EXCLUSIVE")
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO idempotency_keys "
                            "(idempotency_key, payload_hash, response_status, response_body) "
                            "VALUES (?, ?, ?, ?)",
                            (idempotency_key, p_hash, 409, json.dumps(conflict_body)),
                        )
                        conn.execute("COMMIT")
                    except Exception:
                        conn.execute("ROLLBACK")
                        raise
                except Exception as persist_exc:
                    # Persistence failed: return a 503 so the caller knows the
                    # conflict result was NOT stored and must not be assumed durable.
                    return CheckoutResponse(
                        status=503,
                        body={
                            "error": "idempotency_persistence_failed",
                            "detail": (
                                "Vehicle conflict detected and auth voided, but the "
                                "idempotency record could not be persisted. "
                                "Retry may re-run authorization."
                            ),
                            "persistence_error": str(persist_exc),
                            "payment_auth_voided": auth["auth_id"],
                        },
                    )
                return conflict_resp

            except Exception:
                conn.execute("ROLLBACK")
                raise

        except CheckoutResponse:  # re-raise if we returned inside try block somehow
            raise
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc).lower():
                return CheckoutResponse(
                    status=503,
                    body={"error": "database_locked", "detail": str(exc)},
                )
            raise
