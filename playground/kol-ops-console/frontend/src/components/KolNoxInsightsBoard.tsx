import { useMemo } from 'react';
import {
  buildNoxDashboardCategories,
  hasAnyNoxFacts,
} from '../lib/noxDashboardCategories';

export function KolNoxInsightsBoard({ facts }: { facts: Record<string, unknown> }) {
  const categories = useMemo(() => buildNoxDashboardCategories(facts), [facts]);

  if (!hasAnyNoxFacts(facts) && categories.length === 0) {
    return null;
  }

  if (categories.length === 0) {
    return (
      <div className="border-t border-violet-100 bg-violet-50/30 px-4 py-3 text-xs text-violet-800">
        已有 Nox 标记，但尚无可展示的结构化指标。请重新运行尽调以写入完整数据。
      </div>
    );
  }

  return (
    <div className="border-t border-violet-100 bg-gradient-to-b from-violet-50/50 to-white px-4 py-4">
      <div className="mb-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-violet-900">
          Nox 数据看板
        </div>
        <p className="mt-0.5 text-[11px] text-violet-700/90">
          来自 Nox CLI 的尽调 / 联系人数据，按类别汇总（与下方「已确认事实」同源）。
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {categories.map((cat) => (
          <section
            key={cat.id}
            className="rounded-lg border border-violet-100 bg-white/90 p-3 shadow-sm"
          >
            <header className="mb-2 border-b border-violet-50 pb-1.5">
              <h3 className="text-sm font-semibold text-violet-900">{cat.title}</h3>
              <p className="text-[10px] text-slate-500">{cat.description}</p>
            </header>
            <dl className="space-y-1.5">
              {cat.items.map((item) => (
                <div key={item.factKey} className="grid grid-cols-[minmax(5rem,34%)_1fr] gap-2 text-xs">
                  <dt className="font-medium text-slate-600">{item.label}</dt>
                  <dd className="break-words text-slate-900">{item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  );
}
