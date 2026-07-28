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
      <span className="text-sm text-slate-700">
        {spec.label}
        {spec.sensitive && (
          <span
            className="ml-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
            title="敏感欄位，除非在設定中開啟，否則不會自動填入履歷"
          >
            預設不自動填
          </span>
        )}
      </span>
      <div className="mt-1">{renderInput(spec, value, onChange)}</div>
    </label>
  )
}

function renderInput(spec: FieldSpec, value: string, onChange: (v: string) => void) {
  if (spec.kind === 'choice') {
    return (
      <select className={INPUT_CLASS} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">（未填）</option>
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
