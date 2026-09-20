"""填寫頁：批次、列印、進度與取消、對映清單直接打字。

`page.wait_for_function` 在這裡**不能用**——它是在頁面裡 eval，會被本站的 CSP
（`script-src 'self'`）擋掉。要等條件就自己輪詢。
"""
from __future__ import annotations

import json
import zipfile

import pytest

pytestmark = pytest.mark.browser

PROFILE = {"basic": {"name_zh": "虛構甲"}, "contact": {"mobile": "0900-000-000"}}


def open_job(page, base, job_id):
    page.evaluate(f"sessionStorage.setItem('fill.jobId', '{job_id}')")
    page.goto(base + "/fill")


def wait_until(page, fn, tries=200, gap=50):
    for _ in range(tries):
        if fn():
            return True
        page.wait_for_timeout(gap)
    return False


class TestBatch:
    def test_picking_several_files_starts_a_batch(self, page, live_server, seed, tmp_path):
        """真正的入口：一次選好幾個檔案。"""
        base, _home = live_server
        seed(profile=PROFILE)              # 先把我的資料寫進去
        page.goto(base + "/fill")
        page.wait_for_selector("text=把空白履歷表拖到這裡", timeout=30000)

        picks = []
        for name in ("A公司.docx", "B公司.docx", "C公司.docx"):
            dst = tmp_path / name
            dst.write_bytes(seed.form.read_bytes())
            picks.append(str(dst))
        page.set_input_files("input[type=file]", picks)

        page.wait_for_selector("text=這一批共 3 份", timeout=60000)
        made = page.evaluate("JSON.parse(sessionStorage.getItem('fill.batchIds') || '[]')")
        assert len(made) == 3 and len(set(made)) == 3

    def test_panel_shows_each_ones_state(self, page, live_server, seed, slots_of):
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        ids = [
            seed("甲公司表格.docx", profile=PROFILE,
                 decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]}),
            seed("乙公司表格.docx",
                 decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]}),
            seed("丙公司表格.docx", status="processing", stage="排隊中（前面還有 1 份）"),
        ]
        page.evaluate(f"sessionStorage.setItem('fill.batchIds', '{json.dumps(ids)}')")
        page.goto(base + "/fill")

        page.wait_for_selector("text=這一批共 3 份", timeout=30000)
        page.wait_for_selector("text=甲公司表格.docx", timeout=15000)
        body = page.inner_text("body")
        assert all(n in body for n in ("甲公司", "乙公司", "丙公司"))
        assert "排隊中（前面還有 1 份）" in body
        assert "會填" in body

    def test_polling_stays_light(self, page, live_server, seed, slots_of):
        """輪詢要打 /jobs/batch，不能拿單份那一支（它會回整份計畫）。"""
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        job_id = seed("甲公司表格.docx", profile=PROFILE,
                      decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]})
        urls = []
        page.on("request", lambda r: urls.append(r.url))
        page.evaluate(f"sessionStorage.setItem('fill.batchIds', '{json.dumps([job_id])}')")
        page.goto(base + "/fill")
        page.wait_for_selector("text=這一批共 1 份", timeout=30000)

        assert any("/api/jobs/batch?ids=" in u for u in urls)
        assert not any(u.rstrip("/").endswith(f"/api/jobs/{job_id}") for u in urls)

    def test_open_one_then_come_back(self, page, live_server, seed, slots_of):
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        ids = [seed("甲公司表格.docx", profile=PROFILE,
                    decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]})]
        page.evaluate(f"sessionStorage.setItem('fill.batchIds', '{json.dumps(ids)}')")
        page.goto(base + "/fill")

        page.get_by_role("button", name="檢視").first.click()
        page.wait_for_selector("text=回到這批", timeout=30000)
        # 單份畫面的功能一個都不能少
        assert page.get_by_role("button", name="列印／存成 PDF").is_visible()
        page.get_by_text("← 回到這批").click()
        page.wait_for_selector("text=這一批共 1 份", timeout=15000)

    def test_apply_all_downloads_a_zip(self, page, live_server, seed, slots_of):
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        decided = {blank.id: ["basic.name_zh", 0, "model", "中文姓名"]}
        ids = [seed("甲公司表格.docx", profile=PROFILE, decided=decided),
               seed("乙公司表格.docx", decided=decided)]
        page.evaluate(f"sessionStorage.setItem('fill.batchIds', '{json.dumps(ids)}')")
        page.goto(base + "/fill")

        btn = page.get_by_role("button", name="全部套用並下載（2 份）")
        btn.wait_for(timeout=30000)
        with page.expect_download(timeout=60000) as dl:
            btn.click()
        assert dl.value.suggested_filename.endswith(".zip")
        names = zipfile.ZipFile(dl.value.path()).namelist()
        assert len(names) == 2
        assert all(n.endswith("_已填寫.docx") for n in names)

    def test_leaving_the_batch_forgets_it(self, page, live_server, seed):
        base, _home = live_server
        ids = [seed("甲公司表格.docx", profile=PROFILE)]
        page.evaluate(f"sessionStorage.setItem('fill.batchIds', '{json.dumps(ids)}')")
        page.goto(base + "/fill")
        page.wait_for_selector("text=這一批共 1 份", timeout=30000)

        page.get_by_text("← 換一批檔案").click()
        page.wait_for_selector("text=把空白履歷表拖到這裡", timeout=15000)
        assert "一次拖好幾份" in page.inner_text("body")
        assert page.evaluate("sessionStorage.getItem('fill.batchIds')") is None


