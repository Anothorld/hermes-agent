import { useMemo } from 'react';
import {
  buildNoxInsightsCategories,
  type NoxDashboardCategory,
  type NoxDashboardItem,
} from '../lib/noxDashboardCategories';
import { buildChartPayload, NOX_CHART_LABELS } from '../lib/noxDistributionParse';
import {
  NoxAgeSplitChart,
  NoxHorizontalBarChart,
  NoxPieChart,
} from './NoxDistributionChart';

type Props = {
  facts: Record<string, unknown>;
  /** When set, only render these category ids (e.g. ``['audience']``). */
  categoryIds?: string[];
  /** Narrow popover: single column, stacked charts, no horizontal bleed. */
  variant?: 'default' | 'popover';
};

const LONG_TEXT_LABELS = new Set(['合作优点', '合作风险点', '全部内容标签', '受众兴趣', '合作品牌']);

function NoxChartBlock({
  item,
  facts,
  stackedCharts,
}: {
  item: NoxDashboardItem;
  facts: Record<string, unknown>;
  stackedCharts?: boolean;
}) {
  const payload = useMemo(
    () => buildChartPayload(item.label, item.value, facts, item.factKey),
    [item.label, item.value, item.factKey, facts],
  );

  if (!payload) {
    return (
      <div className="min-w-0">
        <div className="text-xs font-medium text-violet-800">{item.label}</div>
        <p className="mt-0.5 text-xs leading-snug text-slate-700">{item.value}</p>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-1 text-xs font-semibold text-violet-900">{item.label}</div>
      {payload.kind === 'age' ? (
        <NoxAgeSplitChart groups={payload.groups} stacked={stackedCharts} />
      ) : payload.kind === 'pie' ? (
        <NoxPieChart
          segments={payload.segments}
          unit={payload.unit}
          compact
          stacked={stackedCharts}
        />
      ) : (
        <NoxHorizontalBarChart segments={payload.segments} unit={payload.unit} />
      )}
    </div>
  );
}

function CategoryCard({
  cat,
  facts,
  variant = 'default',
}: {
  cat: NoxDashboardCategory;
  facts: Record<string, unknown>;
  variant?: 'default' | 'popover';
}) {
  const isPopover = variant === 'popover';
  const stackedCharts = isPopover;
  const chartItems = cat.items.filter(
    (i) => NOX_CHART_LABELS.has(i.label) && i.value !== '—',
  );
  const scalarItems = cat.items.filter((i) => !NOX_CHART_LABELS.has(i.label));

  return (
    <section
      className={[
        'min-w-0 overflow-hidden rounded-lg border border-violet-100 bg-white/90',
        isPopover ? 'p-2' : 'p-3',
      ].join(' ')}
    >
      <header className="mb-2 border-b border-violet-50 pb-1">
        <h3 className="text-xs font-semibold text-violet-900">{cat.title}</h3>
        {cat.description && (
          <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{cat.description}</p>
        )}
      </header>

      <div
        className={[
          'grid min-w-0 grid-cols-1 gap-x-3 gap-y-2.5',
          isPopover ? '' : 'sm:grid-cols-2',
        ].join(' ')}
      >
        {chartItems.map((item) => (
          <div
            key={`${cat.id}-chart-${item.label}`}
            className={[
              'min-w-0 overflow-hidden',
              !isPopover && item.label === '年龄分布' ? 'sm:col-span-2' : '',
            ].join(' ')}
          >
            <NoxChartBlock
              item={item}
              facts={facts}
              stackedCharts={stackedCharts}
            />
          </div>
        ))}
        {scalarItems.map((item) => (
          <div
            key={`${cat.id}-${item.label}`}
            className={[
              'min-w-0 overflow-hidden rounded-md border border-violet-50/80 bg-violet-50/20 px-2 py-1.5',
              !isPopover && LONG_TEXT_LABELS.has(item.label) ? 'sm:col-span-2' : '',
            ].join(' ')}
          >
            <dt className="text-xs font-medium text-slate-600">{item.label}</dt>
            <dd className="mt-0.5 break-words text-xs leading-snug text-slate-900">
              {item.value}
            </dd>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Unified Nox diligence data (profile / audience / content / cooperation / meta). */
export function NoxInsightsSections({
  facts,
  categoryIds,
  variant = 'default',
}: Props) {
  const categories = useMemo(() => {
    const all = buildNoxInsightsCategories(facts);
    if (!categoryIds?.length) return all;
    const allowed = new Set(categoryIds);
    return all.filter((c) => allowed.has(c.id));
  }, [facts, categoryIds]);
  if (!categories.length) return null;

  const isPopover = variant === 'popover';

  return (
    <div
      className={[
        'grid min-w-0 grid-cols-1 gap-2.5',
        isPopover ? '' : 'lg:grid-cols-2',
      ].join(' ')}
    >
      {categories.map((cat) => (
        <CategoryCard key={cat.id} cat={cat} facts={facts} variant={variant} />
      ))}
    </div>
  );
}
