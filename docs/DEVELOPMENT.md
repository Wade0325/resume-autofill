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
│ · 匯入履歷     │  │   ├─ 網頁填寫         │  │  ＋mmproj 視覺檔  │
│ · 網頁填寫     │  │   └─ SQLite          │  │  GBNF 受限解碼    │
│ · 日誌         │  │                      │  │                  │
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

**網頁填寫 Playwright＋系統的 Edge** — 把我的資料補進求職平台（目前是 Cake）。
Playwright 的 Python 套件自帶 Node.js，隨發佈包出去；瀏覽器用 Windows 內建的 Edge，
不下載 Playwright 自己的 Chromium。見第 9 節。

---

## 3. 填寫的處理流程

兩條路（上傳畫面的「判斷方式」），共用範本快取與保留格式的寫回（`runs.py`：只換受影響的文字節點）：

- **看版面**（`filler.py`，模型看得到圖時的預設）：程式自己畫版面示意圖、列出所有可寫的位置，
  模型只回答「這個位置放哪一項資料」（答案被 JSON Schema 限定在既有的項目代碼），
  值怎麼寫——日期拆格、西元換民國、打勾——由程式決定。步驟與每一步的理由見 `filler.py` 開頭的說明。
- **讀文字**（`planner.py`＋`writer.py`）：規則錨定為主、純文字模型墊後，流程如下。
  模型看不到圖（沒有視覺檔）時自動改走這條。

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
     空白範本（多數情況）先用規則掃過，沒有「值長相」的內容就整步跳過
     │
     ▼
[3] 標籤錨定（程式為主，模型墊後）
     a. 掃出表格上印的標籤，能用對照表（LABEL_MAP）確定對上欄位的
        → 直接錨定右鄰／下方的可填位置，零模型呼叫
     b. 對照表沒有 → 帶著「同列列首」上下文，一次小呼叫問模型
        （「姓名｜緊急連絡人」和單獨的「姓名」是不同欄位）
     c. 兩層都錨不住 → 留白待人工，不硬猜
     之後：確定性對齊（期間欄拆起訖）＋模型列指派（大學／研究所列
     對應第幾筆學歷）

     填寫頁可以逐格改對映；改完的結果連同格式指紋一起進範本快取，
     同一份表格下次直接沿用。
     │
     ▼
[4] 保留格式寫回（程式）
     只替換文字節點；勾選題把 □ 換成 ■；短提示格用附加不覆蓋
     │
     ▼
   完成的 .docx ＋ 稽核清單（填了什麼、略過什麼、決策來源）
```

學過的格式存在 `template`，鍵是「結構指紋＋表格上印的字」（`service._template_key`）。
只看結構的話，同一套版型的不同公司（欄名不一樣）會共用同一份對映、填出來全錯；
加上欄名就分得開。舊資料只有結構指紋，第一次用到時搬到新鍵底下——不搬的話等於沒修好，
舊鍵沒有欄名資訊，另一家公司照樣撈得到它。填寫頁可以列出、忘掉，也可以「重新判讀」
（`use_cache=False`）重跑一次模型。

同一份格式只需要判斷一次：決策連同格式指紋存入範本快取，
第二次上傳直接沿用，0 次模型呼叫。實測 109 格的真實表單：
首次約 85 秒、快取後數秒。

左右對照預覽由前端 `docx-preview` 直接渲染 `.docx`：左邊原稿、右邊套用後
（黃底由 `writer` 的 highlight 寫進文件本身）。兩邊同一份文件、同一套渲染，
版面天然對齊，也不需要伺服器端轉檔。

匯入方向相反（已填履歷 → 我的資料）。**PDF 才附頁面截圖**：PDF 的文字順序是
繪製順序，排版資訊得靠圖補；`.docx` 攤平後本身就帶著表格結構，附截圖反而讓
模型改去讀圖。兩份文件實測：純文字 27 對 3 錯，視覺 28 對 9 錯——多出來的錯
是把欄位標題當成值（「畢業年月」→ 畢業狀態）、把姓名當成職稱那一類。

PDF 取字一定要 NFKC——104 的字型把中文對映到康熙部首區，不正規化的話
逐字驗證會把每個值都判成幻覺。

防幻覺靠驗證——回傳的每個值必須在原文逐字找得到，找不到就丟棄。注意這只保證
「這串字出現在文件某處」，不保證出現在對的地方，所以上面那類錯誤擋不住。

長文（工作內容、自傳）模型幾乎一定會改寫：換行、把「，」寫成「、」、省略幾個字，
整段就被丟掉。現在開頭 16 個字（只比文字與數字，不看標點與空白）對得上就認，
而且值改用**原文那一段**——留下的還是文件裡的字，不是模型的改寫版。

整份掃描成圖片的 PDF 沒有文字層，逐字驗證會把每個值都丟掉（以前就是默默回 0 筆）。
現在認得出來（全文不到 60 個字）：有視覺能力就附頁面截圖、跳過逐字驗證，
並在匯入頁標明「請自己核對」；沒有視覺能力就直接說清楚，不要假裝讀完了。

---

## 4. 兩個方向的衝突處理（刻意不同）

- **填寫**以「我的資料」為準：文件原本的值會被覆蓋，表格用刪除線＋「將被覆蓋」事先告知。
- **這次應徵**（應徵職務、工作地點…）跟著那一份工作走，存在 `job.apply`，不進「我的資料」：
  每間公司都不一樣，存成全域值只會填錯。看版面把這些位置標上 `Slot.job_field`，
  值一律由面板決定（模型挑的、舊快取記的都不算）；位置另外編號（`#0.j1`）、也不算進格式指紋，
  學過的格式才不會因為多了這個功能全部要重學。讀文字靠 `LABEL_MAP` 認同一批欄名。
