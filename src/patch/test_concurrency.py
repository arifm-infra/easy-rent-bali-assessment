"""
test_concurrency.py — Concurrency and idempotency tests for CheckoutService.

Run:
    py -3 -m unittest src.patch.test_concurrency -v   (no pytest needed)
    # or:
    py -3 src/patch/test_concurrency.py

All tests use isolated temporary SQLite databases; no shared state between tests.
Tests must not rely on real time delays — the handler uses no sleeps.
"""

from __future__ import annotations

import sys
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Allow running from repo root or from src/patch/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.patch.checkout_handler import CheckoutService, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> tuple[CheckoutService, str]:
    """Return (service, db_path) backed by a fresh temp SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="erb_test_")
    os.close(fd)
    conn = init_db(path)
    return CheckoutService(conn), path


def _cleanup(svc: CheckoutService, db_path: str) -> None:
    """Close the connection then remove the temp file (required on Windows).

    Windows holds a file lock while the SQLite connection is open, so we must
    close the connection before calling os.unlink().
    """
    try:
        svc._conn.close()
    except Exception:
        pass
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _ref_payload() -> dict:
    return {
        "booking_id": "erb_kuta_9941",
        "vehicle_id": "car_avanza_04",
        "customer_id": "cust_foreign_881",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFiveSimultaneousRequests(unittest.TestCase):
    """
    Fire 5 simultaneous requests for the same vehicle, each with a distinct
    idempotency key and a distinct booking_id (competing bookings).
    Assert exactly ONE success (HTTP 200) and FOUR 409 Conflict responses.
    """

    def test_five_concurrent_exactly_one_success(self):
        svc, db_path = _make_service()
        vehicle_id = "car_avanza_04"
        customer_id = "cust_foreign_881"

        # Build 5 distinct payloads (different booking_ids) and distinct idempotency keys
        requests = [
            (
                str(uuid.uuid4()),           # unique idempotency key per request
                {
                    "booking_id": f"erb_kuta_{9941 + i}",
                    "vehicle_id": vehicle_id,
                    "customer_id": customer_id,
                },
            )
            for i in range(5)
        ]

        barrier = threading.Barrier(5)
        results = []
        lock = threading.Lock()

        def call(idem_key, payload):
            barrier.wait()  # all threads start simultaneously
            resp = svc.checkout(idem_key, payload)
            with lock:
                results.append(resp.status)

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(call, ik, p) for ik, p in requests]
            for f in as_completed(futures):
                f.result()  # re-raise any exception

        successes = results.count(200)
        conflicts = results.count(409)
        self.assertEqual(
            successes, 1,
            f"Expected exactly 1 success, got {successes}. Statuses: {results}",
        )
        self.assertEqual(
            conflicts, 4,
            f"Expected exactly 4 conflicts, got {conflicts}. Statuses: {results}",
        )

        _cleanup(svc, db_path)


class TestIdempotencyReplay(unittest.TestCase):
    """Same key + same payload must return stored result without re-executing side effects."""

    def test_same_key_same_payload_replays(self):
        svc, db_path = _make_service()
        idem_key = str(uuid.uuid4())
        payload = _ref_payload()

        r1 = svc.checkout(idem_key, payload)
        r2 = svc.checkout(idem_key, payload)
        r3 = svc.checkout(idem_key, payload)

        self.assertEqual(r1.status, 200)
        self.assertEqual(r2.status, 200)
        self.assertEqual(r3.status, 200)

        # Replayed responses must be flagged
        self.assertFalse(r1.body.get("replayed"), "First call must not be flagged replayed")
        self.assertTrue(r2.body.get("replayed"), "Second call must be flagged replayed")
        self.assertTrue(r3.body.get("replayed"), "Third call must be flagged replayed")

        # reservation_id must be identical across replays (no new reservation created)
        self.assertEqual(r1.body["reservation_id"], r2.body["reservation_id"])
        self.assertEqual(r1.body["reservation_id"], r3.body["reservation_id"])

        _cleanup(svc, db_path)

    def test_no_duplicate_side_effects_on_replay(self):
        """Replayed checkout must not create additional reservation rows."""
        import sqlite3 as _sqlite3
        svc, db_path = _make_service()
        idem_key = str(uuid.uuid4())
        payload = _ref_payload()

        svc.checkout(idem_key, payload)
        svc.checkout(idem_key, payload)
        svc.checkout(idem_key, payload)

        # Open a separate read connection to count rows before cleanup
        conn2 = _sqlite3.connect(db_path)
        count = conn2.execute(
            "SELECT COUNT(*) FROM reservations WHERE vehicle_id = ?",
            (payload["vehicle_id"],),
        ).fetchone()[0]
        conn2.close()

        self.assertEqual(count, 1, f"Expected 1 reservation row, found {count}")
        _cleanup(svc, db_path)


class TestKeyPayloadConflict(unittest.TestCase):
    """Same key + different payload must return 409 Conflict immediately."""

    def test_changed_payload_same_key_returns_409(self):
        svc, db_path = _make_service()
        idem_key = str(uuid.uuid4())
        payload_a = _ref_payload()
        payload_b = dict(_ref_payload())
        payload_b["customer_id"] = "cust_other_999"

        r1 = svc.checkout(idem_key, payload_a)
        r2 = svc.checkout(idem_key, payload_b)

        self.assertEqual(r1.status, 200)
        self.assertEqual(r2.status, 409)
        self.assertEqual(r2.body["error"], "idempotency_key_conflict")

        _cleanup(svc, db_path)


class TestVehicleAlreadyReserved(unittest.TestCase):
    """A second distinct booking for the same vehicle must get 409."""

    def test_competing_booking_409(self):
        svc, db_path = _make_service()
        vehicle_id = "car_avanza_04"

        r1 = svc.checkout(
            str(uuid.uuid4()),
            {"booking_id": "erb_001", "vehicle_id": vehicle_id, "customer_id": "cust_a"},
        )
        r2 = svc.checkout(
            str(uuid.uuid4()),
            {"booking_id": "erb_002", "vehicle_id": vehicle_id, "customer_id": "cust_b"},
        )

        self.assertEqual(r1.status, 200)
        self.assertEqual(r2.status, 409)
        self.assertIn("already_reserved", r2.body.get("error", ""))

        _cleanup(svc, db_path)


class TestConflictReplay(unittest.TestCase):
    """
    Regression tests for conflict-path idempotency persistence (audit finding #3).

    Scenario:
      - booking_a succeeds (HTTP 200) and reserves the vehicle.
      - booking_b uses a NEW idempotency key and receives 409 Conflict because
        the vehicle is already reserved.
      - Repeating booking_b's exact request (same key + same payload) must replay
        the stored 409 — not re-run authorization.
      - Repeating booking_b with the same key but a CHANGED payload must return
        409 idempotency_key_conflict immediately.
    """

    def test_conflict_result_is_replayed(self):
        """Second call with same conflict key+payload returns replayed=True and 409."""
        svc, db_path = _make_service()
        vehicle_id = "car_vios_conflict_01"

        # Step 1: book the vehicle successfully
        key_a = str(uuid.uuid4())
        payload_a = {
            "booking_id": "erb_conflict_a",
            "vehicle_id": vehicle_id,
            "customer_id": "cust_first",
        }
        r_a = svc.checkout(key_a, payload_a)
        self.assertEqual(r_a.status, 200, f"First booking must succeed; got {r_a.body}")

        # Step 2: competing booking gets 409
        key_b = str(uuid.uuid4())
        payload_b = {
            "booking_id": "erb_conflict_b",
            "vehicle_id": vehicle_id,
            "customer_id": "cust_second",
        }
        r_b1 = svc.checkout(key_b, payload_b)
        self.assertEqual(r_b1.status, 409, f"Competing booking must be 409; got {r_b1.status}")
        self.assertIn("already_reserved", r_b1.body.get("error", ""))

        # Step 3: exact replay — same key_b + same payload_b
        r_b2 = svc.checkout(key_b, payload_b)
        self.assertEqual(r_b2.status, 409, f"Replayed conflict must still return 409; got {r_b2.status}")
        self.assertTrue(
            r_b2.body.get("replayed"),
            f"Replayed conflict response must include replayed=True; body={r_b2.body}",
        )

        _cleanup(svc, db_path)

    def test_conflict_replay_does_not_repeat_authorization(self):
        """
        Replaying a conflict must not create new reservation rows or new auth IDs.
        The auth for the conflict attempt is voided, and no additional rows appear
        on replay.
        """
        import sqlite3 as _sqlite3

        svc, db_path = _make_service()
        vehicle_id = "car_vios_conflict_02"

        # First booking succeeds
        svc.checkout(
            str(uuid.uuid4()),
            {"booking_id": "erb_ca_first", "vehicle_id": vehicle_id, "customer_id": "cust_x"},
        )

        # Competing booking is stored as a conflict
        key_b = str(uuid.uuid4())
        payload_b = {"booking_id": "erb_ca_second", "vehicle_id": vehicle_id, "customer_id": "cust_y"}
        r_b1 = svc.checkout(key_b, payload_b)
        self.assertEqual(r_b1.status, 409)

        # Count reservations before replay
        conn2 = _sqlite3.connect(db_path)
        count_before = conn2.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]

        # Replay the conflict
        r_b2 = svc.checkout(key_b, payload_b)
        self.assertEqual(r_b2.status, 409)
        self.assertTrue(r_b2.body.get("replayed"))

        # No new reservation rows after replay
        count_after = conn2.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        conn2.close()

        self.assertEqual(
            count_before,
            count_after,
            f"Replay must not create reservation rows; before={count_before} after={count_after}",
        )

        _cleanup(svc, db_path)

    def test_conflict_key_with_different_payload_returns_key_conflict_409(self):
        """Same idempotency key used after a conflict with a CHANGED payload → key-conflict 409."""
        svc, db_path = _make_service()
        vehicle_id = "car_vios_conflict_03"

        # Book the vehicle
        svc.checkout(
            str(uuid.uuid4()),
            {"booking_id": "erb_cc_first", "vehicle_id": vehicle_id, "customer_id": "cust_p"},
        )

        # Competing booking → 409 vehicle_already_reserved
        key_b = str(uuid.uuid4())
        payload_b = {"booking_id": "erb_cc_second", "vehicle_id": vehicle_id, "customer_id": "cust_q"}
        r_b1 = svc.checkout(key_b, payload_b)
        self.assertEqual(r_b1.status, 409)

        # Same key, different payload → idempotency_key_conflict
        payload_b_changed = dict(payload_b)
        payload_b_changed["customer_id"] = "cust_r_tampered"
        r_b2 = svc.checkout(key_b, payload_b_changed)
        self.assertEqual(r_b2.status, 409)
        self.assertEqual(
            r_b2.body.get("error"),
            "idempotency_key_conflict",
            f"Changed payload must return idempotency_key_conflict; got {r_b2.body}",
        )

        _cleanup(svc, db_path)

    def test_conflict_persistence_failure_returns_503(self):
        """
        If the idempotency persistence transaction fails, the handler must return
        503 rather than silently swallowing the error.

        We simulate persistence failure by wrapping the CheckoutService so that
        the second BEGIN EXCLUSIVE call (the conflict-persistence transaction)
        raises.  sqlite3.Connection.execute is read-only in Python 3.13+, so
        we subclass CheckoutService and override _checkout_locked to inject a
        failing connection wrapper only for the persistence step.
        """
        import sqlite3 as _sqlite3

        svc, db_path = _make_service()
        vehicle_id = "car_vios_conflict_04"

        # First booking succeeds through the normal service
        svc.checkout(
            str(uuid.uuid4()),
            {"booking_id": "erb_pf_first", "vehicle_id": vehicle_id, "customer_id": "cust_m"},
        )

        # Wrap the connection in a proxy whose execute() intercepts BEGIN EXCLUSIVE
        # and raises on the 2nd call (the conflict-persistence transaction).
        class _FailingConnProxy:
            """Thin proxy that forwards all attribute access to the real connection
            except execute(), where it injects a failure on the 2nd BEGIN EXCLUSIVE."""

            def __init__(self, real_conn):
                self._real = real_conn
                self._begin_count = 0

            def execute(self, sql, *args, **kwargs):
                if "BEGIN EXCLUSIVE" in sql.upper():
                    self._begin_count += 1
                    if self._begin_count >= 2:
                        raise _sqlite3.OperationalError("Simulated persistence failure")
                return self._real.execute(sql, *args, **kwargs)

            def executescript(self, sql):
                return self._real.executescript(sql)

            def __getattr__(self, name):
                return getattr(self._real, name)

        # Replace the service's connection with the proxy
        original_conn = svc._conn
        proxy = _FailingConnProxy(original_conn)
        svc._conn = proxy  # type: ignore[assignment]

        key_b = str(uuid.uuid4())
        payload_b = {"booking_id": "erb_pf_second", "vehicle_id": vehicle_id, "customer_id": "cust_n"}
        r_b = svc.checkout(key_b, payload_b)

        # Restore so cleanup can proceed
        svc._conn = original_conn

        self.assertEqual(
            r_b.status,
            503,
            f"Persistence failure must return 503, got {r_b.status}; body={r_b.body}",
        )
        self.assertEqual(r_b.body.get("error"), "idempotency_persistence_failed")

        _cleanup(svc, db_path)


class TestMissingFields(unittest.TestCase):
    """Requests missing required fields must return 400."""

    def test_missing_vehicle_id(self):
        svc, db_path = _make_service()
        r = svc.checkout(str(uuid.uuid4()), {"booking_id": "x", "customer_id": "y"})
        self.assertEqual(r.status, 400)
        self.assertIn("vehicle_id", r.body.get("missing", []))
        _cleanup(svc, db_path)

    def test_empty_payload(self):
        svc, db_path = _make_service()
        r = svc.checkout(str(uuid.uuid4()), {})
        self.assertEqual(r.status, 400)
        _cleanup(svc, db_path)


class TestReferencePayload(unittest.TestCase):
    """Smoke test with the exact reference payload from the assessment."""

    def test_reference_payload_succeeds(self):
        svc, db_path = _make_service()
        r = svc.checkout(
            str(uuid.uuid4()),
            {
                "booking_id": "erb_kuta_9941",
                "vehicle_id": "car_avanza_04",
                "customer_id": "cust_foreign_881",
            },
        )
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["booking_id"], "erb_kuta_9941")
        self.assertEqual(r.body["vehicle_id"], "car_avanza_04")
        self.assertEqual(r.body["result"], "confirmed")
        _cleanup(svc, db_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