class TestPrint:
    def test_prints_the_clean_copy(self, page, live_server, seed, slots_of):
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        job_id = seed(profile=PROFILE,
                      decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]})
        urls = []
        page.on("request", lambda r: urls.append(r.url))
        open_job(page, base, job_id)

        btn = page.get_by_role("button", name="列印／存成 PDF")
        btn.wait_for(timeout=30000)
        page.wait_for_selector("#root section", timeout=60000)   # 等左右對照渲染完
        page.wait_for_timeout(500)

        # 攔下 window.print，不要真的跳對話框。結尾要留一個可序列化的值，
        # 否則 page.evaluate 回傳函式，後面的行為就不對了
        page.evaluate("""
            window.__printed = 0; window.__at = null;
            window.print = () => {
              window.__printed++;
              const el = document.getElementById('print-root');
              window.__at = {
                exists: !!el,
                sections: el ? el.querySelectorAll('section').length : -1,
                css: (document.getElementById('print-root-style') || {}).textContent || '',
                text: el ? el.textContent : '',
              };
            };
            window.__ready = true;
        """)
        btn.click()
        assert wait_until(page, lambda: page.evaluate("window.__printed") > 0)

        got = [u for u in urls if "preview.docx" in u]
        assert any("highlight=false" in u for u in got)      # 印出來不該帶黃底

        at = page.evaluate("window.__at")
        assert at["exists"] and at["sections"] > 0
        assert "虛構甲" in at["text"]
        css = at["css"]
        assert "@page" in css and "mm" in css               # 紙張尺寸照文件自己的
        assert "margin: 0" in css                           # 不然內容會被擠掉一塊
        assert "display: none !important" in css            # 列印時把 app 藏起來
        assert "zoom: 1" in css                             # 畫面上的預覽有縮，列印不縮

    def test_cleans_up_after_printing(self, page, live_server, seed, slots_of):
        base, _home = live_server
        blank = next(s for s in slots_of(seed.form) if s.kind == "blank")
        job_id = seed(profile=PROFILE,
                      decided={blank.id: ["basic.name_zh", 0, "model", "中文姓名"]})
        open_job(page, base, job_id)
        btn = page.get_by_role("button", name="列印／存成 PDF")
        btn.wait_for(timeout=30000)
        page.wait_for_selector("#root section", timeout=60000)
        page.wait_for_timeout(500)
        page.evaluate("window.__printed = 0; window.print = () => { window.__printed++ };"
                      " window.__ready = true;")
        btn.click()
        # 先等列印真的被呼叫，再看 DOM——直接輪詢元素的話，渲染慢一點就會誤判
        assert wait_until(page, lambda: page.evaluate("window.__printed") > 0)
        assert page.evaluate("!!document.getElementById('print-root')")

        page.evaluate("window.dispatchEvent(new Event('afterprint'))")
        page.wait_for_timeout(300)
        assert page.evaluate("!document.getElementById('print-root')")
        assert page.evaluate("!document.getElementById('print-root-style')")


class TestProgressAndCancel:
    def test_shows_stage_and_can_cancel(self, page, live_server, seed):
        base, _home = live_server
        job_id = seed("虛構履歷表.docx", status="processing",
                      stage="逐格判讀 第 2／7 批", profile=PROFILE)
        open_job(page, base, job_id)

        page.wait_for_selector("text=逐格判讀 第 2／7 批", timeout=30000)
        page.get_by_role("button", name="取消").click()
        page.wait_for_timeout(1200)
        # 後端把它標成取消中；worker 收掉之後畫面要顯示原因
        assert "取消" in page.inner_text("body")