- **自己打的值**（對映清單的「將填入」欄）存在 `job.typed`，跟著那一份工作，比任何判斷都優先。
  寫入時照原樣寫：日期拆進年月日、同一項攤到連續空格、一格一個字、單位前只收數字這些規則
  都是為了「從我的資料推出該寫什麼」，手打的不必再推一次（勾選框例外，打字＝勾那個選項）。
- **分析可取消、會排隊、進度看得到第幾批**：`filler.analyze` 收兩個 callback
  （`progress(說明)`、`stop() -> bool`），在批次之間呼叫——正在跑的那一次模型呼叫不會被打斷
  （中斷 HTTP 請求救不回半個回應，下一批再停就好）。取消旗標在 `service._cancelled`，
  排隊用一個號誌（`_Queued`），等的時候照樣看得到「前面還有幾份」，也還能取消。
- **算出來的欄位**（年齡、總年資、服役期間、就學／任職期間）標成 `derived`，存的值不算數：
  存著的年齡去年填今年就錯了，工作經歷改了總年資也該跟著變。一律在 `filler.fields_of` 算。
- **日期存檔時統一寫法**（`profiles.normalize_dates`）：手選的是「1996年04月15日」、匯入的常是
  「1996/4/15」，混在一起我的資料頁的下拉就認不得。「至今」與認不出來的原樣保留，不換算曆制。
- **少見的欄位表格提到才列給模型挑**（`filler.ASK_ONLY_IF_MENTIONED`）：語言、求職偏好、
  問答題多數表格沒有，清單越長模型越容易配錯。別人的資料（家人、諮詢人、緊急聯絡人）本來就是這樣。

  這件事的代價比想像中大。實測把候選清單**多加一項**（總年資，118 → 119 項），三份考題就從
  67/70/48 掉成 66/68/48——掉的是住家電話和兩格證照名稱，跟新欄位毫不相干。所以名單除了整個
  區段，也可以只擋一個欄位（`contact.postal_mailing`、`certificate[].issued`…）；新增欄位時
  順手加一條，沒問到那個欄位的表格看到的清單就跟以前一模一樣。攤平後的鍵是
  `certificate[0].issued`，名單寫 `certificate[].issued`，比對前先用 `_ROW_INDEX` 抹掉序號。

  一列一筆的表另外還有一條：單獨指名的欄位要**自己真的填了值**才列出來
  （`filler._worth_offering`）。履歷表印著「發照日期」，可是資料裡沒填，列出來模型就把那一欄
  認成發照日期，真正有資料的證照名稱反而沒地方去——實測就是這樣少填兩格。

  改完要驗的不是分數，是**兩邊送進模型的清單一不一樣**：逐格那一輪的 `usable_fields`、
  一列一筆那一輪的 enum（攔 `llm.ask` 把 schema 取出來），還有 `parse()` 認出來的位置
  （新增欄位等於新增欄位名稱，可能改變格子的標題判讀）。同一份資料、同三份考題，
  舊碼與新碼各跑一次對照。分數會因為模型本身的變異上下跳，這三份清單不會。

  要一次錄下**每一次**呼叫就攔 `llm.requests.post`：配對那一輪接著問（`llm.Chat`），
  不經過 `llm.ask`，只攔 `llm.ask` 會錄到零筆，看起來像「什麼都沒變」。把每一次的
  payload 做雜湊、兩邊比對，四輪送出去的東西是不是逐字相同一次就知道——假的回答要
  固定，不然後面幾批的提示會因為前面的回答不同而分岔。
- **批次（一次好幾份）沒有自己的狀態機**：每一份還是一個獨立的工作，走同一條分析流程、
  同一個排隊號誌（`service._Queued`），前端只是把代碼存成一組（`sessionStorage`
  的 `fill.batchIds`）拿來看進度、一起下載。另做一套批次狀態會多出「批次壞了但裡面的
  工作好好的」這種對不起來的狀態，而且點「檢視」就回到平常的單份畫面，什麼功能都不必重做。
  - 進度用 `GET /jobs/batch?ids=…`，**刻意不回計畫內容**：`get_job_state` 分析完會回
    整份計畫，拿它輪詢五份等於每兩秒搬五份計畫。
  - 批次那幾支路由要宣告在 `/{job_id}` **前面**，否則 `/jobs/batch.zip` 會先被當成 job_id。
  - 一份失敗不影響其他份，`batch_output` 逐份回報；zip 檔名前面加序號，
    兩家公司的表格常常同名，不編號會在 zip 裡互相蓋掉。
  - zip 的中文檔名走 RFC 5987（`filename*=UTF-8''…`），另外留一個 ASCII 備援。

