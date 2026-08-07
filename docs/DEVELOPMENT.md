# 開發文件

使用說明在 [README](../README.md)；這裡是架構、設計決策與開發流程。

---

## 1. 為什麼需要 AI

如果每家公司的履歷表格式都一樣，這個專案根本不需要 AI，寫死座標就好。

問題是每一間都不一樣：A 公司寫「姓　名」，B 公司寫「應徵者姓名」，C 公司寫「Name」，
有的用表格、有的用底線填空、有的用 Word 內容控制項。**欄位長什麼樣子無法窮舉。**

所以分工是這樣切的（確定性優先、模型墊後）：

| 工作 | 誰做 | 為什麼 |
|---|---|---|
| 把 docx 攤成文字、記住每個位置的座標 | 程式 | 這是文件的客觀事實，不需要判斷 |
| 標籤定位：「值填在標籤右邊或下面」 | 程式 | 幾何規則，確定性比模型可靠 |
| 常見標籤 → 欄位（姓名、行動電話…） | 程式（對照表） | 精確比對沒有第二種答案，零模型呼叫 |
| **對照表外的怪標籤是什麼意思** | **模型** | 語意問題，只有這步需要理解力 |
| 取值、寫回 docx、保留格式 | 程式 | 值必須精確，交給模型會被竄改 |

模型只做判讀，不生成內容：填寫時它從固定的欄位清單裡挑一個，匯入時它只能從原文
逐字選取片段。它碰不到你的資料，也編不出你的學經歷。
這也讓模型需求變得很低——不是「生成一份履歷」，而是「判讀」，單卡可跑的本地模型就夠。

---

## 2. 系統架構

```
┌──────────────────────────────────────────────────────────────┐
│  ResumeAutoFill.exe（launcher/，.NET 單檔自足）                │
│    1. 以內嵌 Python 背景啟動後端                               │
│    2. 等 /api/health ready → 自動開瀏覽器                     │
│    3. 常駐系統匣；結束時連 llama-server 一起收                 │
└────────┬──────────────────┬───────────────────────┬──────────┘
         ▼                  ▼                       ▼
┌───────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│  前端          │  │  後端                 │  │  推論引擎         │
│  React         │◄►│  FastAPI             │─►│  llama-server    │
│  + Tailwind    │  │   ├─ docx 結構解析    │  │  (llama.cpp)     │
│                │  │   ├─ 標籤錨定引擎     │  │                  │
│ · 我的資料     │  │   ├─ 保留格式寫回     │  │  Qwen3.5-9B      │
│ · 填寫履歷     │  │   ├─ 模型下載/切換    │  │  Q4_K_M ~5.3 GB  │
│ · 匯入履歷     │  │   └─ SQLite          │  │  ＋mmproj 視覺檔  │
│ · 日誌         │  │                      │  │  GBNF 受限解碼    │
└───────────────┘  └──────────────────────┘  └──────────────────┘
   build 成靜態檔      localhost:8090             localhost:8085
   由 FastAPI 托管     （llama-server 由後端        （僅本機）
                        的模型選單啟動與切換）
```

**前端 React + Tailwind** — 個人資料是結構化的多層表單（學歷、經歷多筆），
填寫過程需要即時預覽和逐格修正，典型的狀態管理需求。build 成靜態檔由 FastAPI 托管，
使用者只看到一個 port。

**後端 FastAPI + SQLite** — docx 處理在 Python 生態最順（`python-docx`＋直接操作 XML）。
SQLite 免安裝免設定，整個資料庫一個檔案，符合單機工具定位。

**推論引擎 llama.cpp** — 相對 Ollama 的關鍵是**打包**：`llama-server.exe` 是獨立執行檔，
跟模型檔一起放進發佈資料夾即可，使用者不需要知道底下跑著什麼。
GBNF grammar 受限解碼是本專案的核心依賴（見第 5 節）。
llama-server 的啟動、切換、模型下載都由後端的模型選單管理。

