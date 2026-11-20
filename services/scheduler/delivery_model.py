"""
services/scheduler/delivery_model.py

Capacity and latency model for relay-and-forward.

Reachability alone ("can this satellite be served?") says nothing about how
much data reaches the ground or how old it is when it does. This module
simulates that with a fluid, FIFO queue model.

Model
-----
* Every satellite generates data at `gen_mbps`.
* A scheduled contact window lets its satellite downlink at `ground_mbps`.
  The contact satellite sends its own queue first, then data relayed from
  other satellites, oldest data first.
* A satellite with no active window of its own can push data over ISL to a
  contact satellite reachable within `max_hops` hops, at `isl_mbps` per
  satellite, and a contact satellite accepts at most `isl_mbps` of relayed
  data (one ISL terminal). Each dark satellite is served by one contact
  satellite per step: the one with the fewest hops, then the most spare
  ground capacity.
* ISL capacity on intermediate hops is not contended (optimistic).

Outputs are MB-weighted: delivered share, mean and 95th-percentile latency
(generation to ground delivery), end-of-horizon backlog and the share of
delivered volume that travelled by relay.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from services.scheduler.window_metadata import hop_reach

MBPS_TO_MB_PER_S = 1.0 / 8.0


@dataclass
class DeliveryResult:
    generated_mb:   float = 0.0
    delivered_mb:   float = 0.0
    relayed_mb:     float = 0.0
    backlog_mb:     float = 0.0
    latencies_s:    list = field(default_factory=list)   # (latency_s, mb)
    by_class:       dict = field(default_factory=dict)

    @property
    def delivered_pct(self) -> float:
        return 100.0 * self.delivered_mb / self.generated_mb if self.generated_mb else 0.0

    @property
    def relay_share_pct(self) -> float:
        return 100.0 * self.relayed_mb / self.delivered_mb if self.delivered_mb else 0.0

    def latency_mean_min(self, sat_filter=None) -> float:
        pairs = self._pairs(sat_filter)
        w = sum(m for _, m in pairs)
        return sum(l * m for l, m in pairs) / w / 60.0 if w else float('nan')

    def latency_p95_min(self, sat_filter=None) -> float:
        pairs = sorted(self._pairs(sat_filter))
        w = sum(m for _, m in pairs)
        if not w:
            return float('nan')
        acc = 0.0
        for l, m in pairs:
            acc += m
            if acc >= 0.95 * w:
                return l / 60.0
        return pairs[-1][0] / 60.0

    def _pairs(self, sat_filter):
        if sat_filter is None:
            return [(l, m) for _, l, m in self.latencies_s]
        return [(l, m) for s, l, m in self.latencies_s if sat_filter(s)]


def simulate_delivery(
    satellite_ids: list[str],
    scheduled_windows: list,
    adjacency_at,                 # callable(unix) -> {sat: {neighbours}}
    t0: float,
    n_steps: int,
    dt: float = 60.0,
    gen_mbps: float = 2.0,
    ground_mbps: float = 10.0,
    isl_mbps: float = 100.0,
    max_hops: int = 3,
    relay: bool = True,
) -> DeliveryResult:
    """Run the fluid delivery model over n_steps of dt seconds."""
    queues = {s: deque() for s in satellite_ids}       # (gen_time, mb)
    res = DeliveryResult()
    gen_mb = gen_mbps * MBPS_TO_MB_PER_S * dt
    ground_cap = ground_mbps * MBPS_TO_MB_PER_S * dt
    isl_cap = isl_mbps * MBPS_TO_MB_PER_S * dt

    def head_age(sat, now):
        q = queues[sat]
        return now - q[0][0] if q else -1.0

    def drain(sat, mb, now, via_relay):
        """Remove up to mb from the front of sat's queue; returns MB removed."""
        q, done = queues[sat], 0.0
        while q and done < mb - 1e-12:
            gt, m = q[0]
            take = min(m, mb - done)
            res.delivered_mb += take
            if via_relay:
                res.relayed_mb += take
            res.latencies_s.append((sat, now + dt - gt, take))
            done += take
            if take >= m - 1e-12:
                q.popleft()
            else:
                q[0] = (gt, m - take)
        return done

    for k in range(n_steps):
        t = t0 + k * dt
        for s in satellite_ids:                       # generation
            queues[s].append((t, gen_mb))
            res.generated_mb += gen_mb

        active = [w for w in scheduled_windows if w.aos_unix <= t < w.los_unix]
        spare = {}
        contact_sats = set()
        for w in active:                              # own queue first
            c = w.satellite_id
            if c in contact_sats:
                continue
            contact_sats.add(c)
            sent = drain(c, ground_cap, t, via_relay=False)
            spare[c] = ground_cap - sent

        if relay and contact_sats:
            adj = adjacency_at(t)
            reach = {c: hop_reach(adj, c, max_hops) for c in contact_sats}
            intake = {c: isl_cap for c in contact_sats}
            candidates = [s for s in satellite_ids
                          if s not in contact_sats and queues[s]]
            candidates.sort(key=lambda s: -head_age(s, t))     # oldest first
            for s in candidates:
                options = [(reach[c][s], -spare[c], c) for c in contact_sats
                           if s in reach[c] and spare[c] > 1e-12
                           and intake[c] > 1e-12]
                if not options:
                    continue
                _, _, c = min(options)
                amount = min(isl_cap, spare[c], intake[c],
                             sum(m for _, m in queues[s]))
                sent = drain(s, amount, t, via_relay=True)
                spare[c] -= sent
                intake[c] -= sent

    res.backlog_mb = sum(m for q in queues.values() for _, m in q)
    return res
