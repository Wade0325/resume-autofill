"""網頁填寫整條流程：真的開瀏覽器，對著本機的假 Cake（fake_cake.html）登入、讀清單、存進去。

不碰真的 Cake。假網站照真的個人檔案頁做出會踩到的那幾點，說明寫在 fake_cake.html 開頭。
"""
from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from backend.webform import dom
from backend.webform import session as session_mod
from backend.webform.browser import Browser
from backend.webform.cake import CakeSite
from backend.webform.session import Session

pytestmark = pytest.mark.browser

HERE = Path(__file__).parent
CHROMIUM_1228 = Path.home() / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe"

SIGN_IN = """<!doctype html><meta charset="utf-8"><title>登入</title>
<input type="email" aria-label="電子郵件"><input type="password" aria-label="密碼">
<button id="login" onclick="document.cookie='session=1; path=/';
  location.href='/dashboard/profile'">登入</button>"""

PROFILE = {
    "basic": {"name_zh": "虛構甲", "national_id": "A123456789"},
    "experience": [
        {"company": "虛構科技", "title": "後端工程師", "start": "2018年08月01日",
         "end": "2021年02月28日"},
        {"company": "另一間公司", "title": "資料工程師", "start": "2021年03月01日",
         "end": "至今", "description": "寫資料管線"},
    ],
    "education": [
        {"school": "虛構大學", "department": "資訊工程學系", "degree": "學士",
         "start": "2014年09月01日", "end": "2018年06月30日"},
    ],
    "certificate": [
        {"name": "TOEIC", "issuer": "ETS", "issued": "2020年05月"},
        {"name": "壞掉的證照", "issuer": "某機構", "issued": "2019/1", "expires": "永久"},
    ],
    "family": [{"name": "虛構乙", "relation": "父"}],
}


