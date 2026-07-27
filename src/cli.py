"""
命令列介面
------------------------------------------------
  resume-autofill init                    建立 ~/.resume_autofill/ 與範例個人資料
  resume-autofill inspect 表格.docx        只解析、不寫檔，看看它認出哪些欄位
  resume-autofill fill 表格.docx -o 完成.docx
  resume-autofill fill 表格.docx --dry-run 只列出計畫
  resume-autofill map 表格.docx tbl0.r1.c1=basic.name_zh   手動修正並記住
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import extractor, matcher, storage, writer
from .llm import get_backend
from .schema import BY_KEY, FIELD_KEYS


def _print_table(rows, headers):
    if not rows:
        print("  （無）")
        return
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    line = "  " + " │ ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  " + "─┼─".join("─" * w for w in widths))
    for r in rows:
        print("  " + " │ ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def cmd_init(args):
    home = storage.init_home()
    print(f"✓ 已建立 {home}")
    print(f"  請編輯 {home/'profile.json'} 填入你自己的資料")
    print(f"  設定檔在 {home/'config.json'}（可切換模型與後端）")


def cmd_inspect(args):
    data = extractor.extract(args.file)
    print(f"檔案：{args.file}")
    print(f"範本指紋：{data['fingerprint']}")
    print(f"偵測到 {len(data['anchors'])} 個可填位置\n")
    rows = [[a["id"], a["kind"], a["label"][:22],
             "、".join(a.get("options", []))[:18]] for a in data["anchors"]]
    _print_table(rows, ["anchor_id", "型態", "標籤", "選項"])
    if args.json:
        Path(args.json).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ 已輸出 {args.json}")


def cmd_fill(args):
    cfg = storage.load_config()
    if args.model:
        cfg["model"] = args.model
    if args.backend:
        cfg["backend"] = args.backend

    profile = storage.load_profile(args.profile)
    data = extractor.extract(args.file)
    anchors = data["anchors"]
    fp = data["fingerprint"]
    cached = storage.load_template(fp)

    backend = get_backend(cfg)
    print(f"範本指紋 {fp} ｜ 快取 {len(cached)} 筆 ｜ LLM 後端 {backend.name}"
          f"（{cfg.get('model')}）")

    ops, skipped = matcher.resolve(
        anchors, profile, backend,
        cached_map=cached,
        min_confidence=cfg["min_confidence"],
        allow_sensitive=cfg["allow_sensitive"] or args.allow_sensitive,
    )

    print(f"\n【將填入 {len(ops)} 格】")
    _print_table([[o.anchor["id"], o.anchor["label"][:14], o.field_key,
                   str(o.value)[:24], f"{o.confidence:.2f}", o.source] for o in ops],
                 ["anchor", "標籤", "欄位", "值", "信心", "來源"])

    print(f"\n【略過 {len(skipped)} 格】")
    _print_table([[s.anchor["id"], s.anchor["label"][:14], s.note] for s in skipped],
                 ["anchor", "標籤", "原因"])

    if args.dry_run:
        print("\n(--dry-run：未寫入任何檔案)")
        return

    out = args.output or str(Path(args.file).with_name(
        Path(args.file).stem + "_已填寫.docx"))
    result = writer.apply_ops(args.file, out, ops,
                              highlight=cfg.get("highlight_filled", True))
    print(f"\n✓ 已輸出 {out}（成功 {result['written']} 格，失敗 {result['failed']} 格）")

    # 學習：把這次的決策存成範本快取，下次同一份表格 0 成本秒填
    if not args.no_learn:
        mapping = dict(cached)
        mapping.update({o.anchor["id"]: o.field_key for o in ops})
        storage.save_template(fp, mapping, source_name=Path(args.file).name)
        print(f"✓ 已記住此範本（{len(mapping)} 筆對映），下次不需再呼叫模型")

    print("\n⚠ 請務必開啟輸出檔人工複核後再送出；黃色底色處為自動填入。")


def cmd_map(args):
    data = extractor.extract(args.file)
    fp = data["fingerprint"]
    mapping = storage.load_template(fp)
    ids = {a["id"] for a in data["anchors"]}
    for pair in args.pairs:
        if "=" not in pair:
            print(f"✗ 格式應為 anchor_id=field_key：{pair}")
            return 1
        aid, key = pair.split("=", 1)
        if aid not in ids:
            print(f"✗ 找不到 anchor_id {aid}")
            return 1
        if key not in FIELD_KEYS:
            print(f"✗ 未知欄位代碼 {key}")
            return 1
        mapping[aid] = key
        print(f"✓ {aid} → {key}（{BY_KEY[key].label if key in BY_KEY else key}）")
    storage.save_template(fp, mapping, source_name=Path(args.file).name)
    print(f"已寫入範本快取 {fp}")
    return 0


def cmd_fields(args):
    _print_table([[f.key, f.label, "、".join(f.aliases[:4])] for f in BY_KEY.values()],
                 ["欄位代碼", "名稱", "常見別名"])


def build_parser():
    p = argparse.ArgumentParser(prog="resume-autofill",
                                description="本地端 Word 履歷表自動填寫工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化本機資料夾").set_defaults(func=cmd_init)

    s = sub.add_parser("inspect", help="解析表格結構，不修改檔案")
    s.add_argument("file")
    s.add_argument("--json", help="把解析結果另存成 JSON")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("fill", help="自動填寫")
    s.add_argument("file")
    s.add_argument("-o", "--output")
    s.add_argument("--profile", help="指定個人資料檔")
    s.add_argument("--model", help="覆寫模型名稱，例如 Qwen3.5-9B-Instruct-Q4_K_M")
    s.add_argument("--backend", choices=["llamacpp", "null"])
    s.add_argument("--dry-run", action="store_true", help="只列出計畫不寫檔")
    s.add_argument("--allow-sensitive", action="store_true",
                   help="允許填入身分證字號等敏感欄位")
    s.add_argument("--no-learn", action="store_true", help="不要把結果存入範本快取")
    s.set_defaults(func=cmd_fill)

    s = sub.add_parser("map", help="手動修正對映並記住")
    s.add_argument("file")
    s.add_argument("pairs", nargs="+", metavar="anchor_id=field_key")
    s.set_defaults(func=cmd_map)

    sub.add_parser("fields", help="列出所有支援欄位").set_defaults(func=cmd_fields)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