- **輸出 PDF 走瀏覽器列印**（`components/docx.ts` 的 `printDocx`）：不碰 LibreOffice、Word
  或任何轉檔工具——產品要可攜、離線、解壓就能用，叫使用者裝一套辦公軟體等於毀掉這個定位。
  填好的文件本來就已經用 docx-preview 渲染得出來，列印時把它另外渲染一份到
  `#print-root`（放在畫面外、`visibility:hidden`，但不能用 `display:none`——那樣量不到頁面尺寸），
  再注入一段只在 `@media print` 生效的樣式：把 `#root` 整個藏起來、只留這一份。
  - **紙張尺寸照文件自己的**：量渲染出來的 `section` 換算成 mm 寫進 `@page`，求職表格不一定是 A4。
  - **`margin: 0`**：頁邊距已經畫在 section 裡，再加一層瀏覽器邊界會把內容往內擠、右下被裁掉。
  - **`zoom: 1`**：畫面上的預覽會縮到欄寬，列印要原尺寸，所以 `renderDocxInto` 收一個 `fit` 參數。
  - 印的是**不標黃底**那份（`preview.docx?which=filled&highlight=false`），跟下載的成品同一條
    寫入路徑、內容一樣——黃底是給人核對用的，印出來交出去不該帶著。
  - 收尾掛在 `afterprint`，另外補一個 60 秒的保險：Safari 不一定發這個事件。

- **大頭照不需要**：不存也不填。
- **匯入**反過來：已有值的欄位**預設不勾選**，避免上傳一份舊履歷把維護好的資料蓋掉。
  多筆資料（學歷、經歷…）先依名稱找「我的資料」裡的那一筆（`service._entry_targets`）：
  學校、公司名稱不計簡稱、全半形、臺／台與公司後綴；名稱一樣但學位程度或到職年不同算兩筆
  （同校的學士與碩士、離職又回鍋）；人名要整個一樣。對不上或沒有名稱就新增一筆——
  寧可多一筆讓使用者刪，也不把別家公司的薪資補進現有那一筆。

模型沒開時（llama-server 沒起來）：

- **學過的格式照常填**，兩條路都一樣——範本快取命中就不問模型，幾秒完成。上傳時先看這份格式
  是哪一條路學過的就走哪一條（`service._learned_engine`），不管目前選的是哪一條。
- **沒學過的格式不能填**：讀文字要先讓模型通篇讀過、列出這份表格要填哪些欄位
  （`reader.list_fields`），看版面整份都靠模型——沒有「規則先填一部分」的退路。
  分析會直接失敗、請使用者先啟動模型；上傳畫面在模型沒開時先提醒。
- 匯入履歷一律要模型（抽取資料就是模型的工作）。
- 沒開時「看不看得到圖」改看目前這顆模型有沒有視覺檔（`service.model_state`），
  免得每次打開程式、模型還在啟動的那一兩分鐘，預設的判斷方式先變成讀文字。

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
          "field_key": { "enum": ["basic.name_zh", "contact.mobile", "..."] }
        },
        "required": ["field_key"]
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
.\dev.ps1 frontend       # 只跑前端，port 5177（proxy 已設）
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
  **開發時不要用這個網址**：它給的是 `frontend/dist` 裡上次 build 的靜態檔，沒有熱更新，
  改了原始碼也不會變。開發一律用 Vite 的 5177。
- `--reload` 是整個行程重啟；資料都在 SQLite 與檔案裡所以無影響，正式啟動不要帶。
- llama-server 的 `--ctx-size 16384` 不是隨便訂的。需求由**拆不開的那一次呼叫**決定：
  勾選題那一輪必須附上整份示意圖（只附題目那一頁時真建築連跑兩輪都少一格）。
  用 llama-server 量過各成分——整張示意圖 1600（不滿一張的比較少）、個人資料每項約 16，
  配上三個夾子（示意圖最多 `MAX_PAGES=4` 張、勾選題一批 `BOX_BATCH=8` 題、位置一批
  `BATCH=16` 個），需求是**有天花板的**：表格再大都不會超過約 12800（很厚的履歷配 4 頁
  表格）。三份考題實際量到的峰值 7637，16384 有約兩倍餘裕。

  補漏那一輪不在這個推導裡：它一次問完，伺服器說塞不下才對半切開再問
  （`filler._recall_fitting`，#13）。不設固定的批次上限，是因為補漏是一道配對題，切小了
  模型看不到其他空格——實測配對整輪失敗、補漏要接手二三十格時，固定每 8 格一批比一次問
  多錯 6 格。

  所以**更大的 ctx 買不到「能處理更大的表格」**，只買到「配對那一串少重開幾次」。
  裝不下的機器用 `RESUME_AUTOFILL_LLM_CTX` 調低，但低於 9600 就是有些表格直接送不出去。
  模型起來之後 `model_manager._check_context()` 會讀 `/props` 的實際 `n_ctx` 來判斷——
  讀伺服器而不是看設定值，因為 llama.cpp 會往下夾，使用者也可能自己啟動 server。

  配對那一串什麼時候重開也看同一個 `n_ctx`（`llm.Chat.start_over_if_long`）：對話長度用
  伺服器回報的用量（`usage.prompt_tokens` 是整段提示，快取命中的前綴也算），接上這一批
  會超過就先重開。真的撞到了（HTTP 400 `exceed_context_size_error`，或回答寫到一半
  `finish_reason=length`）就整串重開，那一批在新的一串上重問一次。以前門檻寫死 11000、
  長度用估的，上下文調小時安全閥永遠不開，撞到之後又原樣退回——4 頁的長表格在 8200 下，
  配對 25 批裡連續失敗 20 批。

  `--temp 0` 讓判斷可重現；`--reasoning off` 關掉 Qwen 的 thinking 模式。
