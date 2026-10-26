"""The progress report file (progress/current.json) the desktop app reads.

The floating progress bar that used to watch it was removed (2026-10-19, the
user's call): the app shows every download, its own and the agent's or the
command line's. What is left: a task start writes the report, and the
liveness probe the app uses to tell a running writer from a crashed one. The
Windows probe must NEVER use os.kill(pid, 0) - there it calls TerminateProcess.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litloop.download import progress as pr


class PidAliveTests(unittest.TestCase):
    def test_own_pid_alive(self):
        self.assertTrue(pr._pid_alive(os.getpid()))

    def test_dead_pid_not_alive(self):
        self.assertFalse(pr._pid_alive(999999))

    def test_zero_and_negative_rejected(self):
        self.assertFalse(pr._pid_alive(0))
        self.assertFalse(pr._pid_alive(-5))


class _TmpProgressDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(pr, "_progress_dir", return_value=Path(self.tmp.name) / "progress")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(pr, "_STATE", {}))

    def state(self):
        return json.loads((Path(self.tmp.name) / "progress" / "current.json").read_text(encoding="utf-8"))


class StartTaskTests(_TmpProgressDir):
    def test_start_task_writes_state_and_opens_no_window(self):
        with patch("subprocess.Popen") as popen:
            pr.start_task("文献下载", total=10)
        popen.assert_not_called()
        state = self.state()
        self.assertEqual((state["task"], state["total"], state["pid"]), ("文献下载", 10, os.getpid()))


class SetOutputDirTests(_TmpProgressDir):
    def test_set_output_dir_persists_to_state_file(self):
        pr.start_task("文献下载", total=5)
        pr.set_output_dir(r"D:\litloop-deliverables\L3-Elsevier")
        self.assertEqual(self.state()["output_dir"], r"D:\litloop-deliverables\L3-Elsevier")

    def test_set_output_dir_before_task_is_ignored(self):
        pr.set_output_dir(r"D:\nowhere")  # 没有活动任务时不产生状态
        self.assertFalse((Path(self.tmp.name) / "progress" / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
