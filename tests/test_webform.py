"""網頁填寫（Cake）裡不必開瀏覽器的部分：清單怎麼排、缺什麼、學位怎麼對、API 擋什麼。

整條流程（真的開瀏覽器、對著假 Cake 存進去）在 tests/browser/test_webform_flow.py。
"""
from __future__ import annotations

import json
from concurrent.futures import Future

import pytest

from backend import webform
from backend.webform import cake
from backend.webform.session import Busy, Session

# 2026-09 從真的 Cake 學歷下拉讀出來的選項
CAKE_DEGREES = ["請選擇", "副學士學位", "文學士（BA）", "工商管理學士（BBA）", "工學學士（BEng）",
                "美術學士（BFA）", "理學士（BS）", "學士學位", "工程師學位", "文學碩士（MA）",
                "工商管理碩士（MBA）", "美術碩士（MFA）", "科學碩士（MS）", "碩士學位",
                "哲學博士（PhD）", "醫學博士（MD）", "法學博士（JD）", "高中文憑",
                "非學位課程（例如 Coursera 證書）", "其他"]

PROFILE = {
    "basic": {"name_zh": "虛構甲", "national_id": "A123456789"},
    "contact": {"mobile": "0900-000-000"},
    "experience": [
        {"company": "虛構科技股份有限公司", "title": "後端工程師", "start": "2018年08月01日",
         "end": "至今", "salary": "50,000", "leave_reason": "在職中"},
        {"company": "另一間公司", "title": "實習生", "start": "2017/7", "end": "2017年08月"},
        {"company": "", "title": "沒有公司名稱的不算"},
    ],
    "education": [{"school": "國立虛構大學", "degree": "專科", "department": "資訊科",
                   "start": "2013", "end": "2016"}],
    "certificate": [{"name": "TOEIC", "issuer": "ETS", "issued": "2020年05月", "expires": "永久"},
                    {"name": "丙級技術士", "issuer": ""}],
    "family": [{"name": "虛構乙"}],
}


def _field(item, key):
    return next(f for f in item["fields"] if f["key"] == key)


@pytest.mark.parametrize("text, with_month, want", [
    ("2018年08月01日", True, "2018/08"),
    ("2021/3", True, "2021/03"),
    ("2016", True, "2016"),                   # 沒有月份：清單上會標成缺
    ("2014年09月01日", False, "2014"),
    ("民國一百年", True, "民國一百年"),       # 讀不出來照原文，讓使用者自己改
    ("", True, ""),
])
def test_清單上的日期(text, with_month, want):
    assert cake.fmt_date(text, with_month) == want


@pytest.mark.parametrize("degree, want", [
    ("專科", "副學士學位"),                   # 「副學士」裡也有「學士」，不能對成學士
    ("學士", "學士學位"),                     # 大學有 BA、BS、BEng…，有通稱就用通稱，不猜主修
    ("大學", "學士學位"),
    ("碩士", "碩士學位"),
    ("高中", "高中文憑"),
    ("博士", ""),                            # PhD、MD、JD 選哪個要看主修，留給使用者挑
    ("理學士（BS）", "理學士（BS）"),          # 已經是 Cake 的選項就照用
    ("看不懂的學位", ""),
])
def test_學位對到Cake的選項(degree, want):
    assert cake.map_degree(degree, CAKE_DEGREES) == want


def test_沒有通稱時同一級有好幾個就不選():
    assert cake.map_degree("學士", ["請選擇", "文學士（BA）", "理學士（BS）"]) == ""


@pytest.mark.parametrize("name, lines, want", [
    ("虛構科技", ["後端工程師", "虛構科技股份有限公司", "2018年8月 - 至今"], True),
    ("國立臺灣大學", ["國立台灣大學 National Taiwan University"], True),     # 臺／台、後面接英文
    ("國立虛構大學", ["虛構大學"], True),                                   # 簡稱
    ("ＴＯＥＩＣ", ["TOEIC"], True),                                       # 全半形
    ("資訊科技股份有限公司", ["新增", "資訊"], False),   # 短字不能讓一整筆被當成已經有
    ("另一間公司", ["虛構科技股份有限公司"], False),
    ("", ["虛構科技"], False),
])
def test_判斷已經在頁面上(name, lines, want):
    assert cake.on_page(name, lines) is want


def test_清單():
    existing = {"工作經驗": ["後端工程師", "虛構科技", "2018年8月 - 至今"],
                "學歷": [], "資格認證": []}
    items = cake.build_plan(PROFILE, existing, CAKE_DEGREES)
    by_id = {i["id"]: i for i in items}

    assert set(by_id) == {"experience-0", "experience-1", "education-0",
                          "certificate-0", "certificate-1"}      # 沒有名稱的那筆不列
    assert by_id["experience-0"]["exists"] is True               # 公司後綴不算差別
    assert by_id["experience-1"]["exists"] is False

    new = by_id["experience-1"]
    assert new["title"] == "另一間公司／實習生"
    assert _field(new, "start")["value"] == "2017/07"
    assert _field(new, "end")["alt_on"] is False and _field(new, "end")["value"] == "2017/08"
    assert new["missing"] == []
    assert _field(by_id["experience-0"], "end")["alt_on"] is True  # 至今＝現任職位

    edu = by_id["education-0"]
    assert _field(edu, "degree")["value"] == "副學士學位"
    assert _field(edu, "start")["value"] == "2013" and edu["missing"] == []

    cert = by_id["certificate-0"]
    assert _field(cert, "expires")["alt_on"] is True               # 永久＝永久有效
    assert by_id["certificate-1"]["missing"] == ["發照機構", "發照日期", "到期日"]