- `--n-gpu-layers 999`：整顆模型都放上 GPU。中間一度改成不指定、讓 llama.cpp 自己看
  剩多少 VRAM 決定，因為 log 會少一行 `n_gpu_layers already set by user to 999, abort`。
  **那個改動是錯的，已經改回來**：8 GB 顯卡上自動配置挑了 CPU／GPU 混合，第 0 層被分到
  CPU，連帶 `fused Gated Delta Net not supported, set to disabled`，每次模型呼叫從
  ~10 秒變 14～15 秒（三份考題 93／132／128 秒 vs 全上 GPU 的 71／99／90 秒），
  跑約 40 分鐘後整個 server 以 `got exception: bad allocation` ＋
  `GGML_ASSERT(batch.slot_batched || batch.size() == 0) failed` 崩潰，之後連不上。
  那行 abort 只是告知，不是問題。裝不下的機器用 `RESUME_AUTOFILL_GPU_LAYERS` 指定層數
  （設 `0` 就純 CPU 跑）。

  教訓：評估分數突然變差時，先確認模型服務還活著再懷疑自己的程式——那次「主線也掉分」
  其實是 server 已經死了。

- 這個標籤只能靠 `nvidia-smi --query-compute-apps` 有沒有列到那個 pid 來判斷，**不能**
  要求 `used_memory` 是數字：Windows 的 WDDM 驅動模式問不到，那一欄會是 `[N/A]`，
  以前整顆模型都在顯卡上卻顯示「CPU」。另外自己設 `RESUME_AUTOFILL_GPU_LAYERS=0` 的人
  一律顯示 CPU——視覺投影檔仍然會佔 1.3 GB 左右顯卡（llama.cpp 預設把它放上去），
  但文字推論在 CPU，標籤要回答的是「為什麼這麼慢」。
- 開程式時自動把上次用的模型載回來（`RESUME_AUTOFILL_AUTOSTART=0` 關掉，開發時不想等就設它）。
  推論埠上已經有服務在聽就完全不動作——那可能是別的工作階段或使用者自己開的，不該去砍它。
- 跑在 GPU 還是 CPU：問 `nvidia-smi` 自己生的那個 pid 有沒有被列進「正在用 GPU 的行程」。不去解析 llama-server 的 log
  ——各版本寫法不一樣，這台機器上的 log 根本沒印 CUDA 初始化那幾行。
- 模型下載會續傳（`.part` ＋ `Range`，斷了不刪），完成後拿來源的 ETag 比對
  （Hugging Face 的 LFS ETag 就是檔案的 sha256）；壞檔直接刪掉重抓，不會一直續傳到同一個壞結果。
  開始前先檢查磁碟空間（模型大小＋1 GB 餘裕）。
- 推論引擎有金鑰：後端啟動 llama-server 時用環境變數 `LLAMA_API_KEY` 給 `data/llm.key`
  裡的金鑰（第一次自動產生），`llm.py` 的呼叫帶同一把。不用 `--api-key-file`——它開不了
  中文路徑的檔案。`dev.ps1 llm` 手動起的沒有金鑰，照常可用。
- 後端只收 Host 是 `127.0.0.1`／`localhost` 的請求，會改東西的請求 Origin 要跟 Host 同源
  （擋別的網頁借瀏覽器打本機服務）。所以 `vite.config.ts` 的 proxy 要維持 `changeOrigin: false`，
  否則開發時的存檔、上傳全部 403。正式版的頁面另帶 CSP，只准跑自己的腳本。

### 測試工具

```
tools/make_sample.py            產生標準測試表格
tools/make_tricky_sample.py     標籤刻意寫怪，測模型層
tools/check_docx_integrity.py   比對原稿與填好的檔案：勾選框、底線、字型、刪除線有沒有被弄掉
```

研究迴圈的評分只比對每一格的文字，寫回時弄壞格式它看不見。改了寫入端（`runs.py`、
`writer.py`、`filler.py` 的寫回）之後，除了回放評分，也要拿輸出跑一次 `check_docx_integrity.py`。

### 看模型實際收到什麼（Langfuse）

