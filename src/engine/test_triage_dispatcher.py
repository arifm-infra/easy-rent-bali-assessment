"""
test_triage_dispatcher.py — Unit tests for triage_engine and dispatcher.

Run:
    python -m pytest src/engine/test_triage_dispatcher.py -v
    # or directly:
    python src/engine/test_triage_dispatcher.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.engine.triage_engine import (
    OFFLINE_PING_THRESHOLD_SECONDS,
    BATTERY_LOW_THRESHOLD_V,
    FUEL_LOW_THRESHOLD_PCT,
    triage_vehicle,
    triage_fleet,
    load_and_triage,
)
from src.engine.dispatcher import (
    DispatchConfig,
    build_alert_payload,
    dispatch_alert,
    dispatch_fleet,
    build_reconciliation,
    _compute_delay,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(**overrides) -> dict:
    """Return a valid baseline record, with optional field overrides."""
    base = {
        "device_id": "iot_test_01",
        "plate": "DK 0000 ZZ",
        "model": "Test Vehicle",
        "status": "rented",
        "current_speed_kmh": 0,
        "current_location": {"zone": "KUTA_HUB"},
        "battery_voltage": 12.5,
        "fuel_percent": 50,
        "last_ping_seconds_ago": 30,
        "gateway_id": "gw_test_01",
    }
    for k, v in overrides.items():
        if k == "zone":
            base["current_location"]["zone"] = v
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Triage rule tests
# ---------------------------------------------------------------------------

class TestCriticalSecurityAlert(unittest.TestCase):

    def test_available_moving_triggers(self):
        r = triage_vehicle(_rec(status="available", current_speed_kmh=10), 0)
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_available_stationary_no_alert(self):
        r = triage_vehicle(_rec(status="available", current_speed_kmh=0), 0)
        self.assertNotIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_rented_moving_no_alert(self):
        r = triage_vehicle(_rec(status="rented", current_speed_kmh=50), 0)
        self.assertNotIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_restricted_zone_triggers(self):
        r = triage_vehicle(_rec(zone="RESTRICTED_OFFROAD_BATUKARU"), 0)
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_restricted_zone_any_status(self):
        for status in ("rented", "available"):
            with self.subTest(status=status):
                r = triage_vehicle(_rec(status=status, zone="RESTRICTED_OFFROAD_BATUKARU"), 0)
                self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_normal_zone_no_alert(self):
        r = triage_vehicle(_rec(zone="CANGGU"), 0)
        self.assertNotIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_case_insensitive_zone(self):
        r = triage_vehicle(_rec(zone="restricted_offroad_area"), 0)
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)


class TestOfflineTelemetryDrop(unittest.TestCase):

    def test_rented_over_threshold_triggers(self):
        r = triage_vehicle(_rec(status="rented", last_ping_seconds_ago=901), 0)
        self.assertIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_rented_exactly_threshold_no_trigger(self):
        # strictly greater than 900 required
        r = triage_vehicle(_rec(status="rented", last_ping_seconds_ago=900), 0)
        self.assertNotIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_rented_below_threshold_no_trigger(self):
        r = triage_vehicle(_rec(status="rented", last_ping_seconds_ago=899), 0)
        self.assertNotIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_available_over_threshold_no_trigger(self):
        # Available vehicles are excluded from offline check
        r = triage_vehicle(_rec(status="available", last_ping_seconds_ago=9999), 0)
        self.assertNotIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_active_status_triggers(self):
        r = triage_vehicle(_rec(status="active", last_ping_seconds_ago=950), 0)
        self.assertIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_1320_seconds_triggers(self):
        # iot_car_06 reference case
        r = triage_vehicle(_rec(status="rented", last_ping_seconds_ago=1320), 0)
        self.assertIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)


class TestMaintenanceRequired(unittest.TestCase):

    def test_low_battery_triggers(self):
        r = triage_vehicle(_rec(battery_voltage=11.4), 0)
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_battery_exactly_threshold_no_trigger(self):
        # strictly less than 11.5 required
        r = triage_vehicle(_rec(battery_voltage=11.5), 0)
        self.assertNotIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_battery_above_threshold_no_trigger(self):
        r = triage_vehicle(_rec(battery_voltage=12.0), 0)
        self.assertNotIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_low_fuel_triggers(self):
        r = triage_vehicle(_rec(fuel_percent=9), 0)
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_fuel_exactly_threshold_no_trigger(self):
        # strictly less than 15 required
        r = triage_vehicle(_rec(fuel_percent=15), 0)
        self.assertNotIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_fuel_above_threshold_no_trigger(self):
        r = triage_vehicle(_rec(fuel_percent=16), 0)
        self.assertNotIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_both_low_battery_and_fuel(self):
        r = triage_vehicle(_rec(battery_voltage=11.2, fuel_percent=9), 0)
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)
        self.assertIn("11.2", r.tag_reasons["MAINTENANCE_REQUIRED"])
        self.assertIn("9", r.tag_reasons["MAINTENANCE_REQUIRED"])


class TestMultipleTags(unittest.TestCase):

    def test_vehicle_can_have_all_three_tags(self):
        r = triage_vehicle(
            _rec(
                status="rented",
                zone="RESTRICTED_OFFROAD_BATUKARU",
                battery_voltage=11.0,
                fuel_percent=5,
                last_ping_seconds_ago=1000,
            ),
            0,
        )
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)
        self.assertIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)
        self.assertEqual(len(r.incident_tags), 3)

    def test_clear_vehicle_has_no_tags(self):
        r = triage_vehicle(_rec(), 0)
        self.assertEqual(r.incident_tags, [])


class TestValidation(unittest.TestCase):

    def test_missing_device_id_raises(self):
        bad = _rec()
        del bad["device_id"]
        with self.assertRaises(ValueError) as ctx:
            triage_vehicle(bad, 0)
        self.assertIn("device_id", str(ctx.exception))

    def test_missing_zone_raises(self):
        bad = _rec()
        bad["current_location"] = {}
        with self.assertRaises(ValueError):
            triage_vehicle(bad, 0)

    def test_non_dict_record_raises(self):
        with self.assertRaises(ValueError):
            triage_vehicle("not a dict", 0)  # type: ignore

    def test_wrong_type_speed_raises(self):
        bad = _rec()
        bad["current_speed_kmh"] = "fast"
        with self.assertRaises(ValueError):
            triage_vehicle(bad, 0)

    def test_non_list_fleet_raises(self):
        with self.assertRaises(ValueError):
            triage_fleet({"device_id": "x"})  # type: ignore


class TestReferenceDataset(unittest.TestCase):
    """Verify known expected tags against the 8-vehicle outage_telemetry.json."""

    def setUp(self):
        telemetry_path = (
            Path(__file__).resolve().parents[2] / "data" / "outage_telemetry.json"
        )
        self.results = {r.device_id: r for r in load_and_triage(telemetry_path)}

    def test_iot_car_01_clear(self):
        r = self.results["iot_car_01"]
        self.assertEqual(r.incident_tags, [], f"Expected clear, got {r.incident_tags}")

    def test_iot_car_02_critical_security(self):
        # available + moving 52 km/h
        r = self.results["iot_car_02"]
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_iot_car_03_maintenance(self):
        # battery 11.2V < 11.5
        r = self.results["iot_car_03"]
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_iot_car_04_maintenance(self):
        # fuel 9% < 15
        r = self.results["iot_car_04"]
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_iot_car_05_critical_restricted(self):
        # RESTRICTED_OFFROAD_BATUKARU
        r = self.results["iot_car_05"]
        self.assertIn("CRITICAL_SECURITY_ALERT", r.incident_tags)

    def test_iot_car_06_offline(self):
        # 1320s > 900s, rented
        r = self.results["iot_car_06"]
        self.assertIn("OFFLINE_TELEMETRY_DROP", r.incident_tags)

    def test_iot_car_07_clear(self):
        r = self.results["iot_car_07"]
        self.assertEqual(r.incident_tags, [], f"Expected clear, got {r.incident_tags}")

    def test_iot_car_08_maintenance(self):
        # battery 11.3V < 11.5 AND fuel 11% < 15
        r = self.results["iot_car_08"]
        self.assertIn("MAINTENANCE_REQUIRED", r.incident_tags)

    def test_all_eight_records_present(self):
        self.assertEqual(len(self.results), 8)


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------

class TestDispatcherBackoff(unittest.TestCase):

    def test_succeeds_first_attempt(self):
        """Sender succeeds immediately — no retries, no sleep calls."""
        sleeper = MagicMock()
        rand_fn = MagicMock(return_value=0.5)
        sender = MagicMock(return_value=True)

        cfg = DispatchConfig(max_retries=3, base_delay_s=1.0)
        rec = _rec()
        r = triage_vehicle(rec, 0)
        payload = build_alert_payload(r, "MAINTENANCE_REQUIRED")
        # force a tag so we can build a payload
        payload["incident_tag"] = "MAINTENANCE_REQUIRED"

        result = dispatch_alert(payload, cfg, sender, sleeper, rand_fn)

        self.assertTrue(result.delivered)
        self.assertEqual(len(result.attempts), 1)
        sleeper.assert_not_called()

    def test_retries_on_failure_then_succeeds(self):
        """Sender fails twice then succeeds — exactly 3 attempts, 2 sleep calls."""
        call_count = {"n": 0}

        def sender(payload):
            call_count["n"] += 1
            return call_count["n"] >= 3   # succeed on 3rd call

        sleeper = MagicMock()
        rand_fn = lambda: 0.0  # no jitter for determinism

        cfg = DispatchConfig(max_retries=3, base_delay_s=1.0, jitter_fraction=0.0)
        payload = {"alert_id": "TEST_01", "device_id": "x", "incident_tag": "T"}
        result = dispatch_alert(payload, cfg, sender, sleeper, rand_fn)

        self.assertTrue(result.delivered)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(sleeper.call_count, 2)

    def test_all_retries_exhausted(self):
        """Sender always fails — exhausts max_retries, delivered=False."""
        sender = MagicMock(return_value=False)
        sleeper = MagicMock()
        rand_fn = lambda: 0.0

        cfg = DispatchConfig(max_retries=2, base_delay_s=0.0, jitter_fraction=0.0)
        payload = {"alert_id": "TEST_02", "device_id": "x", "incident_tag": "T"}
        result = dispatch_alert(payload, cfg, sender, sleeper, rand_fn)

        self.assertFalse(result.delivered)
        self.assertEqual(result.final_status, "failed_after_retries")
        self.assertEqual(len(result.attempts), 3)   # initial + 2 retries

    def test_backoff_delay_increases(self):
        """Verify delay grows exponentially across retries (no jitter)."""
        delays_recorded = []

        def sleeper(s):
            delays_recorded.append(s)

        sender = MagicMock(return_value=False)
        cfg = DispatchConfig(max_retries=3, base_delay_s=1.0, max_delay_s=100.0, jitter_fraction=0.0)
        payload = {"alert_id": "TEST_03", "device_id": "x", "incident_tag": "T"}
        dispatch_alert(payload, cfg, sender, sleeper, lambda: 0.0)

        # delays should be 1, 2, 4 (base * 2^0, 2^1, 2^2)
        self.assertEqual(delays_recorded, [1.0, 2.0, 4.0])

    def test_no_real_sleep_in_tests(self):
        """Ensure tests never wait — sleeper is always mocked."""
        import time
        sleeper = MagicMock()
        sender = MagicMock(return_value=False)
        cfg = DispatchConfig(max_retries=3)
        payload = {"alert_id": "T", "device_id": "x", "incident_tag": "T"}

        start = time.monotonic()
        dispatch_alert(payload, cfg, sender, sleeper)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0, "Test must not block on real delays")

    def test_sender_exception_treated_as_failure(self):
        """If sender raises, it counts as a failure and retries continue."""
        call_count = {"n": 0}

        def sender(payload):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("network error")
            return True

        cfg = DispatchConfig(max_retries=3)
        payload = {"alert_id": "T", "device_id": "x", "incident_tag": "T"}
        result = dispatch_alert(payload, cfg, sender, lambda s: None, lambda: 0.0)

        self.assertTrue(result.delivered)
        self.assertEqual(len(result.attempts), 3)
        self.assertIsNotNone(result.attempts[0].error)


class TestDispatcherCapBound(unittest.TestCase):
    """
    Regression tests verifying that _compute_delay never exceeds cap,
    including when jitter is at its maximum (rand_fn returns values close to 1.0).
    """

    def test_delay_never_exceeds_cap_at_max_jitter(self):
        """
        With rand_fn=lambda: 0.9999 (near-maximum jitter), the returned delay
        must still be <= max_delay_s (the configured cap).
        """
        cfg = DispatchConfig(max_retries=5, base_delay_s=1.0, max_delay_s=10.0, jitter_fraction=0.5)
        near_max_rand = lambda: 0.9999

        for attempt in range(cfg.max_retries + 1):
            delay = _compute_delay(
                attempt,
                cfg.base_delay_s,
                cfg.max_delay_s,
                cfg.jitter_fraction,
                near_max_rand,
            )
            self.assertLessEqual(
                delay,
                cfg.max_delay_s,
                f"Delay {delay} exceeds cap {cfg.max_delay_s} at attempt {attempt}",
            )

    def test_delay_never_exceeds_cap_at_exact_one_jitter(self):
        """rand_fn returning exactly 1.0 is a degenerate edge case; delay must still <= cap."""
        cfg = DispatchConfig(base_delay_s=2.0, max_delay_s=5.0, jitter_fraction=1.0)
        # rand_fn=1.0 means jitter = expo * 1.0 * 1.0 = expo; total = 2*expo before cap clamp.
        for attempt in range(4):
            delay = _compute_delay(attempt, cfg.base_delay_s, cfg.max_delay_s, cfg.jitter_fraction, lambda: 1.0)
            self.assertLessEqual(
                delay,
                cfg.max_delay_s,
                f"Delay {delay} exceeds cap {cfg.max_delay_s} at attempt {attempt} with jitter=1.0",
            )

    def test_delay_without_jitter_equals_capped_exponential(self):
        """With jitter_fraction=0 or rand_fn=0.0, delay must equal min(base*2^attempt, cap)."""
        import math as _math
        base, cap = 1.0, 8.0
        for attempt in range(5):
            expected = round(min(base * _math.pow(2, attempt), cap), 3)
            got = _compute_delay(attempt, base, cap, 0.5, lambda: 0.0)
            self.assertAlmostEqual(got, expected, places=3,
                                   msg=f"Zero-jitter delay mismatch at attempt {attempt}")


class TestFleetDispatch(unittest.TestCase):

    def test_vehicles_without_incidents_included(self):
        """All vehicles must appear in summaries, even those with no tags."""
        import json
        from pathlib import Path as P
        results = load_and_triage(
            P(__file__).resolve().parents[2] / "data" / "outage_telemetry.json"
        )
        summaries = dispatch_fleet(
            results,
            sender=lambda p: True,
            sleeper=lambda s: None,
            rand_fn=lambda: 0.0,
        )
        self.assertEqual(len(summaries), 8)

    def test_reconciliation_structure(self):
        results = load_and_triage(
            Path(__file__).resolve().parents[2] / "data" / "outage_telemetry.json"
        )
        summaries = dispatch_fleet(
            results,
            sender=lambda p: True,
            sleeper=lambda s: None,
            rand_fn=lambda: 0.0,
        )
        rec = build_reconciliation(summaries)
        self.assertIn("summary", rec)
        self.assertIn("vehicles", rec)
        self.assertEqual(rec["summary"]["total_vehicles"], 8)
        self.assertGreater(rec["summary"]["vehicles_with_incidents"], 0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
