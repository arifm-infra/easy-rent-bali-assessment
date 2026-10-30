# Network Architecture — Easy Rent Bali (Kuta Hub)

**Document type:** Design specification  
**Revision:** 1.0  
**Date:** 2026-10-30  
**Scope:** Kuta Hub site WAN/LAN, VLAN segmentation, dual-WAN failover, and connectivity to HQ Renon and Cloud Management.

---

## 1. Site Topology

```
                ┌─────────────────────────────────────┐
                │          CLOUD MANAGEMENT            │
                │  (AWS/GCP — Fleet SaaS, POS Cloud,  │
                │   Monitoring, VPN Concentrator)       │
                └────────────┬────────────────────────┘
                             │  IPSec / WireGuard
                             │  Site-to-Site VPN
              ┌──────────────┴──────────────┐
              │         HQ RENON            │
              │  Core Router / Firewall     │
              │  (Policy, BGP, VPN hub)     │
              └──────────────┬──────────────┘
                             │
             ┌───────────────┴────────────────┐
             │         WAN DUAL-LINK          │
             │  WAN1: Primary Fiber (50 Mbps) │
             │  WAN2: Cellular LTE (10 Mbps)  │
             └───────────────┬────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  EDGE GATEWAY / FIREWALL    │
              │  (Dual-WAN, NAT, IPSec,     │
              │   BGP/Static, QoS shaper)   │
              └──────────────┬──────────────┘
                             │  Trunk (802.1Q)
              ┌──────────────▼──────────────┐
              │   L2/L3 MANAGED VLAN SWITCH │
              │   (Inter-VLAN routing via   │
              │    firewall policy; ACLs)   │
              └──┬──────┬──────┬──────┬─────┘
                 │      │      │      │
         VLAN10 VLAN20 VLAN30 VLAN40 VLAN50
         Mgmt   POS   Staff  IoT/CCTV Guest
```

### Link Descriptions

| Link | Type | Capacity | Role |
|------|------|----------|------|
| WAN1 | Fiber (ISP-A) | 50 Mbps symmetric | Primary internet + VPN |
| WAN2 | LTE (Telkomsel / XL) | ≤ 10 Mbps DL / 5 Mbps UL | Cold standby; failover only |
| HQ↔Kuta | IPSec/WireGuard over WAN1 | ~45 Mbps (post-overhead) | Operational traffic, sync |
| Kuta↔Cloud | IPSec/WireGuard over WAN1 | Shared with HQ tunnel | Fleet telemetry, POS auth |

---

## 2. VLAN Matrix

### 2.1 VLAN 10 — Management

| Field | Value |
|-------|-------|
| **Subnet** | `10.10.10.0/24` |
| **Gateway** | `10.10.10.1` |
| **DHCP Scope** | `10.10.10.10 – 10.10.10.50` |
| **Static Reservations** | Switches, APs, gateways (`.1`–`.9`) |
| **Purpose** | Out-of-band management of network infrastructure |
| **QoS / DSCP** | CS6 (Network Control); 5 Mbps guaranteed minimum |
| **Access restriction** | Admin SSH/HTTPS from `10.10.10.0/24` only; no access from other VLANs |

**Inter-VLAN rules (default-deny, explicit allow):**
- ❌ No traffic from any other VLAN into VLAN 10 (ingress drop-all)
- ✅ VLAN 10 → WAN (NTP, firmware, SNMP trap collector at HQ)
- ✅ VLAN 10 → HQ Renon management subnet (for NOC monitoring)

**Example DHCP pool (ISC / dnsmasq):**
```
dhcp-range=vlan10,10.10.10.10,10.10.10.50,255.255.255.0,8h
dhcp-option=vlan10,3,10.10.10.1   # default gateway
dhcp-option=vlan10,6,10.10.10.1   # DNS (local resolver)
```

---

### 2.2 VLAN 20 — POS & Kiosks

| Field | Value |
|-------|-------|
| **Subnet** | `10.10.20.0/24` |
| **Gateway** | `10.10.20.1` |
| **DHCP Scope** | `10.10.20.10 – 10.10.20.80` |
| **Static Reservations** | POS terminals (`.10`–`.30`), kiosk endpoints (`.31`–`.50`) |
| **Purpose** | Payment terminals, self-service kiosks, booking POS |
| **QoS / DSCP** | DSCP EF (Expedited Forwarding, 46); highest-priority queue; 15 Mbps guaranteed; strict PQ on WAN |

