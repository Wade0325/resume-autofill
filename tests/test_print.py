"""列印／存成 PDF 用的那一份文件。

畫面上的預覽標黃底，是給人核對哪一格填了什麼用的；印出來交給公司不該帶著，
所以列印走 highlight=false，內容跟「套用並下載」的成品同一條寫入路徑。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from backend import service


def highlight_count(content: bytes) -> int:
    """這份 docx 裡有幾個黃底標記。"""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("word/document.xml").decode("utf-8").count("<w:highlight ")


@pytest.fixture
def job(make_job):
    return make_job()


class TestHighlight:
    def test_preview_is_marked(self, job):
        assert highlight_count(service.preview_docx(job, "filled", highlight=True)) > 0

    def test_print_copy_is_clean(self, job):
        assert highlight_count(service.preview_docx(job, "filled", highlight=False)) == 0

    def test_only_the_highlight_differs(self, job, text_of):
        marked = service.preview_docx(job, "filled", highlight=True)
        clean = service.preview_docx(job, "filled", highlight=False)
        assert text_of(marked) == text_of(clean)
        assert "虛構甲" in text_of(clean)

    def test_matches_the_downloaded_file(self, job, text_of):
        """列印的內容必須跟下載的成品一模一樣，不能是另一條路算出來的。"""
        clean = service.preview_docx(job, "filled", highlight=False)
        service.write_output(job)
        downloaded = service.output_path(job).read_bytes()
        assert highlight_count(downloaded) == 0
        assert text_of(clean) == text_of(downloaded)


class TestPreviewApi:
    def test_clean_copy_over_http(self, client, job):
        r = client.get(f"/api/jobs/{job}/preview.docx",
                       params={"which": "filled", "highlight": "false"})
        assert r.status_code == 200
        assert "wordprocessingml" in r.headers["content-type"]
        assert highlight_count(r.content) == 0

    def test_default_still_marks(self, client, job):
        """網頁上的左右對照不受影響。"""
        r = client.get(f"/api/jobs/{job}/preview.docx", params={"which": "filled"})
        assert highlight_count(r.content) > 0

    def test_original_is_untouched(self, client, job, text_of):
        r = client.get(f"/api/jobs/{job}/preview.docx",
                       params={"which": "original", "highlight": "false"})
        assert highlight_count(r.content) == 0
        assert "虛構甲" not in text_of(r.content)

    def test_bad_which_is_rejected(self, client, job):
        assert client.get(f"/api/jobs/{job}/preview.docx",
                          params={"which": "亂打"}).status_code == 422