class FakeCake(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.saved = []


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body="", headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/dashboard/profile"):
            if "session=1" not in (self.headers.get("Cookie") or ""):
                return self._send(302, headers=[("Location", "/users/sign-in")])
            html = (HERE / "fake_cake.html").read_text(encoding="utf-8")
            return self._send(200, html.replace("__SAVED__", json.dumps(self.server.saved)))
        if self.path.startswith("/users/sign-in"):
            return self._send(200, SIGN_IN)
        self._send(404, "not found")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.saved.append(json.loads(body))
        self._send(200, "{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_cake():
    server = FakeCake()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture
def session(fake_cake, tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "LOGIN_POLL_SECONDS", 0.3)
    kwargs = {"executable": str(CHROMIUM_1228)} if CHROMIUM_1228.exists() else {"channels": ("",)}
    browser = Browser(tmp_path / "browser", headless=True, **kwargs)
    url = f"http://127.0.0.1:{fake_cake.server_address[1]}/dashboard/profile"
    s = Session(CakeSite(start_url=url), browser, lambda: PROFILE)
    yield s
    s.close()


def wait_for(s, stage, seconds=60, says=""):
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = s.snapshot()
        if state["stage"] == stage and says in state["message"]:
            return state
        time.sleep(0.2)
    pytest.fail(f"等不到 {stage}，停在 {s.snapshot()['stage']}：{s.snapshot()['message']}")


async def _user_logs_in(browser):
    """模擬使用者在那個視窗裡自己登入。"""
    page = await browser.page()
    await page.fill("input[type=password]", "不會經過程式的密碼")
    await page.click("#login")


def test_等登入_讀清單_存進去(session, fake_cake):
    session.open()
    wait_for(session, "login", says="請在跳出來的瀏覽器視窗登入")
    time.sleep(1)                               # 登入頁上不能被程式帶走
    assert session.snapshot()["stage"] == "login"

    session.browser.run(_user_logs_in(session.browser))
    state = wait_for(session, "ready")
    items = {i["id"]: i for i in state["items"]}

    # 名稱對得上（公司後綴不算差別）的就算已經有，不會重複新增
    assert items["experience-0"]["exists"] is True
    assert items["experience-1"]["exists"] is False
    end = next(f for f in items["experience-1"]["fields"] if f["key"] == "end")
    assert end["alt_on"] is True and end["value"] == ""          # 「至今」＝現任職位
    degree = next(f for f in items["education-0"]["fields"] if f["key"] == "degree")
    assert degree["value"] == "學士學位"                         # 選項是從打開的表單讀來的
    assert "學士學位" in degree["options"]
    assert items["certificate-0"]["missing"] == ["到期日"]
    # 不在任何一區清單裡的資料（身分證字號、家人）連清單都進不去
    dumped = json.dumps(state, ensure_ascii=False)
    assert "A123456789" not in dumped and "虛構乙" not in dumped
    assert fake_cake.saved == []                                  # 讀清單不會存任何東西
    # 使用者還盯著瀏覽器視窗：提示他回到程式那一頁
    assert "請回到「履歷自動填寫」確認要補的資料" in session.browser.run(_banner(session.browser))

    session.run([
        {"id": "experience-1"},
        {"id": "education-0"},
        {"id": "certificate-0", "values": {"issuer": "ETS 台灣"}, "alts": {"expires": True}},
        {"id": "certificate-1"},
    ])
    wait_for(session, "running", 5)
    state = wait_for(session, "ready", 120)

    results = {r["id"]: r for r in state["results"]}
    assert all(results[k]["ok"] for k in ("experience-1", "education-0", "certificate-0"))
    assert results["certificate-1"]["ok"] is False
    assert "名稱格式不正確" in results["certificate-1"]["why"]     # 網站自己印的錯誤訊息

    saved = {s["section"] + s["data"].get("公司名稱", s["data"].get("學校", s["data"].get("名稱"))):
             s["data"] for s in fake_cake.saved}
    assert saved["工作經驗另一間公司"] == {
        "公司名稱": "另一間公司", "職稱": "資料工程師", "開始日期": "2021 三月",
        "現任職位": True, "工作內容": "寫資料管線"}
    assert saved["學歷虛構大學"] == {
        "學校": "虛構大學", "學歷": "學士學位", "主修": "資訊工程學系",
        "開始日期": "2014", "結束日期（或預期）": "2018"}
    assert saved["資格認證TOEIC"] == {
        "名稱": "TOEIC", "發照機構": "ETS 台灣", "發照日期": "2020 五月", "永久有效": True}
    assert len(fake_cake.saved) == 3                              # 被拒收的那筆沒有存

    assert "請回到「履歷自動填寫」看結果" in session.browser.run(_banner(session.browser))

    # 存完重新讀：剛存進去的變成「已經有了」，被拒收的還在清單上
    items = {i["id"]: i for i in state["items"]}
    assert items["experience-1"]["exists"] and items["education-0"]["exists"]
    assert items["certificate-0"]["exists"] and not items["certificate-1"]["exists"]


def test_缺必填的整批不開始(session):
    session.browser.run(_login_cookie(session))
    session.open()
    wait_for(session, "ready")
    with pytest.raises(ValueError, match="到期日"):
        session.run([{"id": "experience-1"}, {"id": "certificate-0"}])
    assert session.snapshot()["stage"] == "ready"


def test_使用者關掉視窗(session):
    session.browser.run(_login_cookie(session))
    session.open()
    wait_for(session, "ready")
    session.browser.run(_close_window(session.browser))
    time.sleep(0.5)
    state = session.snapshot()
    assert state["browser_open"] is False and state["items"]       # 清單留著，重新讀取會再開


def test_提示條不擋點擊也不算頁面文字(session):
    """提示條剛好蓋在按鈕上也點得到；讀「那一區印著什麼」時也讀不到它。"""
    hit, in_text, shown = session.browser.run(_under_banner(session.browser))
    assert hit == 1 and in_text is False and shown is True


async def _under_banner(browser):
    page = await browser.page()
    await page.set_content(
        '<body style="margin:0"><button id="b" onclick="window.hit = 1" style="position:fixed;'
        'top:60px;left:0;width:100vw;height:80px">新增</button></body>')
    await dom.banner(page, "正在自動填寫，請不要操作這個視窗", "busy")
    await page.click("#b", timeout=3000)
    return await page.evaluate("""() => [window.hit,
        document.body.innerText.includes('正在自動填寫'),
        !!document.getElementById('resume-autofill-banner')]""")


async def _banner(browser):
    page = await browser.page()
    return await page.evaluate(
        "() => document.getElementById('resume-autofill-banner')?.textContent || ''")


async def _login_cookie(s):
    """直接帶著登入狀態（省掉等登入那一段）。"""
    page = await s.browser.page()
    host = s.site.start_url.split("/dashboard")[0]
    await page.context.add_cookies([{"name": "session", "value": "1", "url": host}])


async def _close_window(browser):
    page = await browser.page()
    await page.context.close()