**Inter-VLAN rules:**
- ❌ No ingress from VLAN 30/40/50 into VLAN 20
- ✅ VLAN 20 → WAN (payment gateway IPs, Stripe/Midtrans whitelisted FQDN — via firewall app-layer or IP list)
- ✅ VLAN 20 → Cloud Management (POS auth, reservation API) over VPN
- ✅ VLAN 20 → HQ Renon (accounting back-office API; specific IP + port only)
- ❌ VLAN 20 → VLAN 10/30/40/50 (blocked)

**Cellular WAN2 (failover) priority:** POS traffic receives highest QoS queue on WAN2; guest (VLAN 50) is suspended on WAN2 (see Section 4.2).

**Example DHCP pool:**
```
dhcp-range=vlan20,10.10.20.10,10.10.20.80,255.255.255.0,12h
dhcp-option=vlan20,3,10.10.20.1
dhcp-option=vlan20,6,10.10.20.1
```

---

### 2.3 VLAN 30 — Staff

| Field | Value |
|-------|-------|
| **Subnet** | `10.10.30.0/24` |
| **Gateway** | `10.10.30.1` |
| **DHCP Scope** | `10.10.30.10 – 10.10.30.150` |
| **Static Reservations** | Desk printers (`.200`–`.210`) |
| **Purpose** | Staff workstations, internal operations, desk printers |
| **QoS / DSCP** | AF21 (Assured Forwarding); 10 Mbps guaranteed; best-effort burst |

**Inter-VLAN rules:**
- ❌ No access to VLAN 10 (management) or VLAN 20 (POS) or VLAN 50 (guest)
- ✅ VLAN 30 → VLAN 30 static printers (`10.10.30.200/28`; TCP 9100, 631 IPP only)
- ✅ VLAN 30 → WAN (internet, HR/ops SaaS over HTTPS)
- ✅ VLAN 30 → Cloud Management (ops APIs, fleet dashboard read-only) over VPN
- ✅ VLAN 30 → HQ Renon (shared file services, ticketing) over VPN
- ❌ VLAN 30 → VLAN 40 (IoT/CCTV — no workstation access to IoT segment)

**Example DHCP pool:**
```
dhcp-range=vlan30,10.10.30.10,10.10.30.150,255.255.255.0,8h
dhcp-option=vlan30,3,10.10.30.1
dhcp-option=vlan30,6,10.10.30.1
# Printer static assignment
dhcp-host=aa:bb:cc:dd:ee:01,10.10.30.200
```

---

### 2.4 VLAN 40 — Fleet IoT & CCTV

| Field | Value |
|-------|-------|
| **Subnet** | `10.10.40.0/24` |
| **Gateway** | `10.10.40.1` |
| **DHCP Scope** | `10.10.40.10 – 10.10.40.100` |
| **Static Reservations** | Telemetry gateways (`.10`–`.20`), CCTV NVR (`.21`–`.30`) |
| **Purpose** | Vehicle IoT telemetry gateways, yard CCTV cameras and NVR |
| **QoS / DSCP** | AF11 (low-latency best-effort); 5 Mbps guaranteed for telemetry |

**Inter-VLAN rules:**
- ❌ No unsolicited public internet ingress (inbound NAT/firewall blocks all new connections from WAN)
- ✅ VLAN 40 → Cloud Management (telemetry MQTT/HTTPS outbound; specific FQDN/IP + port 8883/443)
- ✅ VLAN 40 → HQ Renon (CCTV feed to NVR at HQ; TCP 554 RTSP, specific destination IP only)
- ❌ VLAN 40 → any other VLAN (no peer-to-peer with staff, POS, or guest)
- ❌ Any VLAN → VLAN 40 (ingress blocked; only established/related return traffic permitted)

**Example DHCP pool:**
```
dhcp-range=vlan40,10.10.40.10,10.10.40.100,255.255.255.0,24h
dhcp-option=vlan40,3,10.10.40.1
dhcp-option=vlan40,6,10.10.40.1
# Gateway static assignment
dhcp-host=11:22:33:44:55:01,10.10.40.10
```

