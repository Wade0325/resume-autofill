"""「我的資料」攤平成欄位、以及哪些欄位會列給模型挑。

最重要的一件事在最下面：清單多一項就會掉格，所以少見的欄位一律「表格提到才列出來」。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from docx import Document

from backend import db, service
from backend.core import document, filler, planner, writer

# 把新欄位都填滿的一份虛構資料——沒填值的欄位本來就不會列出來，
# 拿沒填的去測「有問就給」等於什麼都沒測
FULL = {
    "basic": {"name_zh": "虛構甲", "gender": "男", "children": "2",
              "birthday": "1996年04月15日",
              "military_branch": "陸軍", "military_rank": "下士",
              "military_start": "105年09月01日", "military_end": "106年08月31日"},
    "contact": {"mobile": "0900-000-000", "address_mailing": "虛構市虛構路 1 號",
                "postal_mailing": "100", "postal_household": "200"},
    "certificate": [{"name": "虛構證照", "issuer": "虛構機構",
                     "issued": "110年01月01日", "expires": "115年01月01日"}],
    "language": [{"name": "英文", "level": "中等", "listening": "中等",
                  "speaking": "中等", "reading": "精通", "writing": "中等"}],
    "preference": {"job_type": "全職", "industry": "資訊服務", "role": "後端工程師",
                   "shift": "可配合", "travel": "可短期出差"},
    "qa": {"strengths": "做事仔細", "weaknesses": "容易鑽牛角尖",
           "career_plan": "三年內成為資深工程師", "why_apply": "想做有規模的系統"},
    "experience": [{"company": "虛構科技", "title": "工程師",
                    "start": "2021年01月01日", "end": "至今"}],
}

# 少見的欄位：表格沒問到就不該列給模型挑
GATED = [
    "contact.postal_mailing", "contact.postal_household",
    "basic.military_branch", "basic.military_rank",
    "basic.military_start", "basic.military_end", "basic.military_period",
    "basic.children", "basic.total_tenure",
    "certificate[0].issued", "certificate[0].expires",
    "language[0].name", "preference.job_type", "qa.strengths",
]


def two_column_form(tmp_path: Path, labels: list[str]) -> Path:
    """左邊印標籤、右邊空著的表格。"""
    doc = Document()
    table = doc.add_table(rows=len(labels), cols=2)
    for i, label in enumerate(labels):
        table.cell(i, 0).text = label
    path = tmp_path / "form.docx"
    doc.save(path)
    return path


def form_with(tmp_path: Path, labels: list[str]):
    """只印著那些標籤的兩欄表格，照看版面那條路解析好。"""
    _doc, form, _slots = filler.parse(two_column_form(tmp_path, labels))
    return form


class TestDerived:
    """算出來的欄位：存著的值不算數，一律重算。"""

    def test_age_from_birthday(self):
        fields = filler.fields_of({"basic": {"birthday": "1996年04月15日"}})
        assert fields["basic.age"].isdigit()
        assert int(fields["basic.age"]) >= 29      # 存著的年齡去年填今年就錯了

    def test_stored_age_is_ignored(self):
        fields = filler.fields_of({"basic": {"birthday": "1996年04月15日", "age": "18"}})
        assert fields["basic.age"] != "18"

    def test_military_period_from_start_end(self):
        fields = filler.fields_of(FULL)
        assert "~" in fields["basic.military_period"]

    def test_total_tenure_follows_experience(self):
        one = filler.fields_of({"experience": [
            {"start": "2020年01月01日", "end": "2021年12月31日"}]})
        two = filler.fields_of({"experience": [
            {"start": "2018年01月01日", "end": "2021年12月31日"}]})
        assert one["basic.total_tenure"] != two["basic.total_tenure"]

    def test_unreadable_dates_are_not_guessed(self):
        fields = filler.fields_of({"basic": {"birthday": "不知道"}})
        assert "basic.age" not in fields

    @pytest.mark.parametrize("start,end", [
        ("2018年", "2020年"),            # 只有年份：DATE_RE 要年＋月
        ("2018", "2020"),
        ("民國107年", "民國109年"),
        ("不知道", "還在職"),
    ])
    def test_year_only_dates_do_not_crash(self, start, end):
        """使用者很自然會打「2018年」。以前 _months 回空字串，divmod 當場拋 TypeError，
        而 fields_of 在分析與匯出都會跑——等於每一次都當掉。"""
        fields = filler.fields_of({"experience": [
            {"company": "虛構公司", "title": "虛構職稱", "start": start, "end": end}]})
        assert "experience[0].tenure" not in fields    # 算不出來就不寫，不猜
        assert "basic.total_tenure" not in fields

    def test_total_tenure_skips_rather_than_undercounts(self):
        """一筆算得出來、一筆算不出來時，總年資要空著而不是只算得出來的那一筆。

        少算的年資會直接寫進履歷而且沒有任何提示，比空著更糟。
        """
        both = filler.fields_of({"experience": [
            {"start": "2018年01月01日", "end": "2019年12月31日"},
            {"start": "2020年", "end": "2021年"}]})
        assert "basic.total_tenure" not in both

    def test_row_without_dates_does_not_block_total(self):
        """整筆沒寫起訖的不算數：那是沒有主張期間，不是算不出來。"""
        fields = filler.fields_of({"experience": [
            {"start": "2018年01月01日", "end": "2019年12月31日"},
            {"company": "還沒填日期的那一筆"}]})
        assert fields["basic.total_tenure"] == "2年"

    def test_numeric_dates_do_not_crash(self):
        """手改過的備份可能把年份存成數字。以前直接拿去跑正規式，整個拋 TypeError。"""
        fields = filler.fields_of({"experience": [{"start": 2018, "end": 2020}]})
        assert fields["experience[0].period"] == "2018~2020"

    def test_roc_start_is_not_counted_as_year_110(self):
        """沒寫民國的「110年01月」接「至今」，以前直接相減寫出「1916年9個月」。"""
        fields = filler.fields_of({"experience": [{"start": "110年01月01日", "end": "至今"}]})
        assert fields["experience[0].period"] == "110年1月1日~至今"
        assert "experience[0].tenure" not in fields
        assert "basic.total_tenure" not in fields

    def test_roc_prefixed_dates_count_normally(self):
        """寫明民國的換成西元再算，跟另一端是西元年也能相減。"""
        fields = filler.fields_of({"experience": [
            {"start": "民國105年09月", "end": "2020年03月"},
            {"start": "105年09月", "end": "106年08月"}]})
        assert fields["experience[0].tenure"] == "3年7個月"
        assert fields["experience[1].tenure"] == "1年"          # 兩端都是民國年，相減照樣對
        assert fields["basic.total_tenure"] == "4年7個月"


def classic_job(tmp_path: Path, decided: dict) -> str:
    """讀文字那條路分析完的工作（位置照 ParsedDoc 存），不必跑模型。表格印著中文姓名、應徵職務。"""
    form = two_column_form(tmp_path, ["中文姓名", "應徵職務"])
    _text, slots = document.ParsedDoc(str(form)).flatten()
    job_id = uuid.uuid4().hex[:12]
    service.job_dir(job_id).mkdir(parents=True, exist_ok=True)
    shutil.copy(form, service.input_path(job_id))
    db.create_job(job_id, "虛構公司表格.docx", status="processing", engine="classic")
    db.update_job(job_id, status="analyzed", fingerprint="classic:test", decided=decided,
                  anchors=[s.to_dict() for s in slots])
    return job_id


class TestReadingTextRoute:
    """讀文字那條路（planner）取算出來的欄位，要跟 fields_of 同一套算法。

    以前 planner 另有一套合成規則：年齡只有生日一個來源，卻被當成起訖兩欄拆開，
    表格一印「年齡」整份分析就當掉；存著的舊年齡、舊期間也照用。
    """

    def test_age_without_birthday_does_not_crash(self):
        assert planner.get_value({"basic": {}}, "basic.age") == ""

    @pytest.mark.parametrize("key,flat", [
        ("basic.age", "basic.age"),
        ("basic.total_tenure", "basic.total_tenure"),
        ("basic.military_period", "basic.military_period"),
        ("education[].period", "education[0].period"),
        ("experience[].period", "experience[0].period"),
        ("experience[].tenure", "experience[0].tenure"),
    ])
    def test_same_as_fields_of(self, key, flat):
        profile = {**FULL, "education": [{"start": "2014年09月01日", "end": "2018年06月30日"}]}
        assert planner.get_value(profile, key, 0) == filler.fields_of(profile)[flat]

    def test_stored_values_do_not_count(self):
        profile = {"basic": {"birthday": "1996年04月15日", "age": "18"},
                   "education": [{"start": "2014年09月01日", "end": "2018年06月30日",
                                  "period": "舊的期間"}]}
        assert planner.get_value(profile, "basic.age") == filler.fields_of(profile)["basic.age"]
        assert planner.get_value(profile, "education[].period", 0) == "2014年9月1日~2018年6月30日"

    def test_form_printing_age(self, tmp_path):
        """照讀文字那條路實際的順序走：標籤錨定 → 產生計畫。標籤都在對照表裡，不必問模型。"""
        parsed = document.ParsedDoc(str(two_column_form(tmp_path, ["中文姓名", "年齡"])))
        _text, slots = parsed.flatten()
        decisions = planner.decide_by_anchor(parsed.table_texts(), slots,
                                             "http://127.0.0.1:8099", "虛構模型", {},
                                             headers=parsed.slot_headers(slots))
        assert "basic.age" in {d.field_key for d in decisions.values()}

        ops, skipped = planner.build_plan(slots, {"basic": {"name_zh": "虛構甲"}}, decisions)
        assert [o.value for o in ops] == ["虛構甲"]          # 沒填生日：年齡留白，其餘照填
        assert [s.field_key for s in skipped] == ["basic.age"]

        profile = {"basic": {"name_zh": "虛構甲", "birthday": "1996年04月15日"}}
        ops, _ = planner.build_plan(slots, profile, decisions)
        assert [o.value for o in ops] == ["虛構甲", filler.fields_of(profile)["basic.age"]]

    def test_plan_page_with_age_but_no_birthday(self, client, make_job):
        """看版面那條路取不到值時也會退回 planner.get_value，一樣不能當掉。"""
        db.put_kv("profile", {"basic": {"name_zh": "虛構甲"}})
        slot = make_job.blanks[0]
        job_id = make_job(decided={slot.id: ["basic.age", 0, "model", "年齡"]})
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        row = next(i for i in r.json()["plan"]["items"] if i["slot_id"] == slot.id)
        assert row["status"] == "skip"

    @pytest.mark.parametrize("printed,expected", [
        ("就學期間：　　年　　月－　　年　　月", "就學期間： 2014 年 9 月－ 2018 年 6 月"),
        ("年　　月－　　年　　月", "2014年 9 月－ 2018 年 6 月"),
        ("自　　年　　月至　　年　　月", "自 2014 年 9 月至 2018 年 6 月"),
        ("　　年　　月～　　年　　月", " 2014 年 9 月～ 2018 年 6 月"),
    ])
    def test_period_into_printed_units(self, printed, expected):
        """期間值用「~」接起訖，表格印的分隔字不一定是它。以前對不齊，
        後面的單位被擠進最後一格：「2018 年 6月 月」。"""
        profile = {"education": [{"start": "2014年09月", "end": "2018年06月"}]}
        para = Document().add_paragraph(printed)
        assert writer._fill_print(para, filler.fields_of(profile)["education[0].period"], False)
        assert para.text == expected

    @pytest.mark.parametrize("printed,value,expected", [
        ("總年資：　　年　　月", "10個月", "總年資： 0 年 10 月"),
        ("總年資：　　年　　月", "2年10個月", "總年資： 2 年 10 月"),
        ("總年資：　　年", "3年", "總年資： 3 年"),
        ("共　　個月", "2年10個月", "共 34 個月"),
    ])
    def test_tenure_into_printed_units(self, printed, value, expected):
        """年資照表格印的單位寫。以前「10個月」寫成「10 年 個 月」，看起來是十年。"""
        para = Document().add_paragraph(printed)
        assert writer._fill_print(para, value, False)
        assert para.text == expected

    def test_tenure_that_does_not_fit_stays_blank(self):
        """只印「　年」卻有零頭的月數：寫「2 年」會少報年資，寧可留白。"""
        para = Document().add_paragraph("總年資：　　年")
        assert not writer._fill_print(para, "2年10個月", False)
        assert para.text == "總年資：　　年"

    def test_printed_period_end_to_end(self, tmp_path):
        """照讀文字那條路實際的順序走到寫檔：「就學期間」由對照表錨定，不必問模型。"""
        doc = Document()
        doc.add_table(rows=1, cols=2).cell(0, 0).text = "中文姓名"
        doc.add_paragraph("")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "就學期間：　　年　　月－　　年　　月"
        src = tmp_path / "form.docx"
        doc.save(src)
        parsed = document.ParsedDoc(str(src))
        _text, slots = parsed.flatten()
        decisions = planner.decide_by_anchor(parsed.table_texts(), slots,
                                             "http://127.0.0.1:8099", "虛構模型", {},
                                             headers=parsed.slot_headers(slots))
        profile = {"basic": {"name_zh": "虛構甲"},
                   "education": [{"start": "2014年09月", "end": "2018年06月"}]}
        ops, _ = planner.build_plan(slots, profile, decisions)
        out = tmp_path / "out.docx"
        assert writer.apply_ops(str(src), str(out), ops)["failed"] == 0
        cell = Document(out).tables[1].cell(0, 0).text
        assert cell == "就學期間： 2014 年 9 月－ 2018 年 6 月"

    def test_fields_of_once_per_plan(self, monkeypatch):
        """以前每一格算出來的欄位都重算一次整份 fields_of，一份計畫慢一百倍——
        批次面板分析期間每兩秒就要對每份工作算一次。"""
        calls = []
        real = filler.fields_of
        monkeypatch.setattr(filler, "fields_of", lambda p: calls.append(1) or real(p))
        slots = [document.Slot(id=f"s{i}", kind="cell") for i in range(3)]
        decisions = {"s0": planner.Decision("basic.age", 0, "rule", "年齡"),
                     "s1": planner.Decision("experience[].period", 0, "rule", "期間"),
                     "s2": planner.Decision("basic.total_tenure", 0, "rule", "總年資")}
        ops, _ = planner.build_plan(slots, FULL, decisions)
        assert len(ops) == 3
        assert len(calls) == 1

    def test_dates_written_like_the_layout_route(self):
        """日期跟看版面那條路同一種寫法：寫明民國的換西元，生日曆制是民國的也換西元。
        以前原樣照抄，「民國109年3月」寫進「至　年　月」變成「至 民國 年 1093 月」。"""
        profile = {"basic": {"birthday": "85年04月15日", "birthday_era": "民國"},
                   "experience": [{"start": "2016/9", "end": "民國109年3月"}]}
        slots = [document.Slot(id=f"s{i}", kind="cell") for i in range(3)]
        decisions = {"s0": planner.Decision("basic.birthday", 0, "rule", "出生日期"),
                     "s1": planner.Decision("experience[].start", 0, "rule", "到職"),
                     "s2": planner.Decision("experience[].end", 0, "rule", "離職")}
        ops, _ = planner.build_plan(slots, profile, decisions)
        assert [o.value for o in ops] == ["1996年4月15日", "2016年9月", "2020年3月"]

    def test_own_age_not_written_into_family_rows(self, tmp_path):
        """家庭狀況表的「年齡」是家人的年齡，對照表卻不看上下文、把它對到本人的年齡。
        以前算年齡會當掉，蓋住了這件事；修好之後就變成把本人年齡寫進第一位家人那一列。"""
        doc = Document()
        doc.add_table(rows=1, cols=2).cell(0, 0).text = "中文姓名"
        doc.add_paragraph("")
        family = doc.add_table(rows=3, cols=4)
        for c, label in enumerate(["家人稱謂", "家人姓名", "年齡", "家人職業"]):
            family.cell(0, c).text = label
        src = tmp_path / "family.docx"
        doc.save(src)
        parsed = document.ParsedDoc(str(src))
        _text, slots = parsed.flatten()
        decisions = planner.decide_by_anchor(parsed.table_texts(), slots,
                                             "http://127.0.0.1:8099", "虛構模型", {},
                                             headers=parsed.slot_headers(slots))
        in_family = {d.field_key for sid, d in decisions.items() if sid.startswith("tbl1.")}
        assert "basic.age" not in in_family
        assert {"family[].relation", "family[].name", "family[].occupation"} <= in_family

    def test_apply_panel_reaches_the_file(self, tmp_path, text_of):
        """學過的舊格式把應徵職務記成不填，面板填了就要填。以前只有計畫頁照面板走，
        計畫說會填，預覽與下載的檔案卻是空的。"""
        job_id = classic_job(tmp_path, {"tbl0.r0.c1": ["basic.name_zh", 0, "rule", "中文姓名"],
                                        "tbl0.r1.c1": ["__UNKNOWN__", 0, "cache", "應徵職務"]})
        plan = service.set_apply(job_id, {"job.title": "虛構職務"})
        row = next(i for i in plan.items if i.slot_id == "tbl0.r1.c1")
        assert (row.value, row.status, row.note) == ("虛構職務", "fill", "這次應徵")
        assert "虛構職務" in text_of(service.preview_docx(job_id, "filled", highlight=False))
        service.write_output(job_id)
        assert "虛構職務" in text_of(service.output_path(job_id))

    def test_empty_apply_panel_points_to_the_panel(self, tmp_path):
        """沒填的應徵職務標「這次應徵沒填」，面板才會展開提醒。以前寫「個人資料中此欄位為空」，
        按「去填寫」會到我的資料，那裡根本沒有這個欄位。"""
        job_id = classic_job(tmp_path, {"tbl0.r1.c1": ["job.title", 0, "rule", "應徵職務"]})
        row = next(i for i in service.get_plan(job_id).items if i.slot_id == "tbl0.r1.c1")
        assert (row.status, row.note) == ("skip", "這次應徵沒填")

    def test_per_job_keys_match_the_label_table(self):
        """「只算那一份工作」的欄位宣告在兩處（FieldSpec 與欄名表），
        漏一邊就有一邊照舊存進我的資料。"""
        from backend.core.schema import PER_JOB_KEYS, PER_JOB_LABELS
        assert PER_JOB_KEYS == set(PER_JOB_LABELS.values())


class TestGating:
    """表格提到才列給模型挑。"""

    @pytest.mark.parametrize("key", GATED)
    def test_not_offered_when_form_never_asks(self, key, tmp_path):
        plain = filler.usable_fields(form_with(tmp_path, ["姓名", "行動電話", "地址"]), FULL)
        assert key not in plain

    @pytest.mark.parametrize("labels,expected", [
        (["通訊地址", "郵遞區號"], ["contact.postal_mailing", "contact.postal_household"]),
        (["證照名稱", "發照日期"], ["certificate[0].issued"]),
        (["證照名稱", "有效期限"], ["certificate[0].expires"]),
        (["軍種", "軍階", "入伍日期", "退伍日期"],
         ["basic.military_branch", "basic.military_rank",
          "basic.military_start", "basic.military_end"]),
        (["服役期間"], ["basic.military_period"]),
        (["子女人數"], ["basic.children"]),
        (["總年資"], ["basic.total_tenure"]),
        (["語文能力", "聽", "說", "讀", "寫"], ["language[0].name", "language[0].listening"]),
        (["期望工作型態", "可否輪班"], ["preference.job_type", "preference.shift"]),
        (["您的優點", "生涯規劃"], ["qa.strengths", "qa.career_plan"]),
    ])
    def test_offered_when_form_asks(self, labels, expected, tmp_path):
        fields = filler.usable_fields(form_with(tmp_path, labels), FULL)
        for key in expected:
            assert key in fields

    def test_asking_half_offers_half(self, tmp_path):
        fields = filler.usable_fields(form_with(tmp_path, ["發照日期"]), FULL)
        assert "certificate[0].issued" in fields
        assert "certificate[0].expires" not in fields

    def test_other_peoples_data_still_gated(self, tmp_path):
        """家人、諮詢人本來就是這樣擋的，不能因為改寫而壞掉。"""
        profile = {**FULL, "family": [{"name": "虛構乙", "relation": "父"}]}
        plain = filler.usable_fields(form_with(tmp_path, ["姓名"]), profile)
        family = filler.usable_fields(form_with(tmp_path, ["家庭成員", "稱謂"]), profile)
        assert "family[0].name" not in plain
        assert "family[0].name" in family


class TestRowChoices:
    """一列一筆的表：另外一條規則——表上有問、自己也真的填了，才列出來。"""

    def test_filled_is_offered(self):
        assert filler._worth_offering(
            "certificate[].issued", {"certificate[0].issued": "110年01月01日"})

    def test_blank_is_not_offered(self):
        """履歷表印著「發照日期」但沒填，列出來只會佔掉一欄。"""
        assert not filler._worth_offering("certificate[].issued", {"certificate[0].name": "證照"})

    def test_ungated_fields_are_always_offered(self):
        assert filler._worth_offering("family[].contact", {})
        assert filler._worth_offering("experience[].salary", {})
