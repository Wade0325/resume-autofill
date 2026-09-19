import type { FieldSpec } from '../api'
import DateSelect from './DateSelect'

type Props = {
  spec: FieldSpec
  value: string
  onChange: (value: string) => void
}

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm ' +
  'focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500'

export default function Field({ spec, value, onChange }: Props) {
  return (
    <label className="block">
      <span className="text-sm text-slate-700">{spec.label}</span>
      <div className="mt-1">{renderInput(spec, value, onChange)}</div>
    </label>
  )
}

function renderInput(spec: FieldSpec, value: string, onChange: (v: string) => void) {
  if (spec.kind === 'date') {
    return <DateSelect value={value} onChange={onChange} />
  }

  if (spec.kind === 'choice') {
    // 值不在選項裡（匯入時抓到的「單身」之類）也要照實顯示：不然畫面上是「(未填)」，
    // 完成度卻算已填，存檔時還會原樣留著
    const offList = value !== '' && !spec.choices.includes(value)
    return (
      <select className={INPUT_CLASS} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">(未填)</option>
        {offList && <option value={value}>{value}（不在選項裡）</option>}
        {spec.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    )
  }

  if (spec.kind === 'longtext') {
    return (
      <textarea
        className={INPUT_CLASS}
        rows={6}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  if (spec.kind === 'money') {
    return (
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-500">
          NT$
        </span>
        <input
          className={`${INPUT_CLASS} pl-11`}
          type="text"
          value={value}
          placeholder="60,000"
          onChange={(e) => onChange(e.target.value)}
          // 離開欄位才整理千分位：邊打邊改，游標會一直跳到最後
          onBlur={(e) => {
            const tidy = formatMoney(e.target.value)
            if (tidy !== e.target.value) onChange(tidy)
          }}
        />
      </div>
    )
  }

  return (
    <input
      className={INPUT_CLASS}
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/**
 * 金額裡的數字加上千分位，其他字原樣：「40000~50000」→「40,000~50,000」，
 * 「依公司規定」「面議」不動。存進 profile 的就是這個字串，會原樣填進履歷。
 * 以前是把非數字全部剝掉：「40,000~50,000」變成「4,000,050,000」、「依公司規定」變成空白。
 * 四位以上、不是 0 開頭的整數才整理——小數點後面、「0912」這種不動。
 */
function formatMoney(raw: string): string {
  return raw.replace(/(?<![\d.])\d+(?:,\d+)*/g, (m) => {
    const digits = m.replace(/,/g, '')
    return digits.length >= 4 && !digits.startsWith('0')
      ? Number(digits).toLocaleString('en-US')
      : m
  })
}
