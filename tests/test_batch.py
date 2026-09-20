"""批次：一次好幾份的進度、整批套用、打包下載。

每一份還是獨立的工作，所以這裡測的是「綁成一組之後」的那幾件事：
進度輕量、一份壞掉不影響其他份、zip 裡同名不會互相蓋掉。
"""
from __future__ import annotations

import io
import zipfile
from urllib.parse import unquote

import pytest

from backend import service


@pytest.fixture
def four_jobs(make_job):
    """兩份分析完（故意同名）、一份還在排隊、一份失敗。"""
    return {
        "a": make_job("應徵人員資料表.docx"),
        "b": make_job("應徵人員資料表.docx"),
        "running": make_job("還在跑的.docx", status="processing",
                            stage="排隊中（前面還有 2 份）"),
        "broken": make_job("壞掉的.docx", status="failed", error="分析失敗了"),
    }


def ids_of(jobs):
    return ",".join(jobs.values())


class TestStatus:
    def test_returns_every_job(self, client, four_jobs):
        rows = client.get("/api/jobs/batch", params={"ids": ids_of(four_jobs)}).json()
        assert len(rows) == 4

    def test_shows_stage_and_error(self, client, four_jobs):
        rows = {r["job_id"]: r for r in
                client.get("/api/jobs/batch", params={"ids": ids_of(four_jobs)}).json()}
        assert rows[four_jobs["a"]]["status"] == "analyzed"
        assert rows[four_jobs["a"]]["fill"] > 0
        assert rows[four_jobs["running"]]["stage"] == "排隊中（前面還有 2 份）"
        assert rows[four_jobs["broken"]]["error"] == "分析失敗了"
        assert rows[four_jobs["a"]]["downloadable"] is False

    def test_does_not_carry_the_plan(self, client, four_jobs):
        """畫面每兩秒輪詢這一支。帶著計畫的話，五份就是每兩秒搬五份計畫。"""
        rows = client.get("/api/jobs/batch", params={"ids": ids_of(four_jobs)}).json()
        assert all("plan" not in r for r in rows)

    @pytest.mark.parametrize("ids", ["../../etc/passwd", ",", ""])
    def test_rejects_bad_ids(self, client, ids):
        assert client.get("/api/jobs/batch", params={"ids": ids}).status_code == 422

    def test_rejects_too_many(self, client, four_jobs):
        many = ",".join([four_jobs["a"]] * (service.BATCH_MAX + 1))
        assert client.get("/api/jobs/batch", params={"ids": many}).status_code == 422

    def test_single_job_route_still_works(self, client, four_jobs):
        """/jobs/batch 宣告在 /{job_id} 前面，別把單份的路由吃掉了。"""
        assert client.get(f"/api/jobs/{four_jobs['a']}").json()["status"] == "ready"


class TestApplyAll:
    def test_applies_the_ready_ones(self, client, four_jobs):
        res = {r["job_id"]: r for r in
               client.post("/api/jobs/batch/output",
                           json={"job_ids": list(four_jobs.values())}).json()}
        assert res[four_jobs["a"]]["ok"] and res[four_jobs["b"]]["ok"]
        assert res[four_jobs["a"]]["written"] > 0

    def test_one_failure_does_not_stop_the_rest(self, client, four_jobs):
        res = {r["job_id"]: r for r in
               client.post("/api/jobs/batch/output",
                           json={"job_ids": list(four_jobs.values())}).json()}
        assert sum(1 for r in res.values() if r["ok"]) == 2
        assert res[four_jobs["running"]]["ok"] is False
        assert "還沒分析完" in res[four_jobs["running"]]["error"]
        assert res[four_jobs["broken"]]["error"] == "分析失敗了"


class TestZip:
    @pytest.fixture
    def applied(self, client, four_jobs):
        client.post("/api/jobs/batch/output", json={"job_ids": list(four_jobs.values())})
        return four_jobs

    def test_packs_only_what_exists(self, client, applied):
        r = client.get("/api/jobs/batch.zip", params={"ids": ids_of(applied)})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 2

    def test_same_filenames_do_not_clobber(self, client, applied):
        """兩家公司的表格常常都叫「應徵人員資料表.docx」，不編號會在 zip 裡蓋掉。"""
        names = zipfile.ZipFile(
            io.BytesIO(client.get("/api/jobs/batch.zip",
                                  params={"ids": ids_of(applied)}).content)).namelist()
        assert len(set(names)) == 2
        assert all(n.endswith("_已填寫.docx") for n in names)

    def test_contents_are_actually_filled(self, client, applied, text_of):
        content = client.get("/api/jobs/batch.zip", params={"ids": ids_of(applied)}).content
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            first = z.read(z.namelist()[0])
        assert "虛構甲" in text_of(first)

    def test_chinese_filename_and_ascii_fallback(self, client, applied):
        disp = client.get("/api/jobs/batch.zip",
                          params={"ids": ids_of(applied)}).headers["content-disposition"]
        assert "filename*=UTF-8''" in disp
        assert "已填寫履歷_2份.zip" in unquote(disp)
        assert 'filename="resumes_2.zip"' in disp      # 舊瀏覽器的備援

    def test_nothing_applied_yet_says_so(self, client, four_jobs):
        r = client.get("/api/jobs/batch.zip", params={"ids": four_jobs["running"]})
        assert r.status_code == 404