所有模型呼叫都經過 `llm._call`：`llm.ask`（問一次就結束）和 `llm.Chat.ask`（配對那一輪
接著問）最後都走到那裡，選擇性的 Langfuse 追蹤也包在那一層。
金鑰填在根目錄的 `.env`（不入版控），填好重啟後端就開始送，留空就完全不啟用：

```powershell
pip install -e ".[dev]"
# 編輯 .env 填入 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
.\dev.ps1 backend
```

自架的 Langfuse 是 v3 架構，SDK 要跟著留在 v3（`langfuse>=3,<4`）——
v4 換了資料模型與攝取端點，對 v3 伺服器送不進去。

每次呼叫是一筆 generation：input 是送出去的整串訊息（配對那一輪接著問時，前幾輪
都在裡面）、output 是模型回應、metadata 帶著那次用的 JSON Schema 與圖片張數
（`images` 整串共幾張、`images_new` 這一輪新附幾張）、usage 帶 token 數。
頁面截圖會換成「<截圖>」——base64 幾百 KB 塞進 trace 只會讓畫面爆掉。

**只用本機自架的 Langfuse。**提示詞裡是完整的履歷內容（身分證字號、生日、
地址），送到雲端等於把個資上傳第三方。SDK 沒指定位址時的預設值就是雲端，
所以 `llm._tracer()` 會擋掉：沒設 `LANGFUSE_BASE_URL` 或指向雲端就不啟用。

Langfuse 內建 MCP 端點（`/api/public/mcp`），已用 local scope 註冊給這個專案：

```powershell
$t = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$pk`:$sk"))
claude mcp add --transport http --scope local langfuse `
    http://localhost:3000/api/public/mcp --header "Authorization: Basic $t"
```

金鑰存在 `~/.claude.json` 的專案區段，不進版控。注意這個 MCP **沒有讀取
trace／observation 的工具**（54 個工具都是 prompt、dataset、score、evaluator
那些），要撈 trace 內容還是走 REST 的 `/api/public/traces`。

### Log

`data/logs/app.log`（程式根目錄下，UTF-8，5 MB × 5 輪替）。
個人資料整包在根目錄的 `data/`（可攜式，已被 .gitignore 排除），
用 `RESUME_AUTOFILL_HOME` 可另指位置。
同一請求的所有記錄共用 request_id，回應標頭 `X-Request-Id` 帶著它。
**log 永遠不寫入 profile 的值**——log 可能被使用者附在問題回報裡送出。
上傳檔名常帶著本人姓名：只有日誌頁（action 通道）記檔名，讓使用者認得出是哪一份
（使用者要求保留）；開發者 log 一律記工作代碼。
輪詢類請求（模型狀態、分析進度）記在 DEBUG，預設不出現。

### 資料庫

`data/app.db`，用標準庫 sqlite3。我的資料與設定整份存成 JSON（`kv` 表）——從不按欄位查詢，
正規化只會多出 join 與遷移成本。其他表：格式對映快取 `template`、填寫與匯入的工作
`job`／`import_job`、我的資料的舊版本 `profile_version`（每次存檔、匯入、還原前留一份，只留最近 30 份）。

- 新表直接寫進 `db.SCHEMA`（`CREATE TABLE IF NOT EXISTS`）。
- 改舊表（加欄位、搬資料）在 `db.MIGRATIONS` 尾巴加一步；做到第幾步記在 `PRAGMA user_version`，
  已經發出去的步驟不要改。加欄位用 `_add_column`（已經有就跳過），不要 try/except 吞錯誤——
  以前這樣寫，連「資料庫被鎖住」都一起吞掉。
- 寫「我的資料」一律經過 `profiles.save()`，才會留版本；結構由 `profiles.problem()` 把關
  （認得的區塊形狀要對，不認得的照留）。

---

## 7. 測試

```bash
pip install -e ".[test,lint]"
ruff check backend tools tests
pytest                      # 不需要瀏覽器的那些，約 50 秒
npm --prefix frontend run build && pytest -m browser    # 加上瀏覽器那一組
pytest -m ""                # 全部
```

**ruff 的版本是釘死的**（`lint` extra 寫 `ruff==0.9.6`）。ruff 每個小版本都可能多出新規則，
浮動版本會讓 CI 無預警變紅，而且本機跟 CI 跑的不是同一套規則就失去意義。
規則集是 `E/W/F/I`、行長 100——刻意不開 `UP` 與 `B`：`UP006`／`UP007` 會要求把
`Dict`／`Optional` 全面換成 `dict`／`|`，那是 600 多處的風格遷移，要做也該單獨做。

**一個模型都不用跑。** 要模型判斷的地方一律換成假的（`monkeypatch`），要測「模型沒開」
的行為時就把 `RESUME_AUTOFILL_LLM_HOST` 指到一個沒人在聽的埠讓它真的連不上。
所以這套測試在沒有顯卡、沒有模型檔的機器上（包括 CI）照樣跑得完。

幾件動手前要知道的事：

- **`RESUME_AUTOFILL_HOME` 必須在 import backend 之前設好**。`backend.config` 是在
  import 當下讀環境變數的，晚一步設就會寫到真正的 `data/`，把使用者的履歷蓋掉。
  所以 `tests/conftest.py` 把它寫在模組最上面，不是放進 fixture。
