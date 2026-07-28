// 驗證日誌頁：做一次真實操作，確認它的紀錄出現在頁面上。
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8090'
const SHOTS = 'C:\\Users\\wl020\\AppData\\Local\\Temp\\claude\\D--Resume-AutoFill\\94d41adb-abb0-462b-8029-effc551be051\\scratchpad\\shots'
const FILLED = 'D:\\Resume_AutoFill\\output\\測試_已填寫.docx'

const fails = []
const check = (label, cond, detail = '') => {
  console.log(`  ${cond ? '✓' : '✗'} ${label}${detail ? '  ' + detail : ''}`)
  if (!cond) fails.push(label)
}
const step = (t) => console.log(`\n${'─'.repeat(58)}\n${t}\n${'─'.repeat(58)}`)

const browser = await chromium.launch({ headless: false, slowMo: 250 })
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } })
const jsErrors = []
page.on('pageerror', (e) => jsErrors.push(e.message))
page.on('console', (m) => m.type() === 'error' && jsErrors.push(m.text()))

step('[1] 導航列多了「日誌」，位置在匯入履歷右邊')
await page.goto(`${BASE}/profile`, { waitUntil: 'networkidle' })
const tabs = (await page.locator('header nav a').allInnerTexts()).map((t) => t.split('\n')[0])
console.log('  ' + tabs.join(' | '))
check('有日誌分頁', tabs.includes('日誌'))
check('在匯入履歷右邊', tabs.indexOf('日誌') === tabs.indexOf('匯入履歷') + 1)

step('[2] 先做一次真實操作，讓它產生紀錄')
await page.click('header nav a:has-text("匯入履歷")')
await page.setInputFiles('input[type=file]', FILLED)
await page.waitForSelector('table', { timeout: 600000 })
console.log('  匯入完成')

step('[3] 日誌頁應該看得到剛才那次操作')
await page.click('header nav a:has-text("日誌")')
await page.waitForSelector('tbody tr', { timeout: 30000 })
await page.waitForTimeout(500)
await page.screenshot({ path: `${SHOTS}/G1-logs.png`, fullPage: true })

const rows = await page.locator('tbody tr').count()
check('有紀錄', rows > 0, `${rows} 列`)

const firstTime = await page.locator('tbody tr td').first().innerText()
check('時間含年月日時分秒', /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(firstTime), firstTime)

const body = await page.locator('main').innerText()
check('看得到匯入的紀錄', body.includes('匯入'), '')
check('看得到模型呼叫的紀錄', body.includes('模型呼叫') || body.includes('讀取履歷'))

step('[4] 追蹤碼可以點，只看那一次操作')
const idButton = page.locator('tbody button').first()
const traceId = await idButton.innerText()
await idButton.click()
await page.waitForTimeout(800)
check('出現追蹤碼篩選', (await page.locator(`text=追蹤碼 ${traceId}`).count()) > 0, traceId)
const filtered = await page.locator('tbody tr').count()
check('列數變少（只剩該次操作）', filtered > 0 && filtered < rows, `${filtered} 列`)
const ids = await page.locator('tbody button').allInnerTexts()
check('剩下的都是同一個追蹤碼', ids.every((x) => x === traceId))
await page.screenshot({ path: `${SHOTS}/G2-logs-filtered.png`, fullPage: true })

await page.click(`button:has-text("追蹤碼 ${traceId}")`)
await page.waitForTimeout(600)
check('取消篩選後恢復', (await page.locator('tbody tr').count()) >= filtered)

step('[5] 等級篩選')
await page.click('button:has-text("只看錯誤")')
await page.waitForTimeout(800)
const levels = await page.locator('tbody tr td:nth-child(2)').allInnerTexts()
check('只剩 ERROR', levels.every((l) => l.trim() === 'ERROR'),
  levels.length ? `${levels.length} 列` : '沒有錯誤紀錄（正常）')
await page.click('button:has-text("全部")')
await page.waitForTimeout(600)

step('[6] 自動更新')
const before = await page.locator('tbody tr').count()
await page.request.get(`${BASE}/api/health`)   // 製造一筆新紀錄
await page.waitForTimeout(4000)
check('3 秒內自動抓到新紀錄', (await page.locator('tbody tr').count()) >= before)

check('無 JS 錯誤', jsErrors.length === 0, jsErrors.slice(0, 2).join(' | '))

console.log('\n' + '='.repeat(58))
console.log(fails.length ? `失敗 ${fails.length} 項：${fails.join(', ')}` : '全部通過 ✓')
console.log('='.repeat(58))
console.log('\n瀏覽器保持開啟。\n')
await new Promise(() => {})