**啟動器 .NET（launcher/）** — 一般人不會裝 Python、不會開終端機。
啟動器把這些藏起來：點兩下，瀏覽器就開好了。single-file self-contained 發佈，
使用者機器不需要裝 .NET runtime。單一實例（mutex）、port 被占自動退避、
結束時把後端連同 llama-server 整棵行程樹收掉。

---

## 3. 填寫的處理流程

```
   .docx
     │
     ▼
[1] 攤成文字＋座標（程式）
     表格攤平成網格（含合併儲存格、垂直合併映射回主格），
     空白儲存格／底線／勾選群／短提示格（郵遞區號□□□）都是可填位置
     │
     ▼
[2] 模型讀一次：這份文件已經有哪些值？
     分辨「印好的欄位名稱」與「使用者已填的資料」（後者可覆蓋）。
     模型沒啟動時跳過，僅填空白位置
     │
     ▼
[3] 標籤錨定（程式為主，模型墊後）
     a. 掃出表格上印的標籤，能用對照表（LABEL_MAP）確定對上欄位的
        → 直接錨定右鄰／下方的可填位置，零模型呼叫
     b. 使用者教過的標籤（learned_labels 全域字典）→ 同樣零模型呼叫
     c. 都沒有 → 帶著「同列列首」上下文，一次小呼叫問模型
        （「姓名｜緊急連絡人」和單獨的「姓名」是不同欄位）
     d. 三層都錨不住 → 留白待人工，不硬猜
     之後：確定性對齊（期間欄拆起訖）＋模型列指派（大學／研究所列
     對應第幾筆學歷）

     學習閉環：填寫頁的手動修正回饋成跨表格的「標籤→欄位」字典
     （kv 表 learned_labels）——A 公司教過的「服務單位＝公司名稱」，
     B 公司直接受益。安全閥：內建對照表永遠優先；squash 後 ≤2 字的
     泛用標籤（姓名、電話）不學全域；改「找不到對應」＝遺忘。
     │
     ▼
[4] 保留格式寫回（程式）
     只替換文字節點；勾選題把 □ 換成 ■；短提示格用附加不覆蓋
     │
     ▼
   完成的 .docx ＋ 稽核清單（填了什麼、略過什麼、來源與信心值）
```

同一份格式只需要判斷一次：決策連同格式指紋存入範本快取，
第二次上傳直接沿用，0 次模型呼叫。實測 109 格的真實表單：
首次約 85 秒、快取後數秒。

匯入方向相反（已填履歷 → 我的資料）：模型連同**頁面截圖**（LibreOffice 轉 PDF
再轉圖，需模型掛 mmproj）一起判讀，排版資訊補回攤平文字丟失的部分；
防幻覺靠驗證——回傳的每個值必須在原文逐字找得到，找不到就丟棄。

---

## 4. 兩個方向的衝突處理（刻意不同）

- **填寫**以「我的資料」為準：文件原本的值會被覆蓋，表格用刪除線＋「將被覆蓋」事先告知。
- **匯入**反過來：已有值的欄位**預設不勾選**，避免上傳一份舊履歷把維護好的資料蓋掉。

降級行為：llama-server 沒起來時 API 照常回應（`llm_available: false`），
規則層錨定與範本快取照常運作，模型才能判斷的部分標成待人工。模型沒開不該讓工具停擺。

---

## 5. AI 模型：Qwen3.5-9B

**已定案：`Qwen3.5-9B` + `Q4_K_M` 量化，跑在 llama.cpp 上。**

本專案對模型的需求：

| 需要 | 不需要 |
|---|---|
| 繁體中文語意理解（「戶籍地址」vs「通訊地址」） | 長文生成 |
| 穩定遵守 JSON Schema（只能從固定選項挑） | 數學、程式能力 |
| 反應快 | 深度推理 / thinking 模式 |
| 16K 上下文 | 百萬 token 上下文 |

