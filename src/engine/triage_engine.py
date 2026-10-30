"""
triage_engine.py — Fleet IoT triage: load telemetry, tag incidents.

Incident rules
--------------
CRITICAL_SECURITY_ALERT
  • An "available" vehicle moving faster than 0 km/h
    (available vehicles should not be moving — possible theft or tracking error).
  • ANY vehicle in a zone whose name contains "RESTRICTED" (case-insensitive).
    The zone RESTRICTED_OFFROAD_BATUKARU is a documented off-road area banned
    under Easy Rent Bali's rental contract; GPS entry triggers immediate review.

OFFLINE_TELEMETRY_DROP
  • A rented or active vehicle with last_ping_seconds_ago > 900
    (strictly more than 15 minutes without a heartbeat — possible device loss
     or network failure on the vehicle gateway).
  • "Available" vehicles are excluded: silent available vehicles are expected.

MAINTENANCE_REQUIRED
  • battery_voltage < 11.5 V  (strict less-than)
  • OR fuel_percent < 15      (strict less-than)

Tags are additive — a vehicle may carry 0, 1, 2, or all 3 tags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESTRICTED_ZONE_MARKERS: list[str] = ["RESTRICTED"]
"""Zone name substrings (upper-case, case-insensitive match) that trigger CRITICAL_SECURITY_ALERT."""

OFFLINE_PING_THRESHOLD_SECONDS: int = 900
"""last_ping_seconds_ago must be STRICTLY GREATER THAN this value for OFFLINE_TELEMETRY_DROP."""

BATTERY_LOW_THRESHOLD_V: float = 11.5
"""battery_voltage must be STRICTLY LESS THAN this for MAINTENANCE_REQUIRED."""

FUEL_LOW_THRESHOLD_PCT: int = 15
"""fuel_percent must be STRICTLY LESS THAN this for MAINTENANCE_REQUIRED."""

ACTIVE_STATUSES: frozenset[str] = frozenset({"rented", "active"})
"""Statuses considered 'in use' for OFFLINE_TELEMETRY_DROP evaluation."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VehicleTriageResult:
    device_id: str
    plate: str
    model: str
    status: str
    zone: str
    current_speed_kmh: float
    battery_voltage: float
    fuel_percent: float
    last_ping_seconds_ago: int
    gateway_id: str
    incident_tags: list[str] = field(default_factory=list)
    tag_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "plate": self.plate,
            "model": self.model,
            "status": self.status,
            "zone": self.zone,
            "current_speed_kmh": self.current_speed_kmh,
            "battery_voltage": self.battery_voltage,
            "fuel_percent": self.fuel_percent,
            "last_ping_seconds_ago": self.last_ping_seconds_ago,
            "gateway_id": self.gateway_id,
            "incident_tags": self.incident_tags,
            "tag_reasons": self.tag_reasons,
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {
    "device_id": str,
    "plate": str,
    "model": str,
    "status": str,
    "current_speed_kmh": (int, float),
    "current_location": dict,
    "battery_voltage": (int, float),
    "fuel_percent": (int, float),
    "last_ping_seconds_ago": (int, float),
    "gateway_id": str,
}


def _validate_record(record: Any, index: int) -> dict:
    """Raise ValueError with a clear message on malformed/missing fields."""
    if not isinstance(record, dict):
        raise ValueError(f"Record[{index}] is not a JSON object: {type(record).__name__}")

    for field_name, expected_types in _REQUIRED_FIELDS.items():
        if field_name not in record:
            raise ValueError(
                f"Record[{index}] ({record.get('device_id', '?')}): "
                f"missing required field '{field_name}'"
            )
        value = record[field_name]
        if not isinstance(value, expected_types):
            raise ValueError(
                f"Record[{index}] ({record.get('device_id', '?')}): "
                f"field '{field_name}' expected {expected_types}, got {type(value).__name__}"
            )

    loc = record["current_location"]
    if "zone" not in loc or not isinstance(loc["zone"], str):
        raise ValueError(
            f"Record[{index}] ({record.get('device_id', '?')}): "
            "current_location.zone must be a non-empty string"
        )
    return record


