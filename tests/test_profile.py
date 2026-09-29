"""我的資料的存檔規則：所有寫入（存檔、匯入、還原版本、從檔案還原）都經過 profiles.save。

兩件事以前只在讀的那一端擋，寫的那一端照收：
應徵職務、工作地點不是我的資料；日期要是字，不能是數字。
"""
from __future__ import annotations

from backend import db

BACKUP = "resume-autofill-profile"


class TestPerJobNeverStored:
    """應徵職務、工作地點只算那一份工作。我的資料頁看不到也刪不掉，存進去就一直在：
    看版面那條路把它列給模型挑、備份檔帶著它到另一台電腦。"""

    def test_saving_drops_them(self, client, profile):
        """我的資料頁存檔會把整份送回來，連頁面上看不到的欄位。"""
        profile["job"].update({"title": "舊履歷上的職務", "location": "虛構市"})
        assert client.put("/api/profile", json=profile).status_code == 200
        job = db.get_kv("profile")["job"]
        assert "title" not in job and "location" not in job
        assert job["expected_salary"] == "面議"          # 希望待遇是我的資料，照存

    def test_restoring_a_backup_drops_them(self, client, profile):
        profile["job"]["title"] = "舊履歷上的職務"
        r = client.post("/api/profile/restore",
                        json={"format": BACKUP, "version": 1, "profile": profile})
        assert r.status_code == 200
        assert "title" not in r.json()["job"]
        assert "title" not in db.get_kv("profile")["job"]

    def test_upgrade_cleans_what_old_imports_stored(self, profile):
        """舊版匯入存進去的，現在的存檔規則擋不到，升級時整理一次。"""
        profile["job"]["title"] = "以前誤存的職務"
        db.put_kv("profile", profile)
        with db.connect() as conn:
            db._v5(conn)
        job = db.get_kv("profile")["job"]
        assert "title" not in job
        assert job["expected_salary"] == "面議"


class TestDatesAreText:
    """數字日期會讓我的資料頁整頁當掉（連還原的按鈕都按不到），小數還會讀錯月份。"""

    def test_whole_years_become_text(self, client):
        r = client.put("/api/profile",
                       json={"experience": [{"company": "虛構公司", "start": 2018, "end": 2020}]})
        assert r.status_code == 200
        row = db.get_kv("profile")["experience"][0]
        assert (row["start"], row["end"]) == ("2018", "2020")

    def test_decimal_dates_are_refused(self, client):
        """「2018.10」讀進來已經是 2018.1：存下去就變成一月，所以一開始就不收，並說明原因。"""
        body = {"experience": [{"company": "虛構公司", "start": 2018.10, "end": 2020.05}]}
        r = client.put("/api/profile", json=body)
        assert r.status_code == 422
        assert "日期要用文字寫" in r.json()["detail"]
        r = client.post("/api/profile/restore", json={"format": BACKUP, "profile": body})
        assert r.status_code == 422
        assert db.get_kv("profile")["experience"][0]["company"] != "虛構公司"   # 沒被蓋掉

    def test_upgrade_turns_stored_numbers_into_text(self):
        db.put_kv("profile", {"experience": [{"company": "虛構公司", "start": 2018, "end": 2020}]})
        with db.connect() as conn:
            db._v5(conn)
        row = db.get_kv("profile")["experience"][0]
        assert (row["start"], row["end"]) == ("2018", "2020")