### 選它的理由

1. **中文最強** — 同尺寸級距內 Qwen 在中文語境沒有對手，而本專案面對的正是中文標籤。
2. **授權乾淨** — Apache 2.0，可商用、可散布，模型要跟著 exe 一起發佈這點很重要。
3. **記憶體剛好** — 含 16K 上下文約 7.4 GB，8 GB VRAM 塞得下。
4. **原生多模態** — 掛 mmproj 後直接吃頁面截圖做版面理解，匯入的視覺模式靠這個。
5. **無痛升級** — 從模型選單換 27B／35B-A3B（MoE），程式一行不用改。

更小的模型（2B/4B）實測會把區塊標題套到區塊內每一格、分不清自己在看哪一格，
填錯格的代價比省資源大，故不提供。

### 其他評估過的選項

| 模型 | 出處 | 參數 | 約需 VRAM | 授權 | 適用性 |
|---|---|---|---|---|---|
| **Qwen3.5-9B** | 阿里巴巴 🇨🇳 | 9B | ~7 GB | Apache 2.0 | ⭐ **已採用** |
| Hunyuan dense 7B / 4B | 騰訊 🇨🇳 | 7B / 4B | ~4.5 / ~2.5 GB | 騰訊自訂條款 | 中文可用，原生 256K 上下文 |
| MiniCPM 4.1 / 5 系列 | OpenBMB 🇨🇳 | 1B～8B | ~1～5 GB | Apache 2.0 | 端側特化，低階硬體速度優勢 |
| ERNIE 4.5 小型版 | 百度 🇨🇳 | 0.3B 起 | <1 GB | Apache 2.0 | 極輕量，能力較弱 |
| Gemma 4 E4B | Google 🇺🇸 | 有效 4B | ~3 GB | Gemma 條款 | 非中國模型替代，中文略遜 |
| Breeze 2 8B | 聯發創新基地 🇹🇼 | 8B | ~5 GB | 受 Llama 3.2 約束 | 繁中在地化最強，授權較綁手 |

不適用：GLM-5.2（744B MoE 跑不動）、DeepSeek（無合適小型 dense）、
MR Breeze 3（語音模型，非文字分類）。

### 關鍵：受限解碼（constrained decoding）

程式**不是**用 prompt 拜託模型「請回傳 JSON」，而是把 JSON Schema 交給 llama.cpp：

```jsonc
{
  "messages": [ "..." ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "schema": {
        "type": "object",
        "properties": {
          "field_key": { "enum": ["basic.name_zh", "contact.mobile", "..."] },
          "confidence": { "type": "number" }
        },
        "required": ["field_key", "confidence"]
      }
    }
  }
}
```

llama.cpp 把 schema 轉成 **GBNF grammar**，生成的每一步只允許符合文法的 token。
「輸出不是合法 JSON」「發明不存在的欄位」在機制上不可能發生——不是靠模型聽話，
是取樣層面生不出來。`field_key` 鎖成 enum 把「開放式生成」壓縮成「封閉式分類」，
**這才是本地小模型足以勝任的真正原因。**

注意：文法越大取樣越慢，這是欄位對映改成分批／標籤錨定的效能原因之一。

---

## 6. 開發環境

```powershell
.\dev.ps1                # 後端＋前端各開一個視窗（日常開發）
.\dev.ps1 backend        # 只跑後端，port 8090，目前視窗，Ctrl+C 結束
.\dev.ps1 frontend       # 只跑前端，port 5173（proxy 已設）
.\dev.ps1 llm            # 只跑推論引擎，port 8085，等同 scripts\start-llama-server.ps1
.\dev.ps1 stop           # 停掉這三個埠上的服務
```

`dev.ps1` 只是把下面三行包起來，直接下也一樣：

```powershell
python -m uvicorn backend.main:app --port 8090 --reload
cd frontend; npm install; npm run dev
.\scripts\start-llama-server.ps1
```