- **考題不能用研究迴圈那幾份**：`claude_code_in_agent/` 是本人的真實履歷，沒進版控。
  測試用的表格一律由 `tools/make_sample.py` 當場產生，資料全是虛構的。
- **瀏覽器那一組另外開一個後端行程**（不是 TestClient）：畫面要真的連得上 HTTP，
  而且 `frontend/dist` 是後端在 serve 的。它有自己的暫存資料夾。
  塞測試資料時再開一個子行程（`tests/browser/_seed.py`），因為那個後端的資料夾跟
  主測試不同，而 config 的路徑在 import 時就定死了。
- **`page.wait_for_function` 在這個專案不能用**：它是在頁面裡 `eval`，會被本站的
  CSP（`script-src 'self'`）擋掉。要等條件就自己輪詢。
- **`page.evaluate` 的多行字串，最後一個敘述的值不能是函式**（例如結尾是
  `window.print = () => {}`）——回傳值無法序列化，後續行為就不對了。結尾補一個
  `window.__ready = true` 之類的就好。
- `visibility:hidden` 的元素 `innerText` 會回空字串，要驗內容得用 `textContent`。
- **網頁填寫的整條流程對著本機的假 Cake 跑**（`tests/browser/fake_cake.html`，由測試裡的小型
  HTTP 伺服器提供，登入用 cookie 模擬）。它自己開一個無頭瀏覽器：有 `chromium-1228` 就用它，
  沒有就用 `playwright install chromium` 裝的那一份。兩個測試檔不能同名（pytest 預設的
  匯入方式認檔名），所以瀏覽器那一份叫 `test_webform_flow.py`。

### CI

`.github/workflows/ci.yml`，push 到 main 與所有 PR 都會跑，三個 job（平行，總時間看最慢的那個）：

| job | runner | 做什麼 |
|---|---|---|
| python | windows-latest | `ruff check` ＋ `pytest`（198 項） |
| frontend | ubuntu-latest | `npm ci` ＋ `npm run build`（`tsc` 在裡面，等於型別檢查） |
| browser | windows-latest | `npm run build` ＋ `playwright install chromium` ＋ `pytest -m browser`（26 項） |

**為什麼測試跑 Windows 而不是便宜的 Linux**：`backend/model_manager.py` 有幾處沒有防護的
Windows 專屬呼叫（`subprocess.CREATE_NO_WINDOW`、`powershell`），產品本身也只出 Windows。
這個倉庫是公開的，Actions 在所有 runner 上都免費，沒有理由為了省錢冒可攜性的險。
前端沒有這個問題，所以跑 ubuntu，比較快。

**瀏覽器那個 job 有一個陷阱要記得**：`tests/browser/conftest.py` 在缺 playwright、
缺 chromium 或缺 `frontend/dist` 的時候是**跳過**而不是失敗——那是刻意的，沒裝這些的人
才跑得完其他測試。但放到 CI 上，「跳過」會讓整個 job 顯示綠色卻一項都沒測到。
所以那一步跑完會檢查摘要裡有沒有 `skipped`，有就當成失敗。改那一步的時候別把這段拿掉。

檢查刻意只攔 `skipped` 這個字，不自己去判斷「chromium 在不在」：那會變成跟 conftest
各寫一套解析邏輯，兩邊遲早不一致（已經踩過一次——`launch()` 預設要的瀏覽器版本
跟 conftest 實際 fallback 用的那份不同）。

它比另外兩個 job 慢一點（實測 1m54s，其中下載 chromium 約 195 MB 佔了大半，測試本身
只跑 31 秒），但三個 job 是平行的，所以總時間沒有變成相加——加它之前約 1.5 分鐘，
加了之後約 2 分鐘。真嫌慢的話先快取 `~\AppData\Local\ms-playwright`，那比改成每日排程
划算；每日排程的代價是壞掉的畫面最久要一天才會被發現。

打包與 Release 也還沒進 CI：`bin/`（llama.cpp ＋ CUDA DLL，約 667 MB）沒進版控，
CI 拿不到它就產不出完整的包。要做的話得先決定「從上游下載哪個版本」或「發精簡包」，
那是發佈策略的決定，不是 CI 設定的問題。

## 8. 打包

```powershell
.\scripts\build-package.ps1        # 組出 dist\Resume_AutoFill\
.\scripts\build-package.ps1 -Zip   # 另壓成 dist\Resume_AutoFill-v<版本>.zip ＋ SHA256SUMS.txt
.\scripts\build-package.ps1 -Quick # 跳過前端重建與 bin 複製（迭代用）
.\scripts\build-package.ps1 -Bin D:\Resume_AutoFill\bin   # 在 worktree 裡打包時指向主目錄的 bin
```

**不用 PyInstaller**：內嵌官方 embeddable Python＋site-packages＋原始碼，
怎麼開發就怎麼跑——沒有隱藏相依收集的脆弱性，也沒有防毒誤判問題。
embeddable 版本必須與開發用 Python 同 minor 版（二進位套件才相容），腳本會先檢查。

