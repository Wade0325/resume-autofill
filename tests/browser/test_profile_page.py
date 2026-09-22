"""我的資料頁：新增的主題分頁、學過的格式清單。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser


@pytest.fixture
def profile_page(page, live_server):
    base, _home = live_server
    page.goto(base + "/profile")
    page.wait_for_selector("text=基本資料", timeout=30000)
    return page


class TestSections:
    def test_new_topics_are_there(self, profile_page):
        """P2-6 多出來的三個分頁：語言能力、求職偏好、常見問答。"""
        nav = profile_page.inner_text("body")
        for name in ("語言能力", "求職偏好", "常見問答"):
            assert name in nav

    def test_qa_has_the_new_fields(self, profile_page):
        profile_page.get_by_text("常見問答", exact=True).last.click()
        body = profile_page.inner_text("body")
        for label in ("優點", "缺點", "生涯規劃", "應徵動機"):
            assert label in body

    def test_age_is_computed_not_typed(self, profile_page):
        """年齡是算出來的，不該讓人自己填——存著的去年填今年就錯了。"""
        profile_page.get_by_text("基本資料", exact=True).last.click()
        body = profile_page.inner_text("body")
        assert "身分證字號" in body    # 基本資料頁確實開起來了
        assert profile_page.get_by_label("年齡").count() == 0


class TestJobHistory:
    def test_recent_jobs_are_listed(self, page, live_server, seed):
        """填寫紀錄：填過的那幾份要列得出來，保留期內還能重新開啟。"""
        base, _home = live_server
        seed("上禮拜那份.docx")
        page.goto(base + "/fill")
        page.wait_for_selector("text=最近填過的", timeout=30000)

        assert "上禮拜那份.docx" in page.inner_text("body")
        assert page.get_by_role("button", name="開啟").count() >= 1
