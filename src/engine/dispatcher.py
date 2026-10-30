"""
dispatcher.py — Structured alert dispatcher with bounded exponential backoff + jitter.

Design
------
- Produces a structured alert payload for each incident tag on a vehicle.
- Sends alerts via an injectable sender function (default: a no-op mock).
- Uses bounded exponential backoff with random jitter between retries.
- Sleeper and RNG are injectable for deterministic unit tests (no real waits).
- Never claims real external delivery occurred.
- All timing functions are injected; tests pass instant-return sleepers.
"""

from __future__ import annotations

import json
import math
import random as _random_module
import time as _time_module
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from src.engine.triage_engine import VehicleTriageResult


# ---------------------------------------------------------------------------
# Types / configuration
# ---------------------------------------------------------------------------

SenderFn = Callable[[dict], bool]
"""Callable accepting an alert payload dict; returns True on success, False on failure."""

SleeperFn = Callable[[float], None]
"""Callable accepting seconds to sleep (injectable for tests)."""

RandomFn = Callable[[], float]
"""Callable returning a float in [0.0, 1.0) (injectable for tests)."""


@dataclass
class DispatchConfig:
    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter_fraction: float = 0.5   # jitter up to ±50 % of computed delay


# ---------------------------------------------------------------------------
# Alert payload builder
# ---------------------------------------------------------------------------

ALERT_SEVERITY: dict[str, str] = {
    "CRITICAL_SECURITY_ALERT": "CRITICAL",
    "OFFLINE_TELEMETRY_DROP": "HIGH",
    "MAINTENANCE_REQUIRED": "MEDIUM",
}

ALERT_CHANNELS: dict[str, list[str]] = {
    "CRITICAL_SECURITY_ALERT": ["ops_manager", "security_team"],
    "OFFLINE_TELEMETRY_DROP": ["fleet_ops"],
    "MAINTENANCE_REQUIRED": ["maintenance_team"],
}


def build_alert_payload(result: VehicleTriageResult, tag: str) -> dict[str, Any]:
    """Construct a structured alert dict for one incident tag on one vehicle."""
    return {
        "alert_id": f"ALT_{uuid.uuid4().hex[:12].upper()}",
        "device_id": result.device_id,
        "plate": result.plate,
        "model": result.model,
        "status": result.status,
        "zone": result.zone,
        "incident_tag": tag,
        "severity": ALERT_SEVERITY.get(tag, "UNKNOWN"),
        "channels": ALERT_CHANNELS.get(tag, ["ops_manager"]),
        "reason": result.tag_reasons.get(tag, ""),
        "battery_voltage": result.battery_voltage,
        "fuel_percent": result.fuel_percent,
        "last_ping_seconds_ago": result.last_ping_seconds_ago,
        "gateway_id": result.gateway_id,
        "simulated": True,
        "note": "This is a simulated alert. No real notification was delivered.",
    }


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------

def _compute_delay(
    attempt: int,
    base: float,
    cap: float,
    jitter_fraction: float,
    rand_fn: RandomFn,
) -> float:
    """
    Exponential backoff with additive jitter, capped at ``cap``.

    Algorithm:
      expo  = min(base * 2^attempt, cap)          — capped exponential base
      jitter = expo * jitter_fraction * rand_fn() — non-negative additive jitter
                                                    (rand_fn returns [0.0, 1.0))
      delay = min(expo + jitter, cap)             — final delay never exceeds cap

    ``jitter_fraction`` controls the *maximum* jitter as a fraction of ``expo``
    (e.g. 0.5 → up to +50 % of the base delay, not bidirectional ±).
    ``cap`` is the hard upper bound on the returned delay including jitter.
    """
    expo = min(base * math.pow(2, attempt), cap)
    jitter = expo * jitter_fraction * rand_fn()   # always non-negative
    return round(min(expo + jitter, cap), 3)


# ---------------------------------------------------------------------------
# Dispatch attempt result
# ---------------------------------------------------------------------------

@dataclass
class AttemptRecord:
    attempt: int
    success: bool
    delay_before_s: float
    error: Optional[str] = None


@dataclass
class DispatchResult:
    alert_id: str
    device_id: str
    incident_tag: str
    delivered: bool
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_status: str = "unknown"
    note: str = "Simulated dispatch only. No real notification delivered."

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "device_id": self.device_id,
            "incident_tag": self.incident_tag,
            "delivered": self.delivered,
            "final_status": self.final_status,
            "attempts": [
                {
                    "attempt": a.attempt,
                    "success": a.success,
                    "delay_before_s": a.delay_before_s,
                    "error": a.error,
                }
                for a in self.attempts
            ],
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _default_sender(payload: dict) -> bool:
    """No-op mock sender — always succeeds. No real notification sent."""
    return True


