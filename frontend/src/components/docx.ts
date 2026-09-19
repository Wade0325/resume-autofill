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
  if (page) host.style.zoom = String(width / page.offsetWidth)
  return true
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