---

### 2.5 VLAN 50 — Guest Wi-Fi

| Field | Value |
|-------|-------|
| **Subnet (assessment literal)** | `172.16.50.0/22` |
| **Subnet (implementable canonical)** | `172.16.48.0/22` (see note below) |
| **Gateway** | `172.16.48.1` |
| **DHCP Scope** | `172.16.48.100 – 172.16.51.200` |
| **Purpose** | Customer and visitor Wi-Fi; fully isolated from all internal VLANs |
| **QoS** | 2 Mbps per-client hard cap (policing via firewall / AP controller); AF11 class; DSCP CS1 |

> **⚠️ Subnet Notation Note:**  
> The assessment specifies `172.16.50.0/22`. This is **not a canonical /22 network address**.  
> A /22 prefix has a 4-octet boundary of 4 addresses in the third octet (`.0`, `.4`, `.8`, …).  
> The canonical /22 that contains `172.16.50.0` is **`172.16.48.0/22`** (range: `172.16.48.0 – 172.16.51.255`).  
> Using `172.16.50.0` as a network address on equipment that validates canonical boundaries will be **rejected or corrected** by most L3 devices.  
>
> **Resolution:** Configure the network as `172.16.48.0/22`. The literal value `172.16.50.0/22` is preserved here for traceability. The gateway, DHCP scope, and firewall rules in this document use the canonical `172.16.48.0/22`.

**Inter-VLAN rules:**
- ✅ VLAN 50 → WAN (internet only; HTTP/HTTPS/DNS; captive portal redirect)
- ❌ VLAN 50 → any internal VLAN (10/20/30/40) — absolute block
- ❌ Inter-client traffic on VLAN 50 (AP client isolation enabled)
- ❌ VLAN 50 aggregate traffic on WAN2 (cellular) hard-capped at **≤ 120 kbps** via traffic policing (strictly below the 128 kbps assessment limit); effectively suspended during failover (see Section 4.2)

**Example DHCP pool:**
```
dhcp-range=vlan50,172.16.48.100,172.16.51.200,255.255.252.0,1h
dhcp-option=vlan50,3,172.16.48.1
dhcp-option=vlan50,6,8.8.8.8,8.8.4.4   # Google DNS (public, no internal visibility)
```

---

## 3. Default-Deny Inter-VLAN Policy Summary

```
FROM \ TO   | VLAN10 | VLAN20 | VLAN30 | VLAN40 | VLAN50 | WAN  | HQ/Cloud
------------|--------|--------|--------|--------|--------|------|----------
VLAN10 Mgmt |   -    |   ❌   |   ❌   |   ❌   |   ❌   |  ✅  |  ✅ (mgmt)
VLAN20 POS  |   ❌   |   -    |   ❌   |   ❌   |   ❌   |  ✅* |  ✅ (POS)
VLAN30 Staff|   ❌   |   ❌   |   -    |   ❌   |   ❌   |  ✅  |  ✅ (ops)
VLAN40 IoT  |   ❌   |   ❌   |   ❌   |   -    |   ❌   |  ✅† |  ✅ (telem)
VLAN50 Guest|   ❌   |   ❌   |   ❌   |   ❌   |   ❌†† |  ✅‡ |  ❌
```
\* POS to WAN: firewall allows only whitelisted payment gateway FQDNs/IPs  
† IoT outbound only; no unsolicited inbound  
†† AP client isolation; no guest-to-guest traffic  
‡ Guest WAN capped; suspended on WAN2 cellular failover  

---

## 4. Dual-WAN Failover Design

### 4.1 Health-Check Logic (Vendor-neutral design targets)

> **⚠️ All timings below are design targets. Actual failover time depends on hardware, firmware, and link characteristics. Values must be validated in the field with load testing.**

| Parameter | WAN1 (Fiber) | WAN2 (Cellular) |
|-----------|-------------|-----------------|
| Health-check targets | `8.8.8.8`, `1.1.1.1`, HQ VPN endpoint | `8.8.8.8`, `1.1.1.1` |
| Ping interval | 1 second | 1 second (on standby check) |
| Packet size | 64 bytes ICMP | 64 bytes ICMP |
| **Failure threshold** | 2 consecutive losses (after jitter filter) | N/A (promoted only on WAN1 failure) |
| **Recovery threshold** | 5 consecutive successes (hysteresis) | 5 consecutive successes |
| **Design target failover time** | **< 3 seconds** (2 missed pings × 1 s = 2 s detection + < 1 s routing switch = total < 3 s) | — |
| **Design target recovery time** | ~5 seconds (5 success pings × 1 s + settle) | — |

