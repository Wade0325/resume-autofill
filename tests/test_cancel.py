"""分析可以取消、同時丟好幾份會排隊、進度看得到第幾批。

模型換成假的：報三批進度、每批之間檢查取消，中間卡住等測試放行，
這樣才測得到「正在跑的那一批跑完才停」而不是靠運氣。
"""
from __future__ import annotations

import threading
import time

import pytest

from backend import db, service
from backend.core import filler, llm


@pytest.fixture
def fake_model(monkeypatch):
    """把判讀換成假的，回傳 (started, release) 兩個旗標讓測試控制節奏。"""
    started, release = threading.Event(), threading.Event()

    def fake_analyze(blank, profile, host=None, model=None, progress=None, stop=None):
        started.set()
        for i in range(1, 4):
            if stop and stop():
                raise filler.Cancelled("取消")
            if progress:
                progress(f"逐格判讀 第 {i}／3 批")
            release.wait(timeout=5)
        _doc, _form, slots = filler.parse(blank)
        return filler.Draft(slots=slots, fields={}, assignment={}, ticks={})

    monkeypatch.setattr(filler, "analyze", fake_analyze)
    monkeypatch.setattr(llm, "available", lambda host: True)   # 假裝模型開著
    return started, release


@pytest.fixture
def run_job(make_job):
    """開一份還沒分析的工作，並在背景跑 worker。"""
    threads = []

    def _run(filename="虛構.docx"):
        job_id = make_job(filename, status="processing")
        t = threading.Thread(target=service._vlm_worker, args=(job_id, filename), daemon=True)
        t.start()
        threads.append(t)
        return job_id, t

    yield _run
    for t in threads:
        t.join(timeout=15)


def test_finishes_normally(fake_model, make_job):
    started, release = fake_model
    release.set()                       # 不卡住，讓它一路跑完
    job_id = make_job("虛構.docx", status="processing")
    service._vlm_worker(job_id, "虛構.docx")
    assert db.get_job(job_id)["status"] == "analyzed"


def test_progress_shows_which_batch(fake_model, run_job):
    started, release = fake_model
    job_id, _t = run_job()
    started.wait(timeout=10)
    time.sleep(0.3)
    assert db.get_job(job_id)["stage"].startswith("逐格判讀 第")
    release.set()


def test_cancel_stops_it(fake_model, run_job):
    started, release = fake_model
    job_id, t = run_job()
    started.wait(timeout=10)
    time.sleep(0.3)

    assert service.cancel(job_id) is True
    release.set()
    t.join(timeout=10)

    job = db.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "已取消分析"
    # 旗標要清掉，否則同一個代碼再跑一次會立刻被取消
    assert job_id not in service._cancelled


def test_second_one_queues(fake_model, run_job):
    started, release = fake_model
    first, t1 = run_job("先來的.docx")
    started.wait(timeout=10)
    second, _t2 = run_job("後到的.docx")
    time.sleep(1.2)

    assert db.get_job(second)["stage"] == "排隊中（前面還有 1 份）"
    assert db.get_job(first)["status"] == "processing"
    release.set()
    t1.join(timeout=15)
    assert db.get_job(first)["status"] == "analyzed"


def test_can_cancel_while_queued(fake_model, run_job):
    started, release = fake_model
    first, t1 = run_job("先來的.docx")
    started.wait(timeout=10)
    second, t2 = run_job("後到的.docx")
    time.sleep(1.2)

    service.cancel(second)
    t2.join(timeout=10)
    # 排隊中就取消，根本不該去問模型
    assert db.get_job(second)["error"] == "已取消分析"
    release.set()
    t1.join(timeout=15)


class TestCancelApi:
    def test_not_running_is_404(self, client, make_job):
        job_id = make_job()                     # 已經分析完了
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404

    def test_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/aaaaaaaaaaaa/cancel").status_code == 404

    @pytest.mark.parametrize("bad", ["..%2f..%2fetc", "../../etc", "ZZZZZZZZZZZZ", "短"])
    def test_bad_id_never_counts_as_a_job(self, client, bad):
        """代碼會接進檔案路徑，格式不對一律不認。擋在哪一層不重要，不能過就對了。"""
        assert client.post(f"/api/jobs/{bad}/cancel").status_code != 200
