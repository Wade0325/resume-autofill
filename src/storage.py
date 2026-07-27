"""
本地儲存層
------------------------------------------------
所有資料都留在使用者自己的磁碟，沒有任何雲端呼叫。

  ~/.resume_autofill/
      config.json              後端、模型、門檻等設定
      profile.json             個人履歷資料（唯一一份，改一次到處生效）
      templates/<指紋>.json     範本學習成果：這家公司的表格怎麼對映
      logs/                     每次填寫的紀錄，方便回溯

檔案權限一律設為 0600（只有本人可讀寫）。若要更強的保護，
README 有說明如何改用 age 或 SQLCipher 做靜態加密。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

HOME = Path(os.environ.get("RESUME_AUTOFILL_HOME",
                           Path.home() / ".resume_autofill"))

DEFAULT_CONFIG = {
    "backend": "ollama",                 # ollama | llamacpp | null
    "model": "qwen3.5:4b",
    "host": "http://localhost:11434",
    "min_confidence": 0.60,
    "allow_sensitive": False,            # 身分證字號等，預設不自動填
    "highlight_filled": True,            # 填入的字加黃底，方便人工複核
}

PROFILE_TEMPLATE = {
    "basic": {
        "name_zh": "王小明", "name_en": "Wang, Hsiao-Ming", "gender": "男",
        "birthday": "1996-04-15", "age": "30", "birthplace": "臺北市",
        "id_number": "", "blood_type": "O", "marital_status": "未婚",
        "military": "役畢", "disability": "否", "height": "", "weight": "",
    },
    "contact": {
        "mobile": "0912-345-678", "phone_home": "02-2345-6789",
        "phone_contact": "0912-345-678", "email": "hsiaoming@example.com",
        "line_id": "hsiaoming", "address_registered": "○○市○○區○○路 1 號",
        "address_mailing": "○○市○○區○○路 1 號",
    },
    "job": {
        "applied_position": "後端工程師", "expected_salary": "依公司規定",
        "available_date": "2026-09-01", "source": "104 人力銀行", "referrer": "",
    },
    "education": [
        {"school": "國立臺灣大學", "department": "資訊工程學系", "degree": "學士",
         "period": "2014/09-2018/06", "status": "畢業"},
        {"school": "國立臺灣科技大學", "department": "資訊工程研究所", "degree": "碩士",
         "period": "2018/09-2020/06", "status": "畢業"},
    ],
    "experience": [
        {"company": "某某科技股份有限公司", "title": "軟體工程師",
         "period": "2020/08-2023/12", "description": "後端 API 開發與維運",
         "salary": "", "leave_reason": "生涯規劃"},
        {"company": "另一家科技公司", "title": "資深後端工程師",
         "period": "2024/01-2026/06", "description": "微服務架構設計",
         "salary": "", "leave_reason": "尋求新挑戰"},
    ],
    "skills": {
        "languages": "英文（多益 850）、日文（N2）",
        "certificates": "AWS Solutions Architect Associate",
        "computer": "Python、Go、PostgreSQL、Docker、Kubernetes",
        "driver_license": "普通小型車",
        "specialty": "分散式系統設計、效能調校",
    },
    "emergency": {"name": "王大明", "relation": "父子", "phone": "0922-111-222"},
    "autobiography": "（請在此填寫你的自傳……）",
    "motivation": "（請在此填寫應徵動機……）",
}


def _secure_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)      # 0600
    except OSError:
        pass


def init_home() -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "templates").mkdir(exist_ok=True)
    (HOME / "logs").mkdir(exist_ok=True)
    if not (HOME / "config.json").exists():
        _secure_write(HOME / "config.json", DEFAULT_CONFIG)
    if not (HOME / "profile.json").exists():
        _secure_write(HOME / "profile.json", PROFILE_TEMPLATE)
    try:
        os.chmod(HOME, stat.S_IRWXU)                     # 0700
    except OSError:
        pass
    return HOME


def load_config() -> Dict[str, Any]:
    p = HOME / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


def load_profile(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path) if path else HOME / "profile.json"
    if not p.exists():
        raise FileNotFoundError(f"找不到個人資料檔 {p}，請先執行 `resume-autofill init`")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------- 範本快取 ----------------
def template_path(fingerprint: str) -> Path:
    return HOME / "templates" / f"{fingerprint}.json"


def load_template(fingerprint: str) -> Dict[str, str]:
    p = template_path(fingerprint)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("mapping", {})


def save_template(fingerprint: str, mapping: Dict[str, str],
                  source_name: str = "") -> None:
    _secure_write(template_path(fingerprint),
                  {"fingerprint": fingerprint,
                   "source": source_name,
                   "mapping": mapping})
