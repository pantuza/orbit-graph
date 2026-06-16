# Paper scale results

Aggregated CSVs for the journal scaling study (5×5 through 10×10).

Run **one grid size at a time** (10 reps each), then plot when all sizes are done:

```bash
make paper-clean          # optional: wipe CSVs only
make paper-5x5            # first size (no --append)
make paper-6x6            # appends into this directory
# … 7x7, 8x8, 9x9, 10x10
make paper-plots          # figures → ./figures
```

CSV outputs (`ping_raw.csv`, `outage_summary.csv`, …) are gitignored; this README is tracked.

See `simulation_paper.json` for durations and `make help` for `paper-*` targets.
