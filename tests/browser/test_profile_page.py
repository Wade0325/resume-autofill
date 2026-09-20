"""我的資料頁：大頭照、新增的主題分頁、學過的格式清單。"""
from __future__ import annotations

import io

import pytest
from PIL import Image

pytestmark = pytest.mark.browser


def fake_photo() -> bytes:
    img = Image.new("RGB", (600, 800), (200, 190, 180))
    for x in range(0, 600, 40):
        for y in range(0, 800, 40):
            img.putpixel((x, y), (60, 60, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


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
        assert "大頭照" in body        # 基本資料頁確實開起來了
        assert profile_page.get_by_label("年齡").count() == 0


class TestPhoto:
    def test_upload_show_and_remove(self, profile_page, live_server, tmp_path):
        base, _home = live_server
        profile_page.get_by_text("基本資料", exact=True).last.click()
        assert "還沒有照片" in profile_page.inner_text("body")

        path = tmp_path / "me.png"
        path.write_bytes(fake_photo())
        profile_page.set_input_files("input[accept*='image']", str(path))

        # 存進後端了才算數，不是只在畫面上預覽
        for _ in range(60):
            if profile_page.request.get(base + "/api/profile/photo").status == 200:
                break
            profile_page.wait_for_timeout(250)
        assert profile_page.request.get(base + "/api/profile/photo").status == 200
        assert "換一張" in profile_page.inner_text("body")

        profile_page.once("dialog", lambda d: d.accept())
        profile_page.get_by_role("button", name="移除").click()
        for _ in range(60):
            if profile_page.request.get(base + "/api/profile/photo").status == 404:
                break
            profile_page.wait_for_timeout(250)
        assert profile_page.request.get(base + "/api/profile/photo").status == 404


class TestJobHistory:
    def test_recent_jobs_are_listed(self, page, live_server, seed):
        """填寫紀錄：填過的那幾份要列得出來，保留期內還能重新開啟。"""
        base, _home = live_server
        seed("上禮拜那份.docx")
        page.goto(base + "/fill")
        page.wait_for_selector("text=最近填過的", timeout=30000)

        assert "上禮拜那份.docx" in page.inner_text("body")
        assert page.get_by_role("button", name="開啟").count() >= 1
