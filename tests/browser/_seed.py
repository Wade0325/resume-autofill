"""在指定的資料夾裡生一份工作，給瀏覽器測試用。

會用子行程跑是因為：瀏覽器測試的後端有自己的資料夾，而 `backend.config` 是在 import
當下讀環境變數的，同一個行程裡改不了。與其自己寫 SQL（等於把 schema 抄一份，
欄位一改就壞），不如換個行程用後端自己的 `db`。

用法：python _seed.py <資料夾> <表格路徑> <JSON 設定>，成功就印出工作代碼。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

home, form, raw = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["RESUME_AUTOFILL_HOME"] = home
os.environ["RESUME_AUTOFILL_LLM_HOST"] = "http://127.0.0.1:8099"
os.environ["RESUME_AUTOFILL_AUTOSTART"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend import config, db, service  # noqa: E402
from backend.core import filler  # noqa: E402

opts = json.loads(raw)
config.ensure_dirs()
db.init()

if opts.get("kind") == "import":
    # 匯入紀錄：沒有模型也要測得到「讀完之後畫面長怎樣」
    import_id = uuid.uuid4().hex[:12]
    service.job_dir(import_id).mkdir(parents=True, exist_ok=True)
    if opts.get("text") is not None:
        service.input_path(import_id).with_suffix(".txt").write_text(
            opts["text"], encoding="utf-8")
    db.create_import(import_id, opts.get("filename", "貼上的文字"))
    db.update_import(import_id, extracted=opts.get("extracted") or {},
                     status="ready", stage="", note=opts.get("note", ""))
    print(import_id)
    raise SystemExit(0)

if opts.get("profile") is not None:
    db.put_kv("profile", opts["profile"])

job_id = uuid.uuid4().hex[:12]
service.job_dir(job_id).mkdir(parents=True, exist_ok=True)
shutil.copy(form, service.input_path(job_id))
db.create_job(job_id, opts.get("filename", "虛構公司表格.docx"),
              status="processing", engine="vlm")

status = opts.get("status", "analyzed")
if status == "analyzed":
    _doc, _f, slots = filler.parse(Path(form))
    db.update_job(job_id, status="analyzed", fingerprint="vlm:test",
                  decided=opts.get("decided") or {},
                  anchors=[{"id": s.id, "kind": s.kind, "label": "",
                            "tick": None, "basis": None} for s in slots])
elif status == "failed":
    db.update_job(job_id, status="failed", error=opts.get("error") or "分析失敗了")
elif opts.get("stage"):
    db.update_job(job_id, stage=opts["stage"])

print(job_id)