**發出去的要跟測過的一樣**：
- Python 套件照 `scripts/package-requirements.txt` 裝（`--no-deps --only-binary=:all:`）。
  那份是執行期相依連同所有間接相依，版本取自跑過測試與研究迴圈的 `.venv`；
  `tests/test_package.py` 檢查它涵蓋 pyproject 的每一項、而且符合版本下限
- 前端用 `npm ci`，照 lock 檔裝
- 內嵌 Python 的 zip 核對 SHA256 才用（快取裡的也一樣）
- `bin\` 只帶 llama-server 要的檔案（白名單寫在腳本裡），缺一個就停；`bin\` 裡其他的
  llama.cpp 工具與舊的空資料夾不帶。用 `llama-server --list-devices` 驗過白名單：
  DLL 都載得起來、認得到顯示卡

原生程式（npm、pip、dotnet、tar）一律經過 `Invoke-Native`，只看結束碼。PowerShell 5.1
在輸出被重導時會把它們寫到 stderr 的警告當成錯誤、配上 `Stop` 就中斷；另外設
`PYTHONUTF8=1`，不然舊版 pip 用 cp950 讀需求檔，碰到中文註解就炸（Microsoft Store 版
Python 附的 pip 23.3.1 就是這樣）。

產物結構：

```
Resume_AutoFill\
  ResumeAutoFill.exe          啟動器（launcher/，dotnet publish 單檔自足，帶版本與圖示）
  app\                        backend 原始碼＋frontend/dist＋runtime（內嵌 Python）
  bin\                        llama-server.exe 與它要的 DLL（含 CUDA 13 runtime，約 660 MB）
  data\                       個人資料（SQLite、上傳暫存、日誌），一開始是空的
  models\                     模型，由介面首次下載
  README.txt  LICENSE.txt  THIRD-PARTY-NOTICES.txt
