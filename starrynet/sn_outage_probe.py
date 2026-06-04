"""
Container-side continuous reachability probe for outage/recovery timing.

The emulation loop runs control-plane events inline; an SDN route install can
block the Python loop for several seconds, during which a tick-scheduled ping
would only run *after* the install finished -- hiding the real data-plane
outage. To measure outage honestly and identically for OSPF and SDN, we launch
a high-rate `ping -D -O` *inside the source container* before emulation starts.
It keeps probing on the host clock regardless of what the Python loop is doing,
and `-D` timestamps every line (replies and `no answer yet` loss markers) in
epoch seconds -- the same clock as the snapshot wall_start/wall_end fields, so
outage analysis can align data-plane recovery to each control-plane event.
"""

from __future__ import annotations

import os
from typing import Optional

from starrynet.sn_sdn_adapter import resolve_dest_ip
from starrynet.sn_utils import sn_get_container_info, sn_remote_cmd

_REMOTE_PATH = "/tmp/outage_probe.txt"


def start_outage_probe(sn, src: int, des: int, *, hz: int = 10) -> Optional[dict]:
    """Start a detached high-rate ping src->des inside the source container.

    Returns probe metadata (or None if it could not be started). The probe runs
    until collected/teardown; resolution is ~1/hz seconds.
    """
    try:
        dest_ip = resolve_dest_ip(sn, des)
        cid = str(sn_get_container_info(sn.remote_ssh)[src - 1])
    except Exception as exc:  # pragma: no cover - best effort instrumentation
        print(f"[OUTAGE] probe not started ({src}->{des}): {exc}")
        return None

    interval = max(0.01, 1.0 / float(hz))
    # -D: epoch timestamp per line; -O: emit a timestamped marker for each
    # missing reply (so losses are visible even without seq-gap inference);
    # -n: no DNS. No deadline: it runs until teardown removes the container.
    inner = (
        f"rm -f {_REMOTE_PATH}; "
        f"ping -D -O -n -i {interval} {dest_ip} > {_REMOTE_PATH} 2>&1"
    )
    sn_remote_cmd(
        sn.remote_ssh,
        f"docker exec -d {cid} sh -c \"{inner}\"",
    )
    print(
        f"[OUTAGE] probe started {src}->{des} ({dest_ip}) "
        f"@ {hz}Hz inside {cid}"
    )
    return {"src": src, "des": des, "dest_ip": dest_ip, "cid": cid, "hz": hz}


def collect_outage_probe(sn, probe: Optional[dict]) -> Optional[str]:
    """Copy the probe log out of the container before teardown; stop the ping.

    Writes outage-<src>-<des>.txt in the artifact dir and returns its path.
    """
    if not probe:
        return None
    cid = probe["cid"]
    src, des = probe["src"], probe["des"]
    lines = sn_remote_cmd(sn.remote_ssh, f"docker exec -i {cid} cat {_REMOTE_PATH}")
    # Best-effort stop so the ping doesn't linger if teardown is slow.
    sn_remote_cmd(sn.remote_ssh, f"docker exec -i {cid} sh -c \"pkill ping || true\"")

    out_dir = os.path.join(sn.configuration_file_path, sn.file_path)
    out_path = os.path.join(out_dir, f"outage-{src}-{des}.txt")
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"[OUTAGE] probe collected {src}->{des}: {len(lines)} lines -> {out_path}")
    return out_path
