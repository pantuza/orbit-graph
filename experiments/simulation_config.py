#!/usr/bin/env python3
"""
Load scale-experiment plans from simulation.json.

Used by compare_batch.py when ``--simulation`` is passed (``make scale``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ConstellationSpec:
    orbits: int
    sats: int
    duration: Optional[int]
    size_label: str


@dataclass(frozen=True)
class SimulationPlan:
    reps: int
    out_dir: str
    profile: str
    base_seed: int
    modes: List[str]
    constellations: List[ConstellationSpec]
    source_path: str

    def size_list(self) -> List[Tuple[int, int]]:
        return [(c.orbits, c.sats) for c in self.constellations]

    def duration_by_size(self) -> Dict[Tuple[int, int], int]:
        return {
            (c.orbits, c.sats): c.duration
            for c in self.constellations
            if c.duration is not None
        }


def _parse_size(token: str) -> Tuple[int, int, str]:
    text = token.strip().lower()
    if "x" not in text:
        raise ValueError(f"invalid size '{token}' (expected OxS, e.g. 6x6)")
    o, s = text.split("x", 1)
    return int(o), int(s), text


def _require_mapping(data: object, path: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object, got {type(data).__name__}")
    return data


def load_simulation_plan(path: str, root: str) -> SimulationPlan:
    """
    Parse simulation.json into a SimulationPlan.

    ``root`` is the repository root; relative ``out_dir`` values resolve there.
    """
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Simulation plan not found: {abs_path}")

    with open(abs_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    data = _require_mapping(raw, abs_path)

    reps = int(data.get("reps", 5))
    if reps < 1:
        raise ValueError(f"{abs_path}: reps must be >= 1 (got {reps})")

    out_dir = str(data.get("out_dir", "./scale_results"))
    if not os.path.isabs(out_dir):
        out_dir = os.path.normpath(os.path.join(root, out_dir))

    profile = str(data.get("profile", "full"))
    if profile not in ("basic", "full"):
        raise ValueError(f"{abs_path}: profile must be 'basic' or 'full'")

    base_seed = int(data.get("base_seed", 1000))

    modes_raw = data.get("modes", ["ospf", "sdn"])
    if not isinstance(modes_raw, list) or not modes_raw:
        raise ValueError(f"{abs_path}: modes must be a non-empty list")
    modes = [str(m).strip().lower() for m in modes_raw]
    for mode in modes:
        if mode not in ("ospf", "sdn"):
            raise ValueError(f"{abs_path}: unknown mode '{mode}'")

    entries = data.get("constellations")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{abs_path}: constellations must be a non-empty list")

    constellations: List[ConstellationSpec] = []
    seen: set[Tuple[int, int]] = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{abs_path}: constellations[{idx}] must be an object")
        if "size" not in entry:
            raise ValueError(
                f"{abs_path}: constellations[{idx}] missing required 'size'")
        orbits, sats, label = _parse_size(str(entry["size"]))
        key = (orbits, sats)
        if key in seen:
            raise ValueError(f"{abs_path}: duplicate constellation size {label}")
        seen.add(key)

        duration: Optional[int] = None
        if "duration" in entry and entry["duration"] is not None:
            duration = int(entry["duration"])
            if duration < 10:
                raise ValueError(
                    f"{abs_path}: {label} duration must be >= 10s "
                    f"(got {duration})")

        constellations.append(
            ConstellationSpec(orbits, sats, duration, label))

    return SimulationPlan(
        reps=reps,
        out_dir=out_dir,
        profile=profile,
        base_seed=base_seed,
        modes=modes,
        constellations=constellations,
        source_path=abs_path,
    )


def print_plan(plan: SimulationPlan) -> None:
    print(f"Simulation plan: {plan.source_path}")
    print(f"  reps={plan.reps} profile={plan.profile} "
          f"base_seed={plan.base_seed} modes={','.join(plan.modes)}")
    print(f"  out_dir={plan.out_dir}")
    print("  constellations:")
    for c in plan.constellations:
        dur = f"{c.duration}s" if c.duration is not None else "config default"
        print(f"    {c.size_label}: duration={dur}")
