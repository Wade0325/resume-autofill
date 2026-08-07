const NOW = new Date().getFullYear()
const YEARS = Array.from({ length: 101 }, (_, i) => String(NOW + 10 - i))
const MONTHS = Array.from({ length: 12 }, (_, i) => String(i + 1))

const SELECT_CLASS =
  'rounded-md border border-slate-300 px-2 py-2 text-sm bg-white ' +
  'focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500 ' +
  'disabled:bg-slate-50 disabled:text-slate-400'

type Parts = { y: string; m: string; d: string }

type Props = {
  value: string
  onChange: (value: string) => void
}

/** 年／月／日三個下拉，存成「1996年04月15日」。月與日可以不選，值就只到年或到月。 */
export default function DateSelect({ value, onChange }: Props) {
  const parts = parseDate(value)

  // 認不得的舊寫法（例如「84年3月起」）硬塞進下拉會把原本的字吃掉，
  // 改成文字框讓使用者自己看著改；清空後就會回到下拉
  if (!parts) {
    return (
      <div>
        <input
          className={`w-full ${SELECT_CLASS}`}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <p className="mt-1 text-xs text-amber-600">
          這個寫法認不得，清空後可改用下拉選擇
        </p>
      </div>
    )
  }

  const set = (patch: Partial<Parts>) => onChange(format({ ...parts, ...patch }))

  return (
    <div className="flex items-center gap-1.5">
      <select
        className={`${SELECT_CLASS} w-24`}
        value={parts.y}
        onChange={(e) => set(e.target.value ? { y: e.target.value } : { y: '', m: '', d: '' })}
      >
        <option value="">----</option>
        {YEARS.map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>
      <span className="text-sm text-slate-600">年</span>

      <select
        className={`${SELECT_CLASS} w-16`}
        value={parts.m}
        disabled={!parts.y}
        onChange={(e) => set(e.target.value ? { m: e.target.value } : { m: '', d: '' })}
      >
        <option value="">--</option>
        {MONTHS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <span className="text-sm text-slate-600">月</span>

      <select
        className={`${SELECT_CLASS} w-16`}
        value={parts.d}
        disabled={!parts.m}
        onChange={(e) => set({ d: e.target.value })}
      >
        <option value="">--</option>
        {daysIn(parts.y, parts.m).map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>
      <span className="text-sm text-slate-600">日</span>
    </div>
  )
}

/** 認得 1996年04月15日、1996-04-15、1996/04、民國85年3月；認不得回傳 null。 */
export function parseDate(raw: string): Parts | null {
  const s = raw.trim()
  if (!s) return { y: '', m: '', d: '' }
  // 夾雜其他字（「84年3月起」「預計2025年」）一律當作認不得，
  // 只挑數字會把使用者寫的字默默丟掉
  if (!/^[\d\s年月日民國/.-]+$/.test(s)) return null

  const nums = s.match(/\d+/g)
  if (!nums || nums.length > 3) return null

  const roc = s.startsWith('民國')
  const year = roc ? Number(nums[0]) + 1911 : Number(nums[0])
  if (year < NOW - 90 || year > NOW + 10) return null

  const m = nums[1] ? Number(nums[1]) : 0
  const d = nums[2] ? Number(nums[2]) : 0
  if (m > 12 || d > 31) return null

  return { y: String(year), m: m ? String(m) : '', d: d ? String(d) : '' }
}

function format({ y, m, d }: Parts): string {
  if (!y) return ''
  if (!m) return `${y}年`
  if (!d) return `${y}年${pad(m)}月`
  return `${y}年${pad(m)}月${pad(d)}日`
}

function pad(n: string): string {
  return n.padStart(2, '0')
}

function daysIn(y: string, m: string): string[] {
  const count = y && m ? new Date(Number(y), Number(m), 0).getDate() : 31
  return Array.from({ length: count }, (_, i) => String(i + 1))
}
