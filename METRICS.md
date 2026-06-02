# Metrics we analyze (OSPF vs SDN)

This file defines the **paper-facing** metrics we will report from StarryNet runs, and how to interpret them fairly.

## Primary outcome metrics (data plane)

| Metric | How we compute it | Why it matters |
|---|---|---|
| **Ping loss (%)** | Parse `ping-26-27_<t>.txt` in the artifact dir (e.g. `loss=0%`) | End-to-end reachability and stability. |
| **Ping RTT avg (ms)** | Parse `rtt min/avg/max/mdev` line in `ping-26-27_<t>.txt` | User-visible latency; validates path quality under the selected policy. |
| **Path / hop sequence (qualitative)** | `trace-26-27_<tag>.txt` (ICMP traceroute) and `route-get-26-27_<tag>.txt` | Confirms the path matches expectations (e.g. GS→sat→sat→GS), helps debug regressions. |
| **Ping after topology change (handover-relative)** | `ping-<gs1>-<gs2>_<t>.txt` at the handover tick + 2s after (full profile) | Reachability immediately after the GSL handover / `topology_change` event. The handover tick is **geometry-dependent** (t=53 for 5x5, t=23 for 6x6, ...), read per run from `Topo_leo_change.txt`, so we ping relative to it instead of a fixed time. |

## Control-plane metrics (routing events vs steady state)

### SDN (central controller + kernel static routes)

| Metric | Source | Interpretation |
|---|---|---|
| **`recompute_ms` (routing event)** | `sdn_metrics/snapshot_*_{init,damage_recovery,topology_change}.json` | Time to load topology, compute FIB, and push routes for **events that can change forwarding**. |
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
| `control_summary_by_reason.csv` | Per (mode, `reason`): `time_ms` and `installed` pooled across time indices. Use this for seed-dependent events (e.g. all `delay_update` FIB changes) so they form one stable bucket. |

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

