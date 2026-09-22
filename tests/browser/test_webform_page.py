"""網頁填寫頁面：清單怎麼顯示、缺欄位時不能勾、補齊後送出的內容。

後端的 /api/webform/cake 用 page.route 假造，不開第二個瀏覽器、也不碰 Cake。
"""
from __future__ import annotations

import copy

import pytest

pytestmark = pytest.mark.browser


def _f(key, label, kind, value="", required=True, **extra):
    return {"key": key, "label": label, "kind": kind, "value": value, "required": required,
            **extra}


BASE = {"message": "", "notes": [], "progress": {"done": 0, "total": 0, "current": ""},
        "results": [], "browser_open": True, "site": {"name": "cake", "label": "Cake"}}

READY = {**BASE, "stage": "ready", "items": [
    {"id": "experience-0", "section": "工作經驗", "title": "虛構科技／後端工程師", "exists": True,
     "fields": [], "missing": []},
    {"id": "experience-1", "section": "工作經驗", "title": "另一間公司／資料工程師",
     "exists": False, "missing": [], "fields": [
         _f("company", "公司名稱", "text", "另一間公司"),
         _f("title", "職稱", "text", "資料工程師"),
         _f("start", "開始日期", "ym", "2021/03"),
         _f("end", "結束日期", "ym", "", alt="現任職位", alt_on=True),
         _f("description", "工作內容", "textarea", "", False)]},
    {"id": "education-0", "section": "學歷", "title": "虛構大學／博士", "exists": False,
     "missing": ["學歷"], "fields": [
         _f("school", "學校", "text", "虛構大學"),
         _f("degree", "學歷", "select", "", options=["哲學博士（PhD）", "醫學博士（MD）"]),
         _f("department", "主修", "text", "資訊工程")]},
    {"id": "certificate-0", "section": "資格認證", "title": "TOEIC", "exists": False,
     "missing": ["到期日"], "fields": [
         _f("name", "名稱", "text", "TOEIC"),
         _f("issuer", "發照機構", "text", "ETS"),
         _f("issued", "發照日期", "ym", "2020/05"),
         _f("expires", "到期日", "ym", "", alt="永久有效", alt_on=False)]},
]}


@pytest.fixture
def fake_api(page):
    """假的網頁填寫 API。state 換掉就是下一次輪詢拿到的；posts 記下送出的內容。"""
    api = {"state": copy.deepcopy(READY), "posts": [], "after": {}}

    def handle(route):
        req = route.request
        if req.method == "GET":
            return route.fulfill(json=api["state"])
        action = req.url.rsplit("/", 1)[-1]
        api["posts"].append((action, req.post_data_json))
        if action in api["after"]:
            api["state"] = api["after"][action]
        route.fulfill(json=api["state"])

    page.route("**/api/webform/cake**", handle)
    return api


def _open(page, live_server):
    base, _home = live_server
    page.goto(base + "/webform")
    page.wait_for_selector("text=網頁填寫", timeout=30000)


def test_清單與送出(page, live_server, fake_api):
    _open(page, live_server)
    page.wait_for_selector("text=另一間公司／資料工程師")
    body = page.inner_text("body")
    assert "已經在 Cake 上，略過" in body
    assert "還缺：到期日" in body and "還缺：學歷" in body
    save = page.get_by_role("button", name="存進 Cake（1 筆）")     # 只有完整的那筆預設勾起來
    assert save.is_enabled()

    cert = page.locator('[data-item="certificate-0"]')
    assert cert.get_by_role("checkbox", name="新增 TOEIC").is_disabled()
    cert.get_by_label("永久有效").check()                           # 補齊最後一個缺的就自動勾起來
    assert cert.get_by_role("checkbox", name="新增 TOEIC").is_checked()
    assert cert.get_by_label("到期日").is_disabled()

    page.locator('[data-item="education-0"]').get_by_label("學歷").select_option("哲學博士（PhD）")
    cert.get_by_label("發照機構").fill("ETS 台灣")

    progress = {"done": 0, "total": 3, "current": "工作經驗：另一間公司"}
    fake_api["after"]["run"] = {**BASE, "stage": "running", "items": READY["items"],
                                "progress": progress}
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="存進 Cake（3 筆）").click()
    page.wait_for_selector("text=正在存進 Cake：第 1／3 筆")

    action, body = fake_api["posts"][-1]
    assert action == "run"
    picks = {p["id"]: p for p in body["items"]}
    assert set(picks) == {"experience-1", "education-0", "certificate-0"}
    assert picks["certificate-0"]["alts"] == {"expires": True}
    assert picks["certificate-0"]["values"]["issuer"] == "ETS 台灣"
    assert picks["education-0"]["values"]["degree"] == "哲學博士（PhD）"
    assert picks["experience-1"]["alts"] == {"end": True}


def test_取消確認就不送(page, live_server, fake_api):
    _open(page, live_server)
    page.once("dialog", lambda d: d.dismiss())
    page.get_by_role("button", name="存進 Cake（1 筆）").click()
    page.wait_for_timeout(300)
    assert fake_api["posts"] == []


def test_開啟瀏覽器之後等登入(page, live_server, fake_api):
    fake_api["state"] = {**BASE, "stage": "closed", "items": []}
    fake_api["after"]["open"] = {**BASE, "stage": "login", "items": [],
                                 "message": "請在跳出來的瀏覽器視窗登入 Cake"}
    _open(page, live_server)
    page.get_by_role("button", name="開啟瀏覽器").click()
    page.wait_for_selector("text=請在跳出來的瀏覽器視窗登入 Cake")
    assert fake_api["posts"][-1][0] == "open"


def test_存完的結果(page, live_server, fake_api):
    fake_api["state"] = {**READY, "results": [
        {"id": "experience-1", "section": "工作經驗", "title": "另一間公司／資料工程師",
         "ok": True, "why": ""},
        {"id": "certificate-0", "section": "資格認證", "title": "TOEIC", "ok": False,
         "why": "Cake 沒有收下這一筆：名稱格式不正確"}]}
    _open(page, live_server)
    page.wait_for_selector("text=存進 Cake 1 筆，1 筆沒存成")
    assert "資格認證「TOEIC」：Cake 沒有收下這一筆：名稱格式不正確" in page.inner_text("body")


def test_沒有要補的(page, live_server, fake_api):
    fake_api["state"] = {**READY, "items": READY["items"][:1]}
    _open(page, live_server)
    page.wait_for_selector("text=Cake 上已經有你所有的工作經驗、學歷與證照，沒有要補的。")
