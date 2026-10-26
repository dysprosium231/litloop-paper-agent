from __future__ import annotations

from litloop.download import progress as progress_reporter


def test_manual_attention_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))

    progress_reporter.start_task("文献下载", total=3)
    assert progress_reporter.read_state()["attention"] == {}

    progress_reporter.set_attention(
        "publisher:a",
        "请在浏览器窗口完成安全验证",
        current="10.1000/a",
        phase="人工验证",
    )
    progress_reporter.set_attention(
        "publisher:b",
        "请在浏览器窗口完成安全验证",
        current="10.1000/b",
        phase="人工验证",
    )
    state = progress_reporter.read_state()
    assert set(state["attention"]) == {"publisher:a", "publisher:b"}
    assert state["attention"]["publisher:b"]["current"] == "10.1000/b"

    progress_reporter.advance(True, current="10.1000/c")
    assert progress_reporter.read_state()["done"] == 1

    progress_reporter.clear_attention("publisher:a")
    assert set(progress_reporter.read_state()["attention"]) == {"publisher:b"}
    progress_reporter.finish()
    state = progress_reporter.read_state()
    assert state["status"] == "done"
    assert state["attention"] == {}


def test_challenge_wait_publishes_and_clears_attention(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    progress_reporter.start_task("文献下载", total=1)

    from litloop.download import fetcher

    class FakePage:
        def __init__(self):
            self.calls = 0
            self.attention_seen = False

        def title(self):
            self.calls += 1
            if self.calls == 1:
                return "请稍候..."
            self.attention_seen = bool(progress_reporter.read_state()["attention"])
            return "article"

    page = FakePage()
    monkeypatch.setattr(fetcher.time, "sleep", lambda _seconds: None)
    fetcher._wait_for_challenge(page, max_tries=2, current="10.1000/a")

    assert page.attention_seen
    assert progress_reporter.read_state()["attention"] == {}


def test_attention_keeps_the_time_it_first_appeared(tmp_path, monkeypatch):
    """A waiting source repeats its notice on every poll; "since" must stay put
    (2026-10-19: it moved, which made a prompt notice look 36 s late)."""
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    progress_reporter.start_task("文献下载", total=1)
    progress_reporter.set_attention("wait:10.1/x", "请登录", current="10.1/x")
    first = progress_reporter.read_state()["attention"]["wait:10.1/x"]["since"]
    import time
    time.sleep(1.1)
    progress_reporter.set_attention("wait:10.1/x", "请登录", current="10.1/x")
    assert progress_reporter.read_state()["attention"]["wait:10.1/x"]["since"] == first


def test_the_app_sees_a_download_another_process_runs(tmp_path, monkeypatch):
    """The agent's or the command line's download shows in the app; the app's own
    report, a finished one and one whose writer died do not."""
    import os
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    progress_reporter.start_task("出版商下载 · Elsevier", total=2)
    assert progress_reporter.running_elsewhere() is None  # this process's own
    state = progress_reporter.read_state()
    write = progress_reporter._atomic_write
    write({**state, "pid": os.getppid()})  # the parent: alive, not us
    assert progress_reporter.running_elsewhere()["task"] == "出版商下载 · Elsevier"
    monkeypatch.setattr(progress_reporter, "_pid_alive", lambda pid: False)
    assert progress_reporter.running_elsewhere() is None  # its writer died
    monkeypatch.undo()
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    write({**state, "pid": os.getppid(), "status": "done"})
    assert progress_reporter.running_elsewhere() is None


def test_latest_notice_keeps_finished_reports_and_drops_dead_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    assert progress_reporter.latest_notice() is None

    progress_reporter.start_task("文献下载", total=2)
    progress_reporter.set_attention("wait:10.1000/a", "请在弹出的浏览器窗口中登录学校账号")
    notice = progress_reporter.latest_notice()
    assert notice["status"] == "running"
    assert set(notice["attention"]) == {"wait:10.1000/a"}
    assert "pid" not in notice

    progress_reporter.advance(True)
    progress_reporter.finish()
    notice = progress_reporter.latest_notice()
    assert (notice["status"], notice["success"], notice["total"]) == ("done", 1, 2)
    assert notice["finished_at"]

    # A running report whose process has died is not a download any more.
    progress_reporter.start_task("文献下载", total=1, pid=999999)
    monkeypatch.setattr(progress_reporter, "_pid_alive", lambda pid: False)
    assert progress_reporter.latest_notice() is None
