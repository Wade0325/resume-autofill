import { renderAsync } from 'docx-preview'

/**
 * 把 .docx 渲染進容器並縮放到欄寬。文件用實際紙張寬度渲染，要縮才塞得下；
 * 用 zoom 而非 transform：transform 不會重排，容器高度會停在縮放前的尺寸，
 * 底下留一大片空白。
 * 回傳 false＝渲染途中被取消（StrictMode 的第一輪 effect），容器已清空。
 */
export async function renderDocxInto(
  blob: Blob,
  host: HTMLDivElement,
  token: { cancelled: boolean },
  fit = true,
): Promise<boolean> {
  host.replaceChildren()
  host.style.zoom = '1'
  const width = host.clientWidth
  // renderAltChunks 預設開著：文件夾帶的 HTML 片段（altChunk）會放進同源、沒沙箱的
  // <iframe srcdoc>，裡面的腳本一插進頁面就執行，不必點任何東西。履歷表用不到這種片段
  await renderAsync(blob, host, undefined, {
    className: 'docx',
    inWrapper: false,
    renderAltChunks: false,
  })
  if (token.cancelled) {
    host.replaceChildren()
    return false
  }
  neutralizeLinks(host)
  const page = host.querySelector('section')
  // 列印用的那份不能縮：要照紙張原尺寸排版，而且它放在畫面外，量不到欄寬
  if (page && fit) host.style.zoom = String(width / page.offsetWidth)
  return true
}

const PRINT_ID = 'print-root'

/**
 * 把一份 .docx 交給瀏覽器列印（列印對話框裡選「另存為 PDF」就是 PDF）。
 *
 * 不用任何轉檔工具：文件本來就已經在瀏覽器裡渲染得出來，列印時把整個 app 藏起來、
 * 只留這一份就好。Windows 內建「Microsoft Print to PDF」，各家瀏覽器也都有
 * 「另存為 PDF」，使用者不必額外安裝任何東西。
 *
 * 紙張尺寸照文件自己的（求職表格不一定是 A4），邊界設 0——頁邊距已經畫在 docx-preview
 * 排出來的 section 裡，再加一層瀏覽器邊界會把內容往內擠、右邊與下面被裁掉。
 */
export async function printDocx(blob: Blob): Promise<void> {
  cleanupPrint()
  const host = document.createElement('div')
  host.id = PRINT_ID
  // 放在畫面外、但照樣排版：display:none 量不到頁面尺寸，@page 就設不對
  host.style.cssText = 'position:absolute;left:-10000px;top:0;visibility:hidden'
  document.body.appendChild(host)
  const style = document.createElement('style')
  style.id = `${PRINT_ID}-style`
  document.head.appendChild(style)

  try {
    await renderDocxInto(blob, host, { cancelled: false }, false)
  } catch (e) {
    cleanupPrint()
    throw e
  }

  const page = host.querySelector('section')
  const size = page ? `size: ${mm(page.offsetWidth)}mm ${mm(page.offsetHeight)}mm;` : ''
  style.textContent = `@media print {
    @page { ${size} margin: 0 }
    body > *:not(#${PRINT_ID}) { display: none !important }
    #${PRINT_ID} { position: static; left: auto; visibility: visible; zoom: 1 }
    #${PRINT_ID} section {
      box-shadow: none !important; margin: 0 !important;
      break-after: page; page-break-after: always;
    }
    #${PRINT_ID} section:last-of-type { break-after: auto; page-break-after: auto }
  }`

  // Chrome／Edge 會發 afterprint，Safari 不一定——留一個保險，別讓這份留在 DOM 裡
  window.addEventListener('afterprint', cleanupPrint, { once: true })
  window.print()
  window.setTimeout(cleanupPrint, 60_000)
}

/** CSS 的 1px ＝ 1/96 英吋。@page 只吃實體長度，px 有些瀏覽器不收。 */
function mm(px: number): string {
  return ((px / 96) * 25.4).toFixed(1)
}

function cleanupPrint() {
  document.getElementById(PRINT_ID)?.remove()
  document.getElementById(`${PRINT_ID}-style`)?.remove()
}

/**
 * docx-preview 把文件裡的超連結原樣變成 <a href>，而預覽就渲染在這個網頁裡：
 * 公司給的表格若夾著 javascript: 連結，點下去就能讀走「我的資料」。
 * 只留一般網址、信箱與文件內錨點（另開分頁，不把這一頁換掉），其餘拿掉 href——字還在，只是點不動。
 */
function neutralizeLinks(host: HTMLElement) {
  for (const a of host.querySelectorAll('a[href]')) {
    const href = (a.getAttribute('href') ?? '').trim()
    if (href.startsWith('#')) continue
    if (/^(https?:|mailto:)/i.test(href)) {
      a.setAttribute('target', '_blank')
      a.setAttribute('rel', 'noopener noreferrer')
    } else {
      a.removeAttribute('href')
    }
  }
}