```

`THIRD-PARTY-NOTICES.txt` 由 `tools/third_party_notices.py` 產生：Python 套件讀打包進去的
site-packages，前端只列 source map 引用到的套件（package.json 裡大半是 Vite 外掛、Tailwind
編譯器這類建置工具，不在成品裡），llama.cpp 與 .NET runtime 的授權全文存在 `scripts/licenses/`。

### 發佈新版本

1. 版本號改四處：`backend/__init__.py`（以它為準）、`pyproject.toml`、`frontend/package.json`
   （連同 `package-lock.json` 頂端兩處）、啟動器 csproj 的 `<Version>`——`tests/test_version.py`
   會擋住沒跟上的。`CHANGELOG.md` 加一節
2. 走 PR、CI 全綠、合併；從合併後的 main 打包（exe 的產品版本會帶上 commit 編號）
3. 冒煙測試：zip 解壓到**有中文與空格的路徑**、雙擊啟動器、畫面左上角版本正確、
   模型啟動、實際填一份 `tools/make_sample.py` 產生的虛構表格並下載；網頁填寫按「開啟瀏覽器」
   跳得出 Edge、停在 Cake 登入頁（內嵌 Python 帶的 Playwright 與 Node 都要動得起來）
4. `git tag v<版本>`、推 tag，`gh release create` 上傳 zip 與 `SHA256SUMS.txt`
5. 從 GitHub 把 zip 下載回來，比對 SHA256、解壓、再啟動一次

模型不隨附（5.3 GB），由介面首次下載。啟動器以 `RESUME_AUTOFILL_ROOT`
告訴後端根目錄在哪，models/、bin/ 都從它推導。
疑難排解：設 `RESUME_AUTOFILL_DEBUG=1` 會在根目錄寫 launcher-debug.log；
`RESUME_AUTOFILL_NO_BROWSER=1` 不自動開瀏覽器（自動化測試用）。

---

## 9. 網頁填寫（Cake）

`backend/webform/`＋前端「網頁填寫」分頁：把「我的資料」裡 Cake 上還沒有的工作經驗、學歷、
證照補進 Cake 個人檔案。

**這是跟本地 docx 填寫並列的另一個功能，不是它的延伸。** 入口、模組、文件都分開，也不用模型：
Cake 的表單是固定的，每一區送哪幾項直接寫在程式裡。共用的只有比對名稱（`core/names.py`，
匯入履歷判斷「這一筆是我的資料裡的哪一筆」也是用它）。

流程（`session.py`）：開瀏覽器 → 使用者自己登入 → 讀出 Cake 上還沒有的幾筆 → 使用者在清單上
確認、補齊缺的欄位 → 一筆一筆存 → 存完重新讀一次。

| 檔案 | 做什麼 |
|---|---|
| `browser.py` | 專用瀏覽器：系統的 Edge（沒有才用 Chrome）、專用設定檔 `data/browser/`、住在自己的執行緒 |
| `session.py` | 流程與狀態（closed／login／reading／ready／running），前端每秒輪詢 `snapshot()` |
| `cake.py` | Cake 專屬：登入判斷、哪些已經有了、排清單、新增一筆 |
| `dom.py` | 找欄位、填值、勾選、送出、取消——docx 那邊 `cells()`／`apply_fills()` 在網頁上的對應 |

幾個決定與踩過的坑：

- **不叫使用者裝東西**：Playwright 自帶的 Node.js 隨發佈包出去（授權列在 THIRD-PARTY-NOTICES），
  瀏覽器用系統的 Edge，不跑 `playwright install`。
- **密碼不經過程式**：登入是使用者在那個視窗裡自己做的，登入狀態留在 `data/browser/`，
  不碰使用者平常的瀏覽器。等登入時，人還在登入頁或 Google、Facebook 的授權頁就不動他的頁面——
  一 goto 就把登入流程打斷了。
- **`--disable-blink-features=AutomationControlled`**：不加的話 `navigator.webdriver` 是 true，
  使用者按「用 Google 登入」會被 Google 以「這個瀏覽器可能不安全」擋下。
- **瀏覽器住在自己的執行緒、自己的 ProactorEventLoop**：Playwright 物件只能在建立它的迴圈裡用；
  `uvicorn --reload` 會把預設迴圈換成 Selector，那種迴圈開不了子行程，瀏覽器根本起不來。
  其他地方用 `Browser.submit()`／`run()` 把工作丟過去。
- **階段在 API 回傳之前就換好**（`Session._start`）：等工作真的開跑才換的話，前端可能先拿到
  舊的階段、以為沒事就不輪詢了。
- **只新增、不改動**：名稱對得上的就算已經有了（簡稱、全半形、臺／台、公司後綴不算差別），
  寧可少補一筆，也不要多出一筆重複的。判斷「那一區印著什麼」時只拿**同一層**的標題當結尾：
  每一筆的職稱也可能是標題標籤，拿它當結尾會把公司名稱切掉，已經有的就被當成沒有。
- **只送列出來的欄位**：每一區用我的資料的哪幾項明寫在 `cake.py`，身分證字號、家人這類
  不在任何一區裡，就送不出去。
- **學歷**：Cake 用西式名稱（文學士（BA）、工學學士（BEng）…），另外有「學士學位」「碩士學位」
  這種通稱。同一級只有一個選項、或有通稱時才自動選；博士分 PhD／MD／JD，要看主修，留給使用者挑。
  選項是打開「新增」表單讀出來再取消的，不寫死。
- **「現任職位」「永久有效」是樣式化的勾選框**：`page.check()` 會失敗，要點印著字的那一塊，
  再看藏起來的 input 有沒有勾上。我的資料寫「至今」就勾現任職位，寫「永久」就勾永久有效。
- **必填欄**：工作經驗要公司、職稱、起訖年月；學歷要學校、學歷、主修；證照要名稱、發照機構、
  發照日期與到期日（或永久有效）。缺的在清單上標出來、當場補齊；補的值只用在這一次，
  不會寫回我的資料。
- **存完要回頭確認**：表單收起來不算數，要在那一區的列表裡看到這一筆才算。
  網站自己印了錯誤訊息就照它說的回報。
- **瀏覽器視窗裡的提示條**（`dom.banner`）：登入後使用者多半還盯著那個視窗，不知道要回到程式
  那一頁；存的時候也可能去動它。所以清單讀好時掛綠色的「請回到履歷自動填寫確認」，存的時候
  每一筆掛琥珀色的「請不要操作這個視窗（第 N／M 筆）」。它掛在 `<html>` 底下而不是 `<body>`
  （讀「那一區印著什麼」用 `body.innerText`，不會把提示讀進去），而且 `pointer-events:none`
  （剛好蓋在「新增」按鈕上時程式的點擊照樣穿過去）。登入頁不掛：那可能是 Google 的頁面。
- **log 不帶值**：只記工作代碼與原因。Playwright 的錯誤訊息可能夾著剛填進去的值，
  所以新增失敗時只記例外的型別，不記 traceback。

測試都不碰真的 Cake：`tests/test_webform.py` 測清單、學位對照與 API（不開瀏覽器）；
`tests/browser/test_webform_flow.py` 對著本機的假 Cake（`fake_cake.html`，照真的個人檔案頁做出
上面那幾個坑）跑整條流程；`tests/browser/test_webform_page.py` 測頁面。

還沒做：基本資料（頭銜、簡介、電話）、求職偏好（必填的求職階段、希望職位我的資料裡沒有）、
專業背景與語言，以及 Cake 以外的平台。

## 10. 待討論

* 前後端 API 介面定義
* ~~CPU 推論的打包：CUDA 版 llama-server 在無 NVIDIA 驅動的機器上起不來~~——那是舊版
  llama.cpp（CUDA 靜態連結進 exe）的情況。現在隨附的 b10153 把 CUDA 後端做成執行時才載入的
  `ggml-cuda.dll`，載入不了就只剩 CPU 後端。2026-09-22 驗過：把 `ggml-cuda.dll` 拿掉（跟沒有
  驅動時一樣是 LoadLibrary 失敗），用產品的參數原樣啟動（`-ngl 999`、`--ctx-size 16384`、掛
  視覺投影檔），9 秒起來、答得出來，log 裡沒有任何 CUDA。不必另外同捆 CPU 版