# ---------------------------------------------------------------------------
# Triage rules
# ---------------------------------------------------------------------------

def _tag_critical_security(rec: dict) -> tuple[bool, str]:
    """Return (triggered, reason)."""
    reasons = []
    zone = rec["current_location"]["zone"].upper()
    speed = rec["current_speed_kmh"]
    status = rec["status"].lower()

    if status == "available" and speed > 0:
        reasons.append(
            f"available vehicle moving at {speed} km/h (should be stationary)"
        )

    for marker in RESTRICTED_ZONE_MARKERS:
        if marker in zone:
            reasons.append(
                f"vehicle entered restricted zone '{rec['current_location']['zone']}' "
                f"(policy: no-go area under rental contract)"
            )
            break  # one match is sufficient

    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def _tag_offline_telemetry(rec: dict) -> tuple[bool, str]:
    ping = rec["last_ping_seconds_ago"]
    status = rec["status"].lower()
    if status in ACTIVE_STATUSES and ping > OFFLINE_PING_THRESHOLD_SECONDS:
        return True, (
            f"last ping {ping}s ago "
            f"(>{OFFLINE_PING_THRESHOLD_SECONDS}s threshold; vehicle status={rec['status']})"
        )
    return False, ""


def _tag_maintenance(rec: dict) -> tuple[bool, str]:
    reasons = []
    bv = rec["battery_voltage"]
    fp = rec["fuel_percent"]
    if bv < BATTERY_LOW_THRESHOLD_V:
        reasons.append(f"battery_voltage {bv}V < {BATTERY_LOW_THRESHOLD_V}V")
    if fp < FUEL_LOW_THRESHOLD_PCT:
        reasons.append(f"fuel_percent {fp}% < {FUEL_LOW_THRESHOLD_PCT}%")
    if reasons:
        return True, "; ".join(reasons)
    return False, ""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def triage_vehicle(record: dict, index: int) -> VehicleTriageResult:
    """Validate a single record and apply all triage rules."""
    _validate_record(record, index)

    result = VehicleTriageResult(
        device_id=record["device_id"],
        plate=record["plate"],
        model=record["model"],
        status=record["status"],
        zone=record["current_location"]["zone"],
        current_speed_kmh=record["current_speed_kmh"],
        battery_voltage=record["battery_voltage"],
        fuel_percent=record["fuel_percent"],
        last_ping_seconds_ago=int(record["last_ping_seconds_ago"]),
        gateway_id=record["gateway_id"],
    )

    for tag, rule_fn in [
        ("CRITICAL_SECURITY_ALERT", _tag_critical_security),
        ("OFFLINE_TELEMETRY_DROP", _tag_offline_telemetry),
        ("MAINTENANCE_REQUIRED", _tag_maintenance),
    ]:
        triggered, reason = rule_fn(record)
        if triggered:
            result.incident_tags.append(tag)
            result.tag_reasons[tag] = reason

    return result


def triage_fleet(records: list[dict]) -> list[VehicleTriageResult]:
    """Triage an entire fleet; fails fast on any invalid record."""
    if not isinstance(records, list):
        raise ValueError("Fleet telemetry must be a JSON array of vehicle records.")
    results = []
    for i, rec in enumerate(records):
        results.append(triage_vehicle(rec, i))
    return results


def load_and_triage(telemetry_path: str | Path) -> list[VehicleTriageResult]:
    """Load telemetry JSON from disk and run triage."""
    path = Path(telemetry_path)
    if not path.exists():
        raise FileNotFoundError(f"Telemetry file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return triage_fleet(raw)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    telemetry_file = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[2] / "data" / "outage_telemetry.json"
    )
    results = load_and_triage(telemetry_file)
    for r in results:
        tags = r.incident_tags or ["CLEAR"]
        print(f"{r.device_id:12s} [{r.plate}]  tags={tags}")
