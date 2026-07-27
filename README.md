# resume-autofill

**本地端、開源、AI 輔助的 Word 履歷表自動填寫工具。**

你的個人資料只填一次，之後每家公司寄來的不同格式 `.docx` 履歷表，都能自動辨識欄位並填入，
且完整保留原始版面與字型。全程離線，沒有任何資料離開你的電腦。

---

## 1. 設計核心：AI 只做「一題選擇題」

多數人的第一直覺是「把整份 Word 丟給大模型，叫它輸出填好的檔案」。這條路在實務上會壞掉：

| 全交給 LLM 生成 | 本專案的作法 |
|---|---|
| 需要 30B 以上模型才勉強穩定 | 2B～4B 小模型就夠 |
| 版面、字型、表格常被破壞 | 只改 `<w:t>` 文字節點，版面 100% 原封不動 |
| 每次結果不一樣，無法稽核 | 每一格都有 anchor→欄位 的對映紀錄，可審可改 |
| 每次都要重跑，很慢 | 同一份範本第二次起走快取，0 次模型呼叫 |
| 模型可能捏造你的學經歷 | 值一律取自 `profile.json`，模型碰不到值 |

**分工原則：結構問題交給程式，語意問題交給 AI。**

```
   .docx
     │
     ▼
[1] 結構解析（純 Python，零 AI）
     偵測：內容控制項 / 舊式表單欄位 / 表格空白格 / 段落填空 / 勾選框
     產出：42 個 anchor，每個帶「標籤文字 + 精確座標」
     │
     ▼
[2] 三層比對（成本由低到高）
     ├─ 範本快取   這份表格看過 → 直接沿用          0 成本
     ├─ 規則比對   別名表：「姓　名」「Name」→ basic.name_zh   0 成本
     └─ LLM 語意   只剩下 3~10 個怪標籤才呼叫模型
                   受 JSON Schema + enum 約束，模型只能「選」不能「編」
     │
     ▼
[3] 保留格式寫回
     合併 run → 只替換目標文字 → 填入處加黃底供人工複核
     │
     ▼
   完成.docx  ＋ 一份「填了什麼／略過什麼」的稽核清單
```

在內建的測試範本上，**光靠第 1、2 層就解掉 42 格中的 33 格**，LLM 只是用來收尾。
這就是為什麼小模型足夠：它面對的不是「生成一份履歷」，而是「這個標籤屬於哪個欄位」的選擇題。

---

## 2. 安裝

```bash
# 1) 取得程式
git clone <your-repo> resume-autofill && cd resume-autofill
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .

# 2) 安裝本地模型（擇一，見第 4 節）
#    Ollama 路線（最簡單）
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:4b

# 3) 初始化
resume-autofill init
#    然後編輯 ~/.resume_autofill/profile.json 填入你自己的資料
```

## 3. 使用

```bash
# 先看它認出哪些欄位（不改任何檔案）
resume-autofill inspect 台積電應徵表.docx

# 試填，只印計畫不寫檔
resume-autofill fill 台積電應徵表.docx --dry-run

# 正式填寫
resume-autofill fill 台積電應徵表.docx -o 台積電_已填寫.docx

# 有一格對錯了？手動修正，之後這份範本就永遠記得
resume-autofill map 台積電應徵表.docx tbl0.r3.c1=contact.address_mailing

# 列出所有支援欄位代碼
resume-autofill fields
```

舊的 `.doc` 檔請先轉檔（LibreOffice 是開源的）：

```bash
soffice --headless --convert-to docx 舊表格.doc
```

---

## 4. 本地模型怎麼選

本工具的模型需求只有兩項：**中文語意理解**、**遵守 JSON Schema**。不需要長文生成、不需要推理。
所以應該挑「同等參數下最聰明」的小模型，而不是硬上大模型。