- `stop` 用 `taskkill /T` 連子行程一起殺：uvicorn 的 `--reload` 是「監看父行程＋服務子行程」兩個，
  只殺父的話子行程會變孤兒繼續佔著 8090，症狀是重啟時說埠被佔用但看不到人。
- `dev.ps1` 必須存成 **UTF-8 with BOM**；PowerShell 5.1 沒 BOM 會用 ANSI 讀，中文變亂碼直接解析失敗。

- `http://localhost:8090/docs` 是 API 互動文件。
- 前端 build 後（`npm run build`）由後端托管，`http://localhost:8090/` 即完整介面。
- `--reload` 是整個行程重啟；資料都在 SQLite 與檔案裡所以無影響，正式啟動不要帶。
- llama-server 的 `--ctx-size 16384` 不是隨便訂的：整份文件＋輸出，8192 會在生成中被截斷。
  `--temp 0` 讓判斷可重現；`--reasoning off` 關掉 Qwen 的 thinking 模式。

### 測試工具

```
tools/make_sample.py         產生標準測試表格
tools/make_tricky_sample.py  標籤刻意寫怪，測模型層
tools/snapshot.py            docx 整份截成頁面 PNG
tools/vision_lines.py        視覺逐行辨識實驗
tools/label_value.py         標籤值抽取實驗（逐行整理／截圖直出）
tests/prompt_test.py         最小 prompt 實驗場：一張截圖＋prompt 丟模型
```

### Log

`data/logs/app.log`（程式根目錄下，UTF-8，5 MB × 5 輪替）。
個人資料整包在根目錄的 `data/`（可攜式，已被 .gitignore 排除），
用 `RESUME_AUTOFILL_HOME` 可另指位置。
同一請求的所有記錄共用 request_id，回應標頭 `X-Request-Id` 帶著它。
**log 永遠不寫入 profile 的值**——log 可能被使用者附在問題回報裡送出。
輪詢類請求（模型狀態、分析進度）記在 DEBUG，預設不出現。

---

## 7. 打包

```powershell
.\scripts\build-package.ps1        # 組出 dist\Resume_AutoFill\
.\scripts\build-package.ps1 -Zip   # 另壓成 zip
.\scripts\build-package.ps1 -Quick # 跳過前端重建與 bin 複製（迭代用）
```

**不用 PyInstaller**：內嵌官方 embeddable Python＋site-packages＋原始碼，
怎麼開發就怎麼跑——沒有隱藏相依收集的脆弱性，也沒有防毒誤判問題。
embeddable 版本必須與開發用 Python 同 minor 版（二進位套件才相容）。

產物結構：

```
Resume_AutoFill\
  ResumeAutoFill.exe    啟動器（launcher/，dotnet publish 單檔自足）
  app\                  backend 原始碼＋frontend/dist＋runtime（內嵌 Python）
  bin\                  llama-server.exe＋CUDA DLL（約 670 MB）
  data\                 個人資料（SQLite、上傳暫存、日誌），首次啟動自動建立
  models\ input\ output\
  README.txt
```

模型不隨附（5.3 GB），由介面首次下載。啟動器以 `RESUME_AUTOFILL_ROOT`
告訴後端根目錄在哪，models/、bin/ 都從它推導。
疑難排解：設 `RESUME_AUTOFILL_DEBUG=1` 會在根目錄寫 launcher-debug.log；
`RESUME_AUTOFILL_NO_BROWSER=1` 不自動開瀏覽器（自動化測試用）。

---

## 8. 待討論

* SQLite 資料表設計（個人資料的多筆結構、格式對映快取的儲存方式）
* 前後端 API 介面定義
* CPU 推論的打包：CUDA 版 llama-server 在無 NVIDIA 驅動的機器上起不來，
  需同捆 CPU 版二進位並在啟動時偵測選用
