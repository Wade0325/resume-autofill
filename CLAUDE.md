# 給 AI 助手的專案須知

本地端、離線的 Word 履歷表自動填寫工具。使用者把公司給的空白 `.docx` 丟進來，
程式判斷每一格該填什麼，套用「我的資料」後輸出填好的檔案。

細節在 `docs/DEVELOPMENT.md`。這份只寫**動手之前一定要知道的事**——大多是踩過坑換來的。

## 四條硬規則

**1. 不能要使用者安裝任何其他軟體。** 不可以用 LibreOffice、Word、`docx2pdf`、
`win32com` 或任何轉檔工具，也不用 PyInstaller。版面示意圖是自己畫的
（`filler.render_pages`）、輸出 PDF 走瀏覽器列印（`components/docx.ts` 的 `printDocx`），
都是為了這條。舊版 `.doc` 直接擋掉請使用者另存新檔，不為了它引進轉檔工具。

**2. 個資不進版控、不進日誌。** `data/`（含 `app.db`）、`claude_code_in_agent/`
（研究用的考題是本人的真實履歷）都在 `.gitignore`。寫 log 時**不可以印出履歷的值**——
要追查就記欄位代碼或工作代碼。測試資料一律虛構，表格用 `tools/make_sample.py` 當場產生。

**3. 變更走分支，合併要經過維護者確認。** 不要自行 push。

**4. `.ps1` 必須存成 UTF-8 with BOM。** PowerShell 5.1 沒有 BOM 會用 ANSI 讀，
中文全部亂碼而且直接 ParserError。

## 改動之後怎麼驗

```bash
pip install -e ".[test,lint]"
ruff check backend tools tests
pytest                    # 109 項，約 50 秒，不需要模型也不需要顯卡
pytest -m ""              # 加上瀏覽器那一組（要先 npm --prefix frontend run build）
```

CI（`.github/workflows/ci.yml`）跑的就是這些。動到填寫邏輯的話，光有測試還不夠，
見下一節。

## 動到填寫邏輯時的額外要求

填寫正確率靠研究迴圈把關（`claude_code_in_agent/`，未進版控，只在維護者的機器上）。
三份考題目前是 67/70/48 全對。

- **改完不要只看分數，要比「送進模型的東西有沒有變」**：逐格那一輪的候選欄位
  （`filler.usable_fields`）、一列一筆那一輪的 enum、`parse()` 認出來的位置與標題判讀。
  分數會因為模型本身的變異上下跳，這三份清單不會。
- **新增欄位一定要順手加進 `filler.ASK_ONLY_IF_MENTIONED`。** 實測給模型的候選清單
  **多一項**（118 → 119），三份考題就從 67/70/48 掉成 66/68/48，而且掉的格子跟新欄位
  毫不相干——看漏填清單看不出是自己害的。
- **分數變差時先確認模型服務還活著**，再懷疑自己的程式。有一次「連主線也掉分」，
  真相是 llama-server 已經崩潰了。
- 同時改了程式和模型伺服器設定時，先用未改動的碼、同一台 server 跑一次對照組。

## 平台與硬體

Windows 專用：啟動器是 `net10.0-windows`、打包是 PowerShell、`backend/model_manager.py`
有幾處沒有防護的 Windows 專屬呼叫（`subprocess.CREATE_NO_WINDOW`、`powershell`）。
CI 的測試因此跑 windows-latest；前端沒有這個問題，跑 ubuntu。

模型是 llama-server（預設埠 8085），**整顆放上 GPU**（`--n-gpu-layers 999`）。
曾經改成讓 llama.cpp 自動配置，結果變成 CPU／GPU 混合：慢 40%，而且跑約 40 分鐘後
以 `bad allocation` 崩潰。裝不下的機器用 `RESUME_AUTOFILL_GPU_LAYERS` 指定層數。
同一時間只能有一個 llama-server，動 8085 之前先確認沒有別人在用。

## 介面文字

給使用者看的訊息（`backend/actions.py` 的 action 通道）寫**白話短句**：動作＋成功或失敗，
數字和代碼留給開發者 log。例如「匯出履歷「○○」成功」，不是「write_output 完成 written=91」。