| 硬體條件 | 建議模型 | 量化 | 約需記憶體 | 說明 |
|---|---|---|---|---|
| 8 GB RAM、無獨顯 | `qwen3.5:2b` | Q4_K_M | ~2 GB | 分類任務表現穩，能跑就能用 |
| **16 GB RAM 或 8 GB VRAM（預設推薦）** | **`qwen3.5:4b`** | Q4_K_M | ~3 GB | 中文強、原生多模態，5B 以下最佳選擇 |
| 16 GB+ VRAM / Apple Silicon 24 GB+ | `qwen3.5:9b` | Q4_K_M | ~6 GB | 遇到極端怪格式時的保險 |
| 偏好 Google 生態 | `gemma4:e4b` | QAT | ~3 GB | 邊緣裝置取向，中文略遜於 Qwen |
| 完全不想裝模型 | `--backend null` | — | 0 | 只跑規則層，剩下的手動補 |

切換模型只要改 `~/.resume_autofill/config.json` 的 `model` 欄位，或加 `--model qwen3.5:9b`。

### 為什麼用受限解碼（constrained decoding）

程式不是用 prompt 拜託模型「請回傳 JSON」，而是把 JSON Schema 交給推論引擎：

* Ollama → `format` 參數帶 JSON Schema
* llama.cpp → `response_format.json_schema`（或 `--grammar` GBNF）

引擎在每一步只允許符合 schema 的 token 被取樣，所以「輸出不是合法 JSON」或
「模型發明了 `basic.favorite_food` 這種不存在的欄位」在機制上就不可能發生。
`field_key` 被鎖成 enum，這也是 2B 模型就能勝任的關鍵。

搭配 llama.cpp 時：

```bash
llama-server -m qwen3.5-4b-instruct-Q4_K_M.gguf -c 8192 --port 8080 --jinja
resume-autofill fill 表格.docx --backend llamacpp
```

---

## 5. 隱私與資料保護

* 全程離線，除了你自己啟動的 `localhost` 推論服務外沒有任何網路連線
* `~/.resume_autofill/` 目錄權限 `0700`，內部檔案 `0600`
* **身分證字號、身高體重、婚姻狀況、身心障礙等欄位預設不自動填**
  （見 `schema.py` 的 `SENSITIVE_KEYS`），需要時才加 `--allow-sensitive`
* 想要靜態加密，兩個開源選項：
  * [`age`](https://github.com/FiloSottile/age)：`age -p profile.json > profile.json.age`，使用前解密
  * SQLCipher：把 `profile.json` 換成加密 SQLite，適合做成長期產品

---

## 6. 專案結構

```
src/resume_autofill/
  schema.py      標準欄位定義＋別名表（要擴充欄位改這裡就好）
  extractor.py   docx → anchor 清單（純結構解析，不含 AI）
  matcher.py     三層比對引擎，產出可審核的填寫計畫
  llm.py         Ollama / llama.cpp 後端，JSON Schema 受限解碼
  writer.py      保留格式寫回 docx
  storage.py     本地設定、個人資料、範本快取
  cli.py         命令列介面
tools/
  make_sample.py 產生一份仿台灣公司格式的測試履歷表
```

### 擴充欄位

在 `schema.py` 的 `FIELDS` 加一筆即可，比對、白名單、LLM prompt 會自動跟上：

```python
FieldSpec("basic.nationality", "國籍",
          ["國籍", "Nationality", "國別"]),
```

---

## 7. 已知限制

* 掃描件、圖片格式的履歷表不支援（需要 OCR 或視覺模型，見 Roadmap）
* 極度不規則的版面（用空白字元排版、用文字方塊排版）辨識率會下降
* 表格內的巢狀表格目前只解析第一層
* 「自傳」這類長文只會原封不動填入，不會替你改寫（刻意的：履歷造假風險）

## 8. Roadmap

* [ ] 圖形介面（Tauri 或 PySide6），給非工程師使用
* [ ] 掃描件支援：`qwen3.5:4b` 原生多模態，可直接吃頁面截圖做版面理解
* [ ] xlsx / pdf 表單支援（`openpyxl` / `pypdf`）
* [ ] 內建範本社群包：常見大公司表格的對映檔可分享匯入

## 9. 授權

建議採用 **AGPL-3.0**（避免被包成閉源 SaaS）或 **MIT**（求最大採用率）。
注意所選模型本身的授權與本專案的授權是兩回事，商用前請各自確認模型卡上的條款。
