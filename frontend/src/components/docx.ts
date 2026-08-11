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
  await renderAsync(blob, host, undefined, { className: 'docx', inWrapper: false })
  if (token.cancelled) {
    host.replaceChildren()
    return false
  }
  const page = host.querySelector('section')
  if (page) host.style.zoom = String(width / page.offsetWidth)
  return true
}
