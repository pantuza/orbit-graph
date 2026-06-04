"""Regression tests for the data-plane outage parser (no Docker required).

These lock in two subtle `ping -D -O` parsing facts that previously made every
outage read as 0 ms:

1. "no answer yet for icmp_seq=N" is NOT a loss. With a sub-RTT probe interval
   the kernel prints it for every in-flight packet that then replies.
2. An isolated single-packet drop near an event must not short-circuit the
   real (sustained) outage burst that follows.
"""

import os
import tempfile

from experiments.outage import event_outage, parse_probe


def _write(lines):
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("PING 9.0.0.1 (9.0.0.1) 56(84) bytes of data.\n")
        fh.write("\n".join(lines) + "\n")
    return path


def test_no_answer_yet_then_reply_is_not_loss():
    # High RTT (~300 ms) at 10 Hz: each seq gets "no answer yet" then a reply.
    lines = []
    for seq in range(1, 11):
        send = 100.0 + seq * 0.1
        lines.append(f"[{send:.6f}] no answer yet for icmp_seq={seq}")
        lines.append(
            f"[{send + 0.3:.6f}] 64 bytes from 9.0.0.1: "
            f"icmp_seq={seq} ttl=60 time=300 ms")
    path = _write(lines)
    try:
        samples = parse_probe(path)
        assert samples, "expected parsed samples"
        assert all(ok for _, ok in samples), "delayed replies are not losses"
        res = event_outage(samples, wall_start=100.5)
        assert res["outage_ms"] == 0.0
        assert res["still_down"] is False
    finally:
        os.remove(path)


def test_isolated_drop_does_not_mask_real_burst():
    lines = []
    # seqs 1..4 ok
    for seq in range(1, 5):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    # seq 5 isolated drop (never replies)
    lines.append("[100.500000] no answer yet for icmp_seq=5")
    # seqs 6..9 ok again
    for seq in range(6, 10):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    # seqs 10..40 sustained outage (no replies)
    for seq in range(10, 41):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] no answer yet for icmp_seq={seq}")
    # seqs 41..45 recover
    for seq in range(41, 46):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    path = _write(lines)
    try:
        samples = parse_probe(path)
        # event at the burst onset (~seq 10 -> t=101.0)
        res = event_outage(samples, wall_start=101.0)
        # burst spans seq 10 (t=101.0) .. recovery seq 41 (t=104.1) ~= 3.1 s,
        # NOT 0 ms from the isolated seq-5 drop.
        assert res["outage_ms"] is not None
        assert 2800.0 <= res["outage_ms"] <= 3300.0, res["outage_ms"]
        assert res["still_down"] is False
    finally:
        os.remove(path)


def test_unreachable_is_loss():
    lines = []
    for seq in range(1, 5):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    for seq in range(5, 20):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] From 9.0.0.9 icmp_seq={seq} "
                     f"Destination Net Unreachable")
    for seq in range(20, 25):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    path = _write(lines)
    try:
        samples = parse_probe(path)
        res = event_outage(samples, wall_start=100.5)
        assert res["outage_ms"] is not None and res["outage_ms"] > 1000.0
        assert res["still_down"] is False
    finally:
        os.remove(path)


def test_still_down_when_never_recovers():
    lines = []
    for seq in range(1, 4):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] 64 bytes from 9.0.0.1: "
                     f"icmp_seq={seq} ttl=60 time=5 ms")
    for seq in range(4, 30):
        t = 100.0 + seq * 0.1
        lines.append(f"[{t:.6f}] no answer yet for icmp_seq={seq}")
    path = _write(lines)
    try:
        samples = parse_probe(path)
        res = event_outage(samples, wall_start=100.4)
        assert res["still_down"] is True
        assert res["outage_ms"] is not None and res["outage_ms"] > 0.0
    finally:
        os.remove(path)


if __name__ == "__main__":
    test_no_answer_yet_then_reply_is_not_loss()
    test_isolated_drop_does_not_mask_real_burst()
    test_unreachable_is_loss()
    test_still_down_when_never_recovers()
    print("ok")
