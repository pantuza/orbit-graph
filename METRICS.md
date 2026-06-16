# Metrics we analyze (OSPF vs SDN)

This file defines the **paper-facing** metrics we will report from StarryNet runs, and how to interpret them fairly.

## Primary outcome metrics (data plane)

| Metric | How we compute it | Why it matters |
|---|---|---|
| **Ping loss (%)** | Parse `ping-26-27_<t>.txt` in the artifact dir (e.g. `loss=0%`) | End-to-end reachability and stability. |
| **Ping RTT avg (ms)** | Parse `rtt min/avg/max/mdev` line in `ping-26-27_<t>.txt` | User-visible latency; validates path quality under the selected policy. |
| **Path / hop sequence (qualitative)** | `trace-26-27_<tag>.txt` (ICMP traceroute) and `route-get-26-27_<tag>.txt` | Confirms the path matches expectations (e.g. GS→sat→sat→GS), helps debug regressions. |
| **Ping after topology change (handover-relative)** | `ping-<gs1>-<gs2>_<t>.txt` at the handover tick + 2s after (full profile) | Reachability immediately after the GSL handover / `topology_change` event. The handover tick is **geometry-dependent** (t=53 for 5x5, t=23 for 6x6, ...), read per run from `Topo_leo_change.txt`, so we ping relative to it instead of a fixed time. |

## Data-plane outage / recovery time (the fair convergence metric)

The synchronous emulation loop runs control-plane events inline, and an SDN
route install can **block the loop for several seconds**. A tick-scheduled ping
therefore only runs *after* the install finishes and **hides the real outage**.
To measure convergence honestly and identically for both protocols, a
high-rate `ping -D -O` runs **inside the source GS container** for the whole
run (`outage-<gs1>-<gs2>.txt`), timestamped on the host clock. Each control
snapshot records `wall_start` (epoch); we measure **outage = duration of the
sustained loss burst triggered near the event** (onset → first sustained
recovery).

**Loss definition (important):** a packet counts as *lost* only if its
`icmp_seq` **never** gets a `bytes from` reply (or returns ICMP *Unreachable*).
`ping -O`'s `"no answer yet for icmp_seq=N"` is **not** loss — with a sub-RTT
probe interval the kernel prints it for every in-flight packet that then
replies, so treating it as loss makes every outage read as ~0 ms. We also
require a **sustained** burst (≥3 consecutive losses ≈ 300 ms) to mark onset and
≥3 consecutive replies to mark recovery, so isolated jitter drops/late-reply
blips near an event don't short-circuit a real multi-second outage.

| Metric | Source | Why it matters |
|---|---|---|
| **`outage_ms` per event** | `outage-*.txt` probe aligned to snapshot `wall_start` (see `experiments/outage.py`) | The honest, symmetric convergence metric: real data-plane downtime after damage/recovery and topology change, for OSPF and SDN alike. This is the headline. |
| **`still_down`** | derived | The probe never recovered before teardown (path stayed black-holed). |

> Example (6×6 GSL handover, seed 1001): **OSPF black-holed ~5.2 s** during
> reconvergence (ICMP Net-Unreachable then 50 dropped probes, RTT collapsing
> 240 ms → 27 ms on the new path), while **SDN had 0 sustained outage** — its
> atomic `ip route replace` preserved forwarding even though the control-plane
> install *blocked the loop* for 6.4 s (of which compute was only ~2.3 ms).