def dispatch_alert(
    payload: dict,
    config: DispatchConfig | None = None,
    sender: SenderFn = _default_sender,
    sleeper: SleeperFn = _time_module.sleep,
    rand_fn: RandomFn = _random_module.random,
) -> DispatchResult:
    """
    Send one alert with bounded exponential backoff + jitter.

    Parameters
    ----------
    payload   : alert payload from build_alert_payload()
    config    : retry / backoff configuration
    sender    : injectable; receives payload dict, returns bool (success)
    sleeper   : injectable; called with delay_seconds before each retry
    rand_fn   : injectable; returns float in [0,1) for jitter
    """
    if config is None:
        config = DispatchConfig()

    result = DispatchResult(
        alert_id=payload["alert_id"],
        device_id=payload["device_id"],
        incident_tag=payload["incident_tag"],
        delivered=False,
    )

    for attempt in range(config.max_retries + 1):
        delay = 0.0
        if attempt > 0:
            delay = _compute_delay(
                attempt - 1,
                config.base_delay_s,
                config.max_delay_s,
                config.jitter_fraction,
                rand_fn,
            )
            sleeper(delay)

        try:
            success = sender(payload)
        except Exception as exc:
            success = False
            error_msg = str(exc)
        else:
            error_msg = None if success else "sender returned False"

        rec = AttemptRecord(
            attempt=attempt + 1,
            success=success,
            delay_before_s=delay,
            error=error_msg,
        )
        result.attempts.append(rec)

        if success:
            result.delivered = True
            result.final_status = "delivered_simulated"
            break
    else:
        result.final_status = "failed_after_retries"

    return result


# ---------------------------------------------------------------------------
# Fleet dispatch
# ---------------------------------------------------------------------------

@dataclass
class VehicleDispatchSummary:
    device_id: str
    plate: str
    model: str
    status: str
    zone: str
    incident_tags: list[str]
    tag_reasons: dict[str, str]
    alert_payloads: list[dict]
    dispatch_results: list[dict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "plate": self.plate,
            "model": self.model,
            "status": self.status,
            "zone": self.zone,
            "incident_tags": self.incident_tags,
            "tag_reasons": self.tag_reasons,
            "alert_payloads": self.alert_payloads,
            "dispatch_results": self.dispatch_results,
        }


def dispatch_fleet(
    triage_results: list[VehicleTriageResult],
    config: DispatchConfig | None = None,
    sender: SenderFn = _default_sender,
    sleeper: SleeperFn = _time_module.sleep,
    rand_fn: RandomFn = _random_module.random,
) -> list[VehicleDispatchSummary]:
    """Dispatch alerts for every vehicle that has at least one incident tag."""
    summaries: list[VehicleDispatchSummary] = []
    for result in triage_results:
        payloads = []
        dispatch_results = []
        for tag in result.incident_tags:
            payload = build_alert_payload(result, tag)
            dr = dispatch_alert(payload, config, sender, sleeper, rand_fn)
            payloads.append(payload)
            dispatch_results.append(dr.to_dict())

        summaries.append(
            VehicleDispatchSummary(
                device_id=result.device_id,
                plate=result.plate,
                model=result.model,
                status=result.status,
                zone=result.zone,
                incident_tags=result.incident_tags,
                tag_reasons=result.tag_reasons,
                alert_payloads=payloads,
                dispatch_results=dispatch_results,
            )
        )
    return summaries


# ---------------------------------------------------------------------------
# Reconciliation output
# ---------------------------------------------------------------------------

def build_reconciliation(
    summaries: list[VehicleDispatchSummary],
) -> dict[str, Any]:
    """Build the full incident_reconciliation.json structure."""
    total_vehicles = len(summaries)
    vehicles_with_incidents = sum(1 for s in summaries if s.incident_tags)
    tag_counts: dict[str, int] = {}
    for s in summaries:
        for t in s.incident_tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    return {
        "generated_at": _now_iso(),
        "note": (
            "Simulated reconciliation. No real alerts were delivered. "
            "Dispatch results reflect mock sender behaviour only."
        ),
        "summary": {
            "total_vehicles": total_vehicles,
            "vehicles_with_incidents": vehicles_with_incidents,
            "vehicles_clear": total_vehicles - vehicles_with_incidents,
            "incident_tag_counts": tag_counts,
        },
        "vehicles": [s.to_dict() for s in summaries],
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI entry — generates output/incident_reconciliation.json
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    telemetry_path = repo_root / "data" / "outage_telemetry.json"
    output_path = repo_root / "output" / "incident_reconciliation.json"

    from src.engine.triage_engine import load_and_triage

    print(f"Loading telemetry from {telemetry_path} …")
    triage_results = load_and_triage(telemetry_path)

    print("Dispatching alerts …")
    summaries = dispatch_fleet(triage_results)

    reconciliation = build_reconciliation(summaries)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(reconciliation, fh, indent=2, ensure_ascii=False)

    print(f"Reconciliation written to {output_path}")
    s = reconciliation["summary"]
    print(
        f"  Vehicles total:         {s['total_vehicles']}\n"
        f"  Vehicles with incidents:{s['vehicles_with_incidents']}\n"
        f"  Vehicles clear:         {s['vehicles_clear']}\n"
        f"  Tag counts:             {s['incident_tag_counts']}"
    )
