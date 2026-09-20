"""匯入頁：貼上文字、掃描檔的提醒、沒有原稿時改列文字本身。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser

PASTED = "姓名：虛構甲\n手機：0912-345-678\n工作經驗：虛構科技 後端工程師"
SCAN_NOTE = "這份是掃描的圖片檔，沒有原文可以比對，內容由模型看圖判讀——請自己核對一遍"


@pytest.fixture
def import_page(page, live_server):
    base, _home = live_server
    page.goto(base + "/import")
    page.wait_for_selector("text=沒有檔案？貼上文字也可以", timeout=30000)
    return page


class TestPasteBox:
    def test_opens(self, import_page):
        import_page.get_by_text("沒有檔案？貼上文字也可以").click()
        assert import_page.get_by_label("貼上履歷內容").is_visible()

    def test_button_waits_for_enough_text(self, import_page):
        import_page.get_by_text("沒有檔案？貼上文字也可以").click()
        box = import_page.get_by_label("貼上履歷內容")
        start = import_page.get_by_role("button", name="開始讀取")

        box.fill("太短")
        assert start.is_disabled()
        box.fill(PASTED)
        assert start.is_enabled()


class TestAfterReading:
    def test_pasted_text_is_shown_instead_of_a_preview(self, page, live_server, seed_import):
        """貼上的文字沒有原稿可以渲染，就把文字本身列出來對照。"""
        base, _home = live_server
        import_id = seed_import(text=PASTED, extracted={"basic.name_zh": "虛構甲"})
        page.evaluate(f"sessionStorage.setItem('import.id', '{import_id}')")
        page.goto(base + "/import")

        page.wait_for_selector("text=貼上的內容", timeout=30000)
        assert "虛構甲" in page.inner_text("body")

    def test_scan_note_is_shown(self, page, live_server, seed_import):
        base, _home = live_server
        import_id = seed_import(text=PASTED, note=SCAN_NOTE,
                                extracted={"basic.name_zh": "虛構甲"})
        page.evaluate(f"sessionStorage.setItem('import.id', '{import_id}')")
        page.goto(base + "/import")

        page.wait_for_selector(f"text={SCAN_NOTE[:12]}", timeout=30000)
