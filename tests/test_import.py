"""匯入：掃描型 PDF、貼上文字、長文不再被逐字驗證丟掉。

抽出來的值要跟原文對得上才留（防止模型自己編），但長文模型幾乎一定會改寫，
所以長文只比開頭、而且值改用原文那一段。
"""
from __future__ import annotations

import time

import pytest
from PIL import Image

from backend import db, service
from backend.core import llm, reader

SOURCE = ("工作經驗\n公司：虛構科技\n工作內容：負責後端服務的設計與維護，"
          "包含 API 設計、資料庫調校與監控；並帶領兩位工程師完成訂單系統改版。\n")
# 模型改寫過的版本：換了標點、少了幾個字——逐字比對一定對不上
REWRITTEN = ("負責後端服務的設計與維護、包含API設計、資料庫調校與監控，"
             "並帶領兩位工程師完成訂單系統改版")


class TestVerbatim:
    def test_long_text_survives_rewriting(self):
        kept = reader._keep_verbatim(
            {"experience": [{"company": "虛構科技", "description": REWRITTEN}]}, SOURCE, {})
        assert kept["experience"][0]["description"]

    def test_the_kept_value_comes_from_the_source(self):
        """留下來的要是文件裡的字，不是模型的改寫版。"""
        kept = reader._keep_verbatim(
            {"experience": [{"company": "虛構科技", "description": REWRITTEN}]}, SOURCE, {})
        assert kept["experience"][0]["description"].startswith("負責後端服務的設計與維護，")

    def test_short_values_are_still_checked_word_for_word(self):
        """短的值掰出來還是要丟掉——那才是這道防線的重點。"""
        kept = reader._keep_verbatim(
            {"basic": {}, "contact": {"mobile": "0900-999-999"}}, SOURCE, {})
        assert kept.get("contact") is None


@pytest.fixture
def scanned_pdf(tmp_path):
    """整頁都是圖片、沒有文字層的 PDF。"""
    path = tmp_path / "scan.pdf"
    Image.new("RGB", (1240, 1754), (250, 250, 250)).save(path)
    return path.read_bytes()


@pytest.fixture
def fake_reader(monkeypatch):
    """把讀取換成假的，記下它是怎麼被呼叫的。"""
    seen = {}

    def _read(text, host, model, images=None, verify=True):
        seen["verify"] = verify
        seen["images"] = len(images or [])
        return {"basic.name_zh": "看圖讀到的名字"}

    monkeypatch.setattr(reader, "read", _read)
    return seen


def wait_for(import_id, tries=60):
    for _ in range(tries):
        record = db.get_import(import_id)
        if record["status"] != "processing":
            return record
        time.sleep(0.25)
    return db.get_import(import_id)


class TestScannedPdf:
    def test_without_vision_it_says_so(self, monkeypatch, scanned_pdf, fake_reader):
        """以前默默回 0 筆，使用者不知道為什麼。"""
        monkeypatch.setattr(llm, "supports_vision", lambda host: False)
        record = wait_for(service.analyze_import("掃描的履歷.pdf", scanned_pdf)["import_id"])
        assert record["status"] == "failed"
        assert "掃描" in record["error"]

    def test_with_vision_it_reads_the_picture(self, monkeypatch, scanned_pdf, fake_reader):
        monkeypatch.setattr(llm, "supports_vision", lambda host: True)
        record = wait_for(service.analyze_import("掃描的履歷.pdf", scanned_pdf)["import_id"])
        assert record["status"] == "ready"
        assert fake_reader["images"] > 0
        # 沒有文字層，逐字驗證會把每個值都丟掉
        assert fake_reader["verify"] is False
        assert "核對" in record["note"]


class TestPasteText:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        monkeypatch.setattr(reader, "read", lambda text, host, model, images=None, verify=True: {
            "basic.name_zh": "虛構甲", "contact.mobile": "0912-345-678"})

    def test_too_short_is_rejected(self, client):
        assert client.post("/api/imports/text", json={"text": "太短"}).status_code == 422

    def test_reads_what_you_pasted(self, client):
        text = "姓名：虛構甲\n手機：0912-345-678\n工作經驗：虛構科技 後端工程師"
        import_id = client.post("/api/imports/text", json={"text": text}).json()["import_id"]
        state = self._wait(client, import_id)

        assert state["status"] == "ready"
        assert any(r["field_key"] == "contact.mobile" for r in state["preview"]["rows"])
        # 貼上的文字沒有原稿可以渲染，畫面要改列文字本身
        assert state["preview"]["has_source"] is False

    def test_source_endpoint_returns_plain_text(self, client):
        text = "姓名：虛構甲\n手機：0912-345-678\n工作經驗：虛構科技 後端工程師"
        import_id = client.post("/api/imports/text", json={"text": text}).json()["import_id"]
        self._wait(client, import_id)

        r = client.get(f"/api/imports/{import_id}/source")
        assert r.headers["content-type"] == "text/plain; charset=utf-8"
        assert r.text.startswith("姓名：虛構甲")

    @staticmethod
    def _wait(client, import_id, tries=60):
        for _ in range(tries):
            state = client.get(f"/api/imports/{import_id}").json()
            if state["status"] != "processing":
                return state
            time.sleep(0.25)
        return state
