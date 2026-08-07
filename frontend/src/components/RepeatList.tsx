import type { FieldSpec } from '../api'
import Field from './Field'

type Item = Record<string, string>

type Props = {
  title: string
  specs: FieldSpec[] // 例如 education[].school、education[].department …
  items: Item[]
  onChange: (items: Item[]) => void
  isHighlighted?: (specKey: string, index: number) => boolean
}

/** 列的順序就是填進表格的順序。 */
export default function RepeatList({ title, specs, items, onChange, isHighlighted }: Props) {
  function update(index: number, key: string, value: string) {
    const next = items.map((it, i) => (i === index ? { ...it, [key]: value } : it))
    onChange(next)
  }

  function move(index: number, delta: number) {
    const target = index + delta
    if (target < 0 || target >= items.length) return
    const next = [...items]
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold text-slate-900">{title}</h2>
        <button
          type="button"
          onClick={() => onChange([...items, {}])}
          className="text-sm px-3 py-1.5 rounded-md border border-slate-300 hover:bg-slate-50"
        >
          ＋ 新增一筆
        </button>
      </div>

      {items.length === 0 && (
        <p className="text-sm text-slate-400 py-4 text-center">尚未新增，點右上角新增一筆</p>
      )}

      <div className="space-y-4">
        {items.map((item, i) => (
          <div key={i} className="border border-slate-200 rounded-md p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-slate-500">第 {i + 1} 筆</span>
              <div className="flex gap-1">
                <SmallButton onClick={() => move(i, -1)} disabled={i === 0} title="上移">
                  ↑
                </SmallButton>
                <SmallButton
                  onClick={() => move(i, 1)}
                  disabled={i === items.length - 1}
                  title="下移"
                >
                  ↓
                </SmallButton>
                <SmallButton
                  onClick={() => onChange(items.filter((_, j) => j !== i))}
                  title="刪除這一筆"
                  danger
                >
                  刪除
                </SmallButton>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {specs.map((spec) => {
                const sub = spec.key.split('[].')[1]
                return (
                  <div
                    key={spec.key}
                    className={isHighlighted?.(spec.key, i) ? 'ring-2 ring-sky-400 rounded-md' : ''}
                  >
                    <Field
                      spec={spec}
                      value={item[sub] ?? ''}
                      onChange={(v) => update(i, sub, v)}
                    />
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function SmallButton({
  onClick,
  disabled,
  title,
  danger,
  children,
}: {
  onClick: () => void
  disabled?: boolean
  title: string
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`text-xs px-2 py-1 rounded border disabled:opacity-30 ${
        danger
          ? 'border-rose-200 text-rose-600 hover:bg-rose-50'
          : 'border-slate-300 text-slate-600 hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  )
}
