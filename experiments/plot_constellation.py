#!/usr/bin/env python3
"""
Render a paper-style 3D view of the emulated 10×10 LEO constellation over Earth.

Uses the same Walker-style SGP4 propagation as starrynet.sn_observer.Observer
(550 km, 53° inclination, +Grid ISLs). Outputs PNG/PDF under --out-dir.

Usage:
  python experiments/plot_constellation.py
  python experiments/plot_constellation.py --orbits 10 --sats 10 --out-dir ./figures
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datetime import datetime

from sgp4.api import Satrec, WGS84
from skyfield.api import load, wgs84, EarthSatellite

# Ground stations shown in the paper figure (illustrative endpoints).
_DEFAULT_GS_LL = (
    (50.110924, 8.682127),    # Frankfurt, Germany
    (-19.9191, -43.9386),     # Belo Horizonte, Brazil
)

_EARTH_RADIUS_KM = 6371.0


def _lla_to_eci(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
    """Earth-centered Cartesian (km), matching Observer.to_cbf."""
    r = _EARTH_RADIUS_KM + alt_km
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    x = r * math.cos(lat) * math.cos(lon)
    y = r * math.cos(lat) * math.sin(lon)
    z = r * math.sin(lat)
    return np.array([x, y, z], dtype=float)


def propagate_constellation(
    *,
    orbits: int,
    sats_per_orbit: int,
    altitude_km: float = 550.0,
    inclination_deg: float = 53.0,
    time_index: int = 0,
) -> np.ndarray:
    """Return (N, 3) satellite positions in km at one second index."""
    ts = load.timescale()
    since = datetime(1949, 12, 31, 0, 0, 0)
    start = datetime(2020, 1, 1, 0, 0, 0)
    epoch = (start - since).days
    inclination = inclination_deg * 2 * np.pi / 360
    gm = 3.9860044e14
    r_earth = 6371393
    altitude_m = altitude_km * 1000
    mean_motion = math.sqrt(gm / (r_earth + altitude_m) ** 3) * 60
    num_sat = orbits * sats_per_orbit
    f_phase = 18

    duration = max(time_index + 1, 1)
    positions = np.zeros((num_sat, 3), dtype=float)

    for i in range(orbits):
        raan = i / orbits * 2 * math.pi
        for j in range(sats_per_orbit):
            sat_idx = i * sats_per_orbit + j
            mean_anomaly = (
                (j * 360 / sats_per_orbit + i * 360 * f_phase / num_sat) % 360
            ) * 2 * math.pi / 360
            satrec = Satrec()
            satrec.sgp4init(
                WGS84,
                "i",
                sat_idx,
                epoch,
                2.8098e-05,
                6.969196665e-13,
                0.0,
                0.001,
                0.0,
                inclination,
                mean_anomaly,
                mean_motion,
                raan,
            )
            sat = EarthSatellite.from_satrec(satrec, ts)
            cur = datetime(2022, 1, 1, 1, 0, 0)
            t_ts = ts.utc(*cur.timetuple()[:5], range(duration))
            geocentric = sat.at(t_ts)
            sub = wgs84.subpoint(geocentric)
            positions[sat_idx] = _lla_to_eci(
                sub.latitude.degrees[time_index],
                sub.longitude.degrees[time_index],
                sub.elevation.km[time_index],
            )
    return positions


def _grid_isl_edges(orbits: int, sats_per_orbit: int) -> list[tuple[int, int]]:
    """+Grid ISL peers (intra-orbit + inter-orbit), matching Observer.access_P_L."""
    edges: list[tuple[int, int]] = []
    for i in range(orbits):
        for j in range(sats_per_orbit):
            a = i * sats_per_orbit + j
            b = i * sats_per_orbit + (j + 1) % sats_per_orbit
            c = ((i + 1) % orbits) * sats_per_orbit + j
            edges.append((a, b))
            edges.append((a, c))
    return edges


def _view_rotation(elev_deg: float, azim_deg: float) -> np.ndarray:
    """Rotation matrix matching matplotlib mplot3d view_init(elev, azim)."""
    elev = math.radians(elev_deg)
    azim = math.radians(azim_deg)
    ce, se = math.cos(elev), math.sin(elev)
    ca, sa = math.cos(azim), math.sin(azim)
    rz = np.array([[ca, -sa, 0.0], [sa, ca, 0.0], [0.0, 0.0, 1.0]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, ce, -se], [0.0, se, ce]])
    return rx @ rz


def _project(xyz: np.ndarray, rot: np.ndarray) -> np.ndarray:
    """Orthographic projection: (N, 3) -> (N, 3) in view space."""
    return xyz @ rot.T


def _sphere_mesh(res: int = 56) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    u = np.linspace(0, 2 * np.pi, res)
    v = np.linspace(0, np.pi, res)
    x = _EARTH_RADIUS_KM * np.outer(np.cos(u), np.sin(v))
    y = _EARTH_RADIUS_KM * np.outer(np.sin(u), np.sin(v))
    z = _EARTH_RADIUS_KM * np.outer(np.ones_like(u), np.cos(v))
    return x, y, z


def _plot_front_arc(
    ax,
    pv: np.ndarray,
    *,
    color: str,
    alpha: float,
    linewidth: float,
    zorder: int,
) -> None:
    """Draw a 3D polyline projected to 2D, clipped at the limb (z = 0)."""
    for k in range(len(pv) - 1):
        x0, y0, z0 = pv[k]
        x1, y1, z1 = pv[k + 1]
        if z0 < 0.0 and z1 < 0.0:
            continue
        if z0 >= 0.0 and z1 >= 0.0:
            ax.plot([x0, x1], [y0, y1], color=color, alpha=alpha,
                    linewidth=linewidth, zorder=zorder, solid_capstyle="round")
            continue
        if z0 == z1:
            continue
        t = z0 / (z0 - z1)
        xc = x0 + t * (x1 - x0)
        yc = y0 + t * (y1 - y0)
        if z0 >= 0.0:
            ax.plot([x0, xc], [y0, yc], color=color, alpha=alpha,
                    linewidth=linewidth, zorder=zorder, solid_capstyle="round")
        else:
            ax.plot([xc, x1], [yc, y1], color=color, alpha=alpha,
                    linewidth=linewidth, zorder=zorder, solid_capstyle="round")


def _draw_earth(ax, rot: np.ndarray) -> None:
    """Blue sphere + sparse wireframe grid (matches original mplot3d styling)."""
    from matplotlib.patches import Circle

    earth = Circle(
        (0.0, 0.0),
        _EARTH_RADIUS_KM,
        facecolor="#1a4a7a",
        edgecolor="#0d2d4d",
        linewidth=0.35,
        alpha=0.58,
        zorder=3,
    )
    ax.add_patch(earth)

    ex, ey, ez = _sphere_mesh(56)
    stride = 8
    for i in range(0, ex.shape[0], stride):
        pts = np.stack([ex[i, :], ey[i, :], ez[i, :]], axis=1)
        _plot_front_arc(
            ax, _project(pts, rot),
            color="#0d2d4d", alpha=0.12, linewidth=0.3, zorder=4,
        )
    for j in range(0, ex.shape[1], stride):
        pts = np.stack([ex[:, j], ey[:, j], ez[:, j]], axis=1)
        _plot_front_arc(
            ax, _project(pts, rot),
            color="#0d2d4d", alpha=0.12, linewidth=0.3, zorder=4,
        )


def render_constellation(
    *,
    orbits: int,
    sats_per_orbit: int,
    altitude_km: float,
    inclination_deg: float,
    gs_lat_lon: tuple[tuple[float, float], ...],
    out_dir: str,
    stem: str,
    dpi: int,
    show_isl: bool,
) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit(
            "matplotlib is required. Install with: pip install matplotlib"
        ) from exc

    sat_xyz = propagate_constellation(
        orbits=orbits,
        sats_per_orbit=sats_per_orbit,
        altitude_km=altitude_km,
        inclination_deg=inclination_deg,
    )
    gs_xyz = np.array(
        [_lla_to_eci(lat, lon, 0.0) for lat, lon in gs_lat_lon],
        dtype=float,
    )

    n_sats = orbits * sats_per_orbit
    # Two lines keep the (long) title within the compact figure width so it is
    # not clipped on the left or right when the canvas is cropped on save.
    title_plain = (
        f"{orbits}×{sats_per_orbit} LEO constellation\n"
        f"({n_sats} satellites, {altitude_km:.0f} km, "
        f"{inclination_deg:.0f}° inclination)"
    )

    elev, azim = 16.0, -52.0
    rot = _view_rotation(elev, azim)
    sat_v = _project(sat_xyz, rot)
    gs_v = _project(gs_xyz, rot)

    shell_r = float(np.max(np.linalg.norm(sat_xyz, axis=1)))
    pad = shell_r * 0.04

    # Figure width tracks the circular plot to avoid side whitespace.
    fig_w, fig_h = 4.4, 2.55
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor="white")
    ax_h = 0.72
    ax_w = ax_h * (fig_h / fig_w)
    ax_left = (1.0 - ax_w) / 2.0
    ax = fig.add_axes([ax_left, 0.16, ax_w, ax_h])
    ax.set_aspect("equal")
    ax.set_axis_off()

    if show_isl:
        edges = _grid_isl_edges(orbits, sats_per_orbit)
        for a, b in edges:
            seg = _project(np.vstack([sat_xyz[a], sat_xyz[b]]), rot)
            depth = float(seg[:, 2].mean())
            if depth < -_EARTH_RADIUS_KM * 0.15:
                continue
            ax.plot(
                seg[:, 0], seg[:, 1],
                color="#7eb8da", alpha=0.35, linewidth=0.45, zorder=2,
            )

    _draw_earth(ax, rot)

    front = sat_v[:, 2] >= -_EARTH_RADIUS_KM * 0.05
    ax.scatter(
        sat_v[front, 0], sat_v[front, 1],
        s=14, c="#ffb347", edgecolors="#ffffff", linewidths=0.25,
        zorder=5,
    )
    gs_front = gs_v[:, 2] >= -_EARTH_RADIUS_KM * 0.05
    ax.scatter(
        gs_v[gs_front, 0], gs_v[gs_front, 1],
        s=52, c="#2ecc71", marker="^", edgecolors="#1a1a1a", linewidths=0.4,
        zorder=6,
    )

    lim = shell_r + pad
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)

    fig.text(0.5, 0.99, title_plain, ha="center", va="top", fontsize=11.0)
    fig.legend(
        handles=[
            plt.Line2D(
                [0], [0], marker="o", color="w", markerfacecolor="#ffb347",
                markersize=7, markeredgecolor="#ffffff", markeredgewidth=0.25,
                label="Satellites",
            ),
            plt.Line2D(
                [0], [0], marker="^", color="w", markerfacecolor="#2ecc71",
                markersize=9, markeredgecolor="#1a1a1a", markeredgewidth=0.4,
                label="Ground station (Belo Horizonte)",
            ),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
        ncol=2,
        fontsize=10.0,
        frameon=False,
        handletextpad=0.4,
        columnspacing=1.2,
    )

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for ext in ("png", "pdf"):
        path = os.path.join(out_dir, f"{stem}.{ext}")
        fig.savefig(
            path,
            dpi=dpi if ext == "png" else None,
            facecolor="white",
            bbox_inches="tight",
            pad_inches=0.05,
        )
        paths.append(path)
    plt.close(fig)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot Earth + Walker LEO constellation (StarryNet parameters)")
    parser.add_argument("--orbits", type=int, default=10)
    parser.add_argument("--sats", type=int, default=10,
                        help="Satellites per orbital plane")
    parser.add_argument("--altitude-km", type=float, default=550.0)
    parser.add_argument("--inclination", type=float, default=53.0)
    parser.add_argument("--out-dir", default=os.path.join(_ROOT, "figures"))
    parser.add_argument("--stem", default=None,
                        help="Output basename (default: constellation_OxS)")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--no-isl", action="store_true",
                        help="Omit inter-satellite link lines")
    args = parser.parse_args()

    stem = args.stem or f"constellation_{args.orbits}x{args.sats}"
    paths = render_constellation(
        orbits=args.orbits,
        sats_per_orbit=args.sats,
        altitude_km=args.altitude_km,
        inclination_deg=args.inclination,
        gs_lat_lon=_DEFAULT_GS_LL,
        out_dir=args.out_dir,
        stem=stem,
        dpi=args.dpi,
        show_isl=not args.no_isl,
    )
    for p in paths:
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
