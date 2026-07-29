import type { FieldSpec } from '../api'

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
  if (spec.kind === 'choice') {
    return (
      <select className={INPUT_CLASS} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">(未填)</option>
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
          inputMode="numeric"
          value={value}
          placeholder="60,000"
          onChange={(e) => onChange(formatMoney(e.target.value))}
        />
      </div>
    )
  }

  // date 用 text 而非 <input type="date">：履歷表上的日期格式很雜
  // （2014/09、民國 83 年），限制成 ISO 反而讓人沒辦法照原樣填
  return (
    <input
      className={INPUT_CLASS}
      type="text"
      value={value}
      placeholder={spec.kind === 'date' ? '例如 1996-04-15' : ''}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/** 只留數字並加上千分位。存進 profile 的就是這個字串，會原樣填進履歷。 */
function formatMoney(raw: string): string {
  const digits = raw.replace(/\D/g, '')
  if (!digits) return ''
  return Number(digits).toLocaleString('en-US')
}