def test_清單只帶每一區列出的欄位():
    """身分證字號、電話、薪資、離職原因、家人：不在任何一區的欄位清單裡，就送不出去。"""
    items = cake.build_plan(PROFILE, {"工作經驗": [], "學歷": [], "資格認證": []}, CAKE_DEGREES)
    dumped = json.dumps(items, ensure_ascii=False)
    for secret in ("A123456789", "0900-000-000", "50,000", "在職中", "虛構乙"):
        assert secret not in dumped


def test_頁面上找不到的區塊整區不動():
    items = cake.build_plan(PROFILE, {"工作經驗": None, "學歷": [], "資格認證": None}, [])
    assert {i["section"] for i in items} == {"學歷"}
    # 讀不到 Cake 的學歷選項：照原文給使用者看，送出時再找最接近的
    assert _field(items[0], "degree")["kind"] == "text"


@pytest.mark.parametrize("field, bad", [
    ({"kind": "ym", "value": "2020/05", "required": True}, False),
    ({"kind": "ym", "value": "2020", "required": True}, True),        # 要月份
    ({"kind": "ym", "value": "2020/13", "required": True}, True),
    ({"kind": "ym", "value": "", "required": True, "alt": "現任職位", "alt_on": True}, False),
    ({"kind": "ym", "value": "", "required": False}, False),
    ({"kind": "y", "value": "2020", "required": True}, False),
    ({"kind": "select", "value": "學士學位", "required": True, "options": ["學士學位"]}, False),
    ({"kind": "select", "value": "學士", "required": True, "options": ["學士學位"]}, True),
    ({"kind": "text", "value": " ", "required": True}, True),
    ({"kind": "textarea", "value": "", "required": False}, False),
])
def test_還缺什麼(field, bad):
    assert (cake.problems([{"label": "欄", **field}]) == ["欄"]) is bad


@pytest.mark.parametrize("url, want", [
    ("https://www.cake.me/users/sign-in", True),
    ("https://accounts.google.com/o/oauth2/auth", True),      # 第三方登入頁：絕不能動
    ("https://www.cake.me/dashboard/profile", False),
    ("https://www.cake.me/", False),
])
def test_使用者正在登入(url, want):
    assert cake.in_login_flow(url) is want


class _NoBrowser:
    """驗證沒過的時候根本不會碰到瀏覽器。"""
    is_open = False

    def submit(self, coro):
        coro.close()
        raise AssertionError("不該開始")


class _Recorder:
    """記下開始了哪件事，不真的開瀏覽器。"""
    is_open = True

    def __init__(self):
        self.started = []

    def submit(self, coro):
        inner = coro.cr_frame.f_locals["coro"]      # _guard 包著的那一件事
        self.started.append(inner.__name__)
        inner.close()
        coro.close()
        done = Future()
        done.set_result(None)
        return done


def _ready_session(browser=None):
    s = Session(cake.CakeSite(), browser or _NoBrowser(), lambda: PROFILE)
    items = cake.build_plan(PROFILE, {"工作經驗": ["虛構科技"], "學歷": [], "資格認證": []},
                            CAKE_DEGREES)
    s._set(stage="ready", items=items)
    return s


def test_送出前再驗一次():
    s = _ready_session()
    with pytest.raises(ValueError, match="發照機構、發照日期、到期日"):
        s.run([{"id": "certificate-1"}])
    with pytest.raises(ValueError, match="重新讀取"):
        s.run([{"id": "experience-0"}])                       # 已經有的不能再送
    with pytest.raises(ValueError, match="重新讀取"):
        s.run([{"id": "education-9"}])
    with pytest.raises(ValueError, match="沒有勾選"):
        s.run([])


def test_清單還沒好不能送():
    s = Session(cake.CakeSite(), _NoBrowser(), lambda: PROFILE)
    with pytest.raises(Busy):
        s.run([{"id": "experience-1"}])


def test_api(client, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setitem(webform._sessions, "cake", _ready_session(recorder))
    state = client.get("/api/webform/cake").json()
    assert state["stage"] == "ready" and state["site"]["label"] == "Cake"
    assert client.get("/api/webform/104").status_code == 404

    r = client.post("/api/webform/cake/run", json={"items": [{"id": "certificate-1"}]})
    assert r.status_code == 422 and "發照機構" in r.json()["detail"]
    r = client.post("/api/webform/cake/run", json={"items": [
        {"id": "certificate-1", "values": {"issuer": "勞動部", "issued": "2019/3"},
         "alts": {"expires": True}}]})
    assert r.status_code == 200                        # 補齊之後就開始存
    assert r.json()["stage"] == "running" and r.json()["progress"]["total"] == 1
    assert recorder.started == ["_run"]