> Note: OSPF "convergence" is reported as this data-plane recovery (we don't
> trust BIRD's dump time as convergence). For SDN, recovery includes the
> docker-exec install cost — which motivates installing *deltas* and the
> proactive (make-before-break) SDN mode.

### Incremental, make-before-break install (Phase 2)

A real SDN controller ships **deltas**, not the whole table, and orders updates
so a destination is never without a route. We model both:

- **Incremental (default, `incremental_install=True`)**: on a routing event we
  re-scan addresses to learn the new topology but **keep the installed-route
  baseline**, then push only the next-hops that changed. Per node we issue all
  `ip route replace` (add/update) commands **before** any `ip route del`
  (make-before-break): `replace` is atomic, so a rerouted destination is updated
  in place and its stale `del` becomes a harmless no-op. This collapses
  `install_ms` from "push 1500+ routes" to "push the few that moved" and keeps
  data-plane `outage_ms` at/near zero.
- **Full reinstall baseline (`SDN_FULL_REINSTALL=1`)**: discards the baseline and
  re-pushes the entire FIB on every event — the naive approach. Useful as a
  paper comparison point to quantify the cost of *not* doing incremental updates
  (large `install_ms`, every node touched).

| Metric | Incremental | Full reinstall |
|---|---|---|
| `install_ms` (topology change) | small (delta only) | large (whole table) |
| `nodes_touched` | few (handover-affected) | all nodes |
| `compute_ms` | unchanged (~2–3 ms) | unchanged (~2–3 ms) |
| `outage_ms` | ~0 (make-before-break) | ~0 (atomic replace) |

Report `compute_ms` as the inherent SDN control cost; report incremental
`install_ms`/`nodes_touched` as the realistic data-plane update cost, and
optionally the full-reinstall numbers to motivate incremental updates.

### Proactive handover (Phase 3)

Operator-style GSL handover: the SDN controller installs the **post-handover
FIB** after new links are created but **before** old links are removed. OSPF
still reconverges only after the full add+del mutation, so this phase is
SDN-only.

| Step | SDN (proactive) | OSPF |
|---|---|---|
| 1. Add new GSL | — | — |
| 2. **Install post-handover routes** | `proactive_handover@t` snapshot | — |
| 3. Delete old GSL | — | — |
| 4. Finalize | `topology_change@t` (no-op if FIB matches) | `topology_change@t` (starts reconvergence) |

| Metric | Source | Why it matters |
|---|---|---|
| **`proactive_handover` snapshot** | `sdn_metrics/snapshot_*_proactive_handover.json` | When SDN pushed routes relative to the link mutation — **before** the old path is torn down. |
| **`topology_change` finalize** | `sdn_metrics/snapshot_*_topology_change.json` with `proactive_finalized=true` | Confirms no extra install was needed after the old GSL dropped. |
| **`outage_ms` at `proactive_handover`** | outage probe + proactive `wall_start` | SDN path ready while both GSLs briefly coexist. Expect **0 ms**. |
| **`outage_ms` at `topology_change`** | outage probe + OSPF `wall_start` | OSPF reconvergence after full mutation. Expect **multi-second** black-hole. |

Disable with `SDN_PROACTIVE_HANDOVER=0` to revert to Phase 2 (install after
add+del). For the paper: compare **OSPF `topology_change` outage** vs **SDN
`proactive_handover` outage** — same handover event, different control-plane
capabilities.

## Control-plane metrics (routing events vs steady state)

### SDN (central controller + kernel static routes)

| Metric | Source | Interpretation |
|---|---|---|
| **`recompute_ms` (routing event)** | `sdn_metrics/snapshot_*_{init,damage_recovery,topology_change}.json` | Total: load topology + compute FIB + push routes. **Reported, but split below for fairness.** |
| **`compute_ms`** | SDN snapshot field | Algorithmic controller cost (graph load + Dijkstra). The defensible "SDN control" number, independent of the install mechanism. |
| **`install_ms`** | SDN snapshot field | Dataplane push cost (per-route `docker exec`). A **harness artifact**, not inherent to SDN; a real OpenFlow/gRPC plane is far faster and delta-only. Report separately; never conflate with `compute_ms`. |
| **`nodes_touched`** | SDN snapshot field | Number of containers that received any route change this event. With incremental install, only nodes whose next-hops changed are touched (small for a single GSL handover); with full reinstall it is every node. |
| **`recompute_ms` (delay tick, steady)** | `sdn_metrics/snapshot_*_delay_update.json` when `fib_unchanged=true` | “Controller monitoring” overhead only (recompute without dataplane push). Should be **low** and separated from routing-event costs. |
| **Routes pushed: `installed`, `deleted`, `failed`** | SDN snapshot JSON fields | Measures the **size of the change** applied to the network after an event. |
| **FIB size: `fib_entries`** | SDN snapshot JSON field | Sanity check: confirms comparable scale between runs; helps normalize results. |
| **`fib_unchanged` (bool)** | SDN snapshot JSON field | Indicates delay-update ticks where next-hops didn’t change (no route push). Useful for steady-state analysis. |
| **`gateways_resolved`** | SDN snapshot JSON field | Edges that required live gateway lookup before route-key build (should be 0 after address refresh). |

### OSPF (BIRD/OSPF baseline)

| Metric | Source | Interpretation |
|---|---|---|
| **`collection_ms`** | `ospf_metrics/snapshot_*.json` | Measurement time for route/daemon dumps (an observation cost). **Not directly comparable** to SDN’s push time. |
| **`bird_route_ok / nodes_dumped`** | OSPF snapshot JSON fields | Verifies successful collection across requested nodes; also a coarse “routing is alive” check. |

## Reporting guidance (how to read results)

- **Primary claim metric**: Compare **loss** and **avg RTT** by **phase** rather than fixed times. The full profile pings at the **handover** instant, **post_handover** (+2s), and a late **steady** tick. Phases are size-agnostic, so they line up across constellation sizes for cross-size charts. This is the fairest dataplane comparison.
- **Control-plane comparison**:
  - Treat **routing events** as: `init`, `damage_recovery`, `topology_change`, and **delay ticks only when the FIB changes**.
  - Treat **steady delay ticks** (delay updates with `fib_unchanged=true`) separately; they represent “monitoring/TE recompute” overhead, not route churn. OSPF does not collect metrics on these ticks (tc-only updates).
- **Avoid apples-to-oranges**:
  - OSPF routing convergence is handled inside BIRD; our `collection_ms` is a dump/observation cost.
  - SDN’s `recompute_ms` includes a controller compute + dataplane push path in this harness.

## Statistical batches (repeated runs)

For paper-grade results, run **N seeded repetitions** per mode and aggregate
(mean ± stddev). Each repetition uses a distinct RNG seed (`base-seed + i`) so
the damaged-link selection is reproducible, while real netem/Docker timing
still varies run to run.

```bash
make batch REPS=10          # ospf + sdn, full profile, 10 reps each
# or:
python experiments/compare_batch.py --reps 10 --modes ospf,sdn --profile full
```

| Output (under `batch_results/`) | Contents |
|---|---|
| `ping_raw.csv` | One row per (mode, rep, ping time tag): `phase`, `loss_pct`, `avg_rtt_ms`. |
| `ping_summary.csv` | Per (nodes, mode, time tag): mean/stddev of loss and RTT, RTT sample count. |
| `ping_summary_by_phase.csv` | Per (nodes, mode, **phase**): handover-relative view (`post_init`, `handover`, `post_handover`, `steady`) that aligns across sizes. Use this for cross-size dataplane charts. |
| `control_raw.csv` | One row per (mode, rep, snapshot): `time_ms`, `reason`, `routing_event`, `installed`, `fib_unchanged`. |
| `control_summary.csv` | Per (mode, routing event `reason@time`): mean/stddev of `time_ms`, plus `n` (samples) and `reps` (distinct reps contributing). |
| `control_summary_by_reason.csv` | Per (mode, `reason`): `time_ms`, `compute_ms`, `install_ms`, `installed` pooled across time indices. Use this for seed-dependent events and to separate SDN compute vs install cost. |
| `outage_raw.csv` | One row per (mode, rep, event): `outage_ms`, `still_down` — data-plane downtime from the continuous probe. |
| `outage_summary.csv` | Per (nodes, mode, `reason`): mean/stddev `outage_ms`. **The headline convergence comparison.** |

**Reporting:** use `ping_summary_by_phase.csv` for the headline loss/RTT claims
(with stddev/error bars), especially across sizes. For control-plane cost:
- **Deterministic-reason events** (`init`, `damage_recovery`, `topology_change`)
  appear in every rep. The exact `topology_change` tick is geometry-dependent,
  so compare by **reason** via `control_summary_by_reason.csv`, or use
  `control_summary.csv` (`reason@time`) **within a single size**.
- **Seed-dependent events** (delay-tick FIB changes land at different times
  per seed) — use `control_summary_by_reason.csv`, which pools all
  `delay_update` changes into one bucket regardless of tick.

Steady delay ticks (`fib_unchanged`) are excluded from both summaries (they
are not routing events); inspect them via `control_raw.csv` if needed.

### Data-quality guard

The batch runner checks each full-profile rep for the expected ping **phases**
(`post_init`, `handover`, `post_handover`, `steady`) and routing **reasons**
(`init`, `damage_recovery`, `topology_change`). Checks are by phase/reason — not
fixed `@t` ticks — because handover timing depends on constellation geometry, so
a hardcoded `@t53` would falsely flag larger grids. Any missing sample is listed
under **DATA-QUALITY WARNINGS** at the end of the run, so silently dropped
events (e.g. a handover that didn't register) are caught instead of hiding in
the `n`/`reps` columns.

## Figures (`make plots`)

After `make batch` or `make scale`, generate PDF/PNG charts from the **summary
CSVs only** (stable filenames in one directory — not per-run artifact folders):

```bash
make scale SIZES=5x5,6x6,8x8 REPS=10
make plots RESULTS=./scale_results FIGDIR=./figures
```

| Output | Source CSV | What it shows |
|---|---|---|
| `outage_vs_nodes` | `outage_summary.csv` | OSPF `topology_change` vs SDN `proactive_handover` outage (s) vs nodes |
| `rtt_by_phase` | `ping_summary_by_phase.csv` | RTT by phase (handover, post_handover, steady, …) vs nodes |
| `control_handover` | `control_summary_by_reason.csv` | OSPF collection vs SDN compute+install at handover |
| `routes_installed_handover` | `control_summary_by_reason.csv` | SDN incremental route push count at handover |

Point `--in-dir` / `RESULTS` at any directory that contains those CSV names
(e.g. `./batch_results` or `./scale_results`). Requires `matplotlib`
(`pip install matplotlib` or `make install-deps`).

## Constellation scaling (vs node count)

To study how metrics scale with constellation size, run the batch across
multiple grids. Each grid `OxS` has `O*S` satellites + 2 ground stations, and
the ground-station endpoints are derived automatically (`GS = O*S+1, O*S+2`).

```bash
make scale SIZES=5x5,6x6,8x8 REPS=5        # -> scale_results/*.csv
# or directly:
python experiments/compare_batch.py --reps 5 --profile full \
    --sizes 5x5,6x6,8x8 --out-dir scale_results
```

Every CSV gains a **`nodes`** column (total nodes = sats + GS) so results can be
plotted against constellation size. Key scaling relationships for the paper:

| X axis | Y axis (from CSV) | Expectation |
|--------|-------------------|-------------|
| `nodes` | SDN `installed_mean` (control_summary_by_reason) | grows ~O(N) host routes |
| `nodes` | SDN `time_ms_mean` per reason | install/compute cost vs size |
| `nodes` | OSPF vs SDN `rtt_mean_ms` by phase (`ping_summary_by_phase`) | does the SDN handover/steady advantage hold as N grows? |

Note: only the **5x5** grid uses the hand-verified path-hop warmup; larger
grids rely on the size-aware GS endpoints and the LeastDelay path that emerges
from the topology. Because the handover tick differs per size, pings are
scheduled **relative to the handover** (read from `Topo_leo_change.txt`) and
reported by **phase**, so handover and steady effects are comparable both within
and **across** sizes.

## Artifact locations (by mode/profile)

- **SDN**: `...-sdn-full/` or `...-sdn-basic/`
  - Metrics: `sdn_metrics/snapshot_*.json`
  - Route tables: `sdn_metrics/route_dumps/*.txt`
- **OSPF**: `...-ospf-full/` or `...-ospf-basic/`
  - Metrics: `ospf_metrics/snapshot_*.json`
  - Route tables: `ospf_metrics/route_dumps/*.txt`