**Flapping / hysteresis protection:**
- A single missed ping does **not** trigger failover. Two consecutive losses must occur.
- **Timing rationale:** With a 1-second ping interval and a 2-miss threshold, the failure detection window is exactly **2 seconds** (ping 1 fails at t = 1 s, ping 2 fails at t = 2 s, failover decision taken). This leaves < 1 second headroom for routing-table convergence and traffic switching to remain within the **< 3-second design target**. A 3-miss threshold at 1-second intervals would consume the full 3-second budget on detection alone and exceed it once switching overhead is added — this is therefore not used.
- On recovery, WAN1 must pass **5** consecutive health checks before traffic is shifted back.  
  This prevents flapping if WAN1 link is oscillating.
- An additional **10-second hold-down timer** after WAN1 recovery is recommended before preemption to allow BGP/routing table convergence.
- Packet loss measurement (e.g., 2 of last 3 pings lost) may supplement consecutive-loss logic on advanced platforms.

> **⚠️ All timings are design targets.** On platforms with hardware link-down detection (physical layer signalling), failover may be sub-second regardless of ping thresholds. On software-only or cloud-router platforms, actual times must be measured under realistic load. These values have **not been measured on target hardware**.

### 4.2 WAN2 (Cellular) Traffic Policy During Failover

WAN2 is a metered LTE link. During failover, traffic must be strictly prioritized:

| Queue Priority | Traffic | Policy |
|---------------|---------|--------|
| 1 (Highest) | VLAN 20 POS & payment (DSCP EF) | Full pass-through; guaranteed 2 Mbps |
| 2 | Cloud heartbeat / VPN keepalive | Guaranteed 512 kbps |
| 3 | VLAN 40 IoT telemetry (essential) | Best-effort up to 1 Mbps |
| 4 | VLAN 30 Staff (HTTPS only) | Best-effort up to 2 Mbps |
| 5 (Lowest) | VLAN 50 Guest | **Hard-capped at ≤ 120 kbps aggregate** via traffic policing (strictly below the 128 kbps assessment limit; effectively suspended) |

Implementation: Firewall/router traffic-shaping policy applied on WAN2 interface; VLAN 50 traffic is policed to **≤ 120 kbps** (strictly below the PDF requirement of < 128 kbps; can be set to 0 if operator suspends guest entirely on WAN2). This is enforced by a separate WAN2-specific QoS policy distinct from WAN1 policy.

### 4.3 Site-to-Site VPN

- **Protocol:** WireGuard (preferred for low latency) or IPSec IKEv2 (for hardware compatibility)
- **Endpoints:** Kuta Hub Edge GW ↔ HQ Renon Core Router ↔ Cloud VPN Concentrator
- **Authentication:** Pre-shared key or certificate-based (PKI recommended for production)
- **Split tunneling:** Internal subnets (`10.10.0.0/16`, `10.0.0.0/8`) routed over VPN; internet traffic exits locally via WAN
- **VPN on WAN2:** VPN tunnel re-establishes over WAN2 on failover using the same peer configuration; DPD (Dead Peer Detection) interval 10s to detect and re-key promptly

---

## 5. Administrative Access Restrictions

- SSH and HTTPS management of switches/APs/gateways: **only from VLAN 10 (`10.10.10.0/24`)**
- No management access from VLAN 30, 40, or 50 under any circumstance
- MFA (TOTP or hardware key) required for all admin logins
- Default credentials changed on all devices; firmware patched to latest stable
- SNMP v3 (authPriv) only; community strings disabled

---

## 6. Assumptions

1. Managed switch supports 802.1Q VLAN tagging and per-VLAN ACLs.
2. Edge gateway/firewall supports dual-WAN with configurable health checks and QoS shaping.
3. AP controller supports per-SSID VLAN assignment and client isolation.
4. WireGuard or IPSec IKEv2 available on edge devices.
5. All timings are design targets pending hardware acceptance testing.
