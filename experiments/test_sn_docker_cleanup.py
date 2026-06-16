"""Unit tests for ovs_container teardown/wait helpers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from starrynet import sn_utils  # noqa: E402


class SnDockerCleanupTests(unittest.TestCase):
    def test_ensure_gone_is_noop_when_clean(self):
        with mock.patch.object(sn_utils, "_count_ovs_containers", return_value=0):
            with mock.patch.object(sn_utils, "sn_remove_all_ovs_containers") as rm:
                sn_utils.sn_ensure_ovs_containers_gone(None)
                rm.assert_not_called()

    def test_ensure_gone_removes_leftovers(self):
        with mock.patch.object(
            sn_utils, "_count_ovs_containers", side_effect=[3, 0],
        ):
            with mock.patch.object(
                sn_utils, "sn_remove_all_ovs_containers",
            ) as rm:
                sn_utils.sn_ensure_ovs_containers_gone(None)
                rm.assert_called_once()

    def test_docker_run_retries_on_bridge_conflict(self):
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        conflict = mock.Mock(
            returncode=1,
            stdout="",
            stderr=(
                "endpoint with name ovs_container_7 already exists in network bridge"
            ),
        )
        cleanup = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            sn_utils, "sn_run_local_cmd", side_effect=[conflict, cleanup, ok],
        ) as run_cmd:
            with mock.patch.object(sn_utils.time, "sleep"):
                rc, err = sn_utils._docker_run_ovs_container(7)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertEqual(run_cmd.call_count, 3)


if __name__ == "__main__":
    unittest.main()
