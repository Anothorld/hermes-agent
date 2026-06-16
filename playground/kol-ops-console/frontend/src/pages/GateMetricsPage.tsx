import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useEnvStore } from '../lib/store';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { KolRegistryTable } from '../components/KolRegistryTable';
import {
  MetricTrendSparkline,
  type TrendPoint,
  type TrendValueFormat,
} from '../components/MetricTrendSparkline';
import { REJECT_TAG_LABELS, type RejectTag } from '../constants/rejectTags';

type KolDiscoverySummary = {
  discovered_total: number;
  passed_count: number;
  pending_count: number;
  rejected_count: number;
  other_count?: number;
  pass_rate: number;
  initial_outreach_draft_count: number;
  initial_outreach_reply_count: number;
  automated_reply_excluded_count: number;
  pending_reply_count: number;
  initial_outreach_reply_rate: number;
  by_status?: Record<string, number>;
};

type KolDiscoveryTrendKey =
  | keyof Pick<
      KolDiscoverySummary,
      | 'discovered_total'
      | 'passed_count'
      | 'pending_count'
      | 'rejected_count'
      | 'initial_outreach_draft_count'
      | 'initial_outreach_reply_count'
      | 'pending_reply_count'
    >
  | 'pass_rate'
  | 'initial_outreach_reply_rate';

type MetricsResp = {
  env: 'TEST' | 'LIVE' | string;
  window_days: number;
  metrics: {
    first_pass_approval_rate: number;
    avg_handle_minutes: number;
    manual_touchpoints_per_campaign: number;
    termination_rate: number;
    live_incident_rate: number;
  };
  audit_meta?: {
    first_pass_decisions_total?: number;
    touched_campaign_count?: number;
  };
  kol_discovery_summary: KolDiscoverySummary;
  top_rejection_tags: Array<{ tag: string; count: number }>;
};

type TrendBucket = 'day' | 'week' | 'month' | 'year';

type TrendsResp = {
  env: string;
  bucket: TrendBucket;
  periods: number;
  series: Partial<
    Record<keyof MetricsResp['metrics'] | KolDiscoveryTrendKey, TrendPoint[]>
  >;
};

const TREND_BUCKET_LABELS: Record<TrendBucket, string> = {
  day: '按天',
  week: '按周',
  month: '按月',
  year: '按年',
};

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function tagLabel(tag: string): string {
  if (tag in REJECT_TAG_LABELS) {
    return REJECT_TAG_LABELS[tag as RejectTag];
  }
  return tag;
}

const METRIC_HELP: Array<{
  key: keyof MetricsResp['metrics'];
  title: string;
  format: (m: MetricsResp['metrics']) => string;
  hint: (data: MetricsResp) => string;
  improve: string;
  trendFormat: TrendValueFormat;
}> = [
  {
    key: 'first_pass_approval_rate',
    title: '回信首轮通过率',
    format: (m) => pct(m.first_pass_approval_rate),
    hint: (data) => {
      const n = data.audit_meta?.first_pass_decisions_total ?? 0;
      return `近 ${data.window_days} 天 · 回信草稿首次审批即通过（不含先「优化重写」再批准）· 样本 ${n} 条`;
    },
    improve: '偏低时：在「待审批」减少驳回，检查 AI 草稿是否过早谈价或事实错误',
    trendFormat: 'percent',
  },
  {
    key: 'avg_handle_minutes',
    title: '平均处理时长',
    format: (m) => `${m.avg_handle_minutes.toFixed(1)} 分钟`,
    hint: (data) =>
      `近 ${data.window_days} 天 · 从打开待审批/升级到做出决定（需 audit 含 opened_at）`,
    improve: '偏高时：优先处理 SLA 超时项；复杂 case 可先驳回补资料',
    trendFormat: 'minutes',
  },
  {
    key: 'manual_touchpoints_per_campaign',
    title: '人工触点 / 有触达活动',
    format: (m) => m.manual_touchpoints_per_campaign.toFixed(2),
    hint: (data) => {
      const camps = data.audit_meta?.touched_campaign_count ?? 0;
      return `近 ${data.window_days} 天 · 有审批/升级触达的活动平均次数（共 ${camps} 个活动）`;
    },
    improve: '偏高时：检查是否过多 pending 审批或开放升级',
    trendFormat: 'decimal',
  },
  {
    key: 'termination_rate',
    title: '升级终止率',
    format: (m) => pct(m.termination_rate),
    hint: (data) => `近 ${data.window_days} 天 · 升级结案中选「终止」的比例`,
    improve: '偏高时：回顾驳回标签与终止原因，更新策略或产品匹配',
    trendFormat: 'percent',
  },
  {
    key: 'live_incident_rate',
    title: 'LIVE 驳回率',
    format: (m) => pct(m.live_incident_rate),
    hint: (data) =>
      `近 ${data.window_days} 天 · LIVE 环境回信草稿被驳回 ÷ 全部 LIVE 回信审批`,
    improve: '偏高时：先在 TEST 验证流程，LIVE 审批务必逐条核对',
    trendFormat: 'percent',
  },
];

const KOL_DISCOVERY_CARDS: Array<{
  key: keyof Pick<
    KolDiscoverySummary,
    'discovered_total' | 'passed_count' | 'pending_count' | 'rejected_count'
  >;
  title: string;
  hint: string;
  valueClass: string;
  trendFormat: TrendValueFormat;
}> = [
  {
    key: 'discovered_total',
    title: '全部发现',
    hint: 'Agent 发现过的 KOL 总数（按红人去重，含各活动来源）',
    valueClass: 'text-slate-900',
    trendFormat: 'decimal',
  },
  {
    key: 'passed_count',
    title: '已通过',
    hint: 'Shortlist 已批准（状态 selected_for_outreach）',
    valueClass: 'text-emerald-700',
    trendFormat: 'decimal',
  },
  {
    key: 'pending_count',
    title: '待处理',
    hint: '尚未做出 shortlist 决定：discovered / shortlisted / needs_review 等',
    valueClass: 'text-amber-700',
    trendFormat: 'decimal',
  },
  {
    key: 'rejected_count',
    title: '已否决',
    hint: 'Shortlist 已拒绝或归档（rejected / archived）',
    valueClass: 'text-rose-700',
    trendFormat: 'decimal',
  },
];

const KOL_REPLY_CARDS: Array<{
  key: keyof Pick<
    KolDiscoverySummary,
    | 'initial_outreach_draft_count'
    | 'initial_outreach_reply_count'
    | 'pending_reply_count'
  >;
  title: string;
  hint: string;
  valueClass: string;
  trendFormat: TrendValueFormat;
}> = [
  {
    key: 'initial_outreach_draft_count',
    title: '有初邀草稿',
    hint: '已生成初邀 Gmail 草稿的红人数量',
    valueClass: 'text-slate-900',
    trendFormat: 'decimal',
  },
  {
    key: 'initial_outreach_reply_count',
    title: '有回信',
    hint: '收到红人真实回信（不含退信 DSN、Out of Office 等自动回复）',
    valueClass: 'text-emerald-700',
    trendFormat: 'decimal',
  },
  {
    key: 'pending_reply_count',
    title: '待回信',
    hint: '已出初邀草稿但尚无真实回信（含仅收到自动退信/自动回复的）',
    valueClass: 'text-amber-700',
    trendFormat: 'decimal',
  },
];

export function GateMetricsPage() {
  const env = useEnvStore((s) => s.env);
  const [days, setDays] = useState(7);
  const [trendBucket, setTrendBucket] = useState<TrendBucket>('week');
  const [data, setData] = useState<MetricsResp | null>(null);
  const [trends, setTrends] = useState<TrendsResp | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loopOpen, setLoopOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [out, trendOut] = await Promise.all([
        api.get<MetricsResp>(`/admin/gate-metrics?env=${env}&days=${days}`),
        api.get<TrendsResp>(
          `/admin/gate-metrics/trends?env=${env}&bucket=${trendBucket}`,
        ),
      ]);
      setData(out);
      setTrends(trendOut);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env, days, trendBucket]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const emptySummary: KolDiscoverySummary = {
    discovered_total: 0,
    passed_count: 0,
    pending_count: 0,
    rejected_count: 0,
    pass_rate: 0,
    initial_outreach_draft_count: 0,
    initial_outreach_reply_count: 0,
    automated_reply_excluded_count: 0,
    pending_reply_count: 0,
    initial_outreach_reply_rate: 0,
  };

  const summary = data?.kol_discovery_summary ?? emptySummary;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">门禁效果看板</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          title="审批/升级类指标窗口"
        >
          <option value={7}>近 7 天</option>
          <option value={14}>近 14 天</option>
          <option value={30}>近 30 天</option>
        </select>
        <select
          value={trendBucket}
          onChange={(e) => setTrendBucket(e.target.value as TrendBucket)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          title="趋势图时间粒度（与上方汇总窗口独立）"
        >
          {(Object.keys(TREND_BUCKET_LABELS) as TrendBucket[]).map((key) => (
            <option key={key} value={key}>
              趋势{TREND_BUCKET_LABELS[key]}
            </option>
          ))}
        </select>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          刷新
        </button>
        <span className="mx-1 text-slate-300">|</span>
        <Link to="/approvals" className="text-sm text-sky-700 hover:underline">
          待审批
        </Link>
        <Link to="/learning" className="text-sm text-sky-700 hover:underline">
          自主学习
        </Link>
        <Link to="/policies" className="text-sm text-sky-700 hover:underline">
          策略编辑
        </Link>
      </div>
      <p className="text-[11px] text-slate-500">
        下方 KOL 发现统计为 {env} 环境全量累计（不受「近 N 天」窗口影响），趋势图按所选粒度展示各时段
        末累计值。审批/升级类指标受「近 N 天」控制。
      </p>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      {!data && !err && <div className="text-sm text-slate-500">加载中…</div>}
      {data && (
        <>
          <div className="rounded border border-slate-200 bg-white p-3">
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <div className="text-sm font-medium text-slate-800">KOL 发现统计</div>
              <div className="flex flex-wrap items-end gap-4 text-[11px] text-slate-500">
                <div title="已通过 ÷ 全部发现（各时段末累计）">
                  <div>通过率 {pct(summary.pass_rate)}</div>
                  <MetricTrendSparkline
                    points={trends?.series?.pass_rate}
                    valueFormat="percent"
                    colorClass="bg-emerald-500"
                  />
                </div>
                <div
                  title={`有回信 ÷ 有初邀草稿（已排除自动退信/自动回复 ${summary.automated_reply_excluded_count}）`}
                >
                  <div>
                    初邀回信率 {pct(summary.initial_outreach_reply_rate)}（
                    {summary.initial_outreach_reply_count} /{' '}
                    {summary.initial_outreach_draft_count}）
                  </div>
                  <MetricTrendSparkline
                    points={trends?.series?.initial_outreach_reply_rate}
                    valueFormat="percent"
                    colorClass="bg-sky-500"
                  />
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {KOL_DISCOVERY_CARDS.map((def) => (
                <div
                  key={def.key}
                  className="rounded border border-slate-100 bg-slate-50 px-3 py-2"
                  title={def.hint}
                >
                  <div className="text-xs text-slate-500">{def.title}</div>
                  <div className={`mt-1 text-2xl font-semibold ${def.valueClass}`}>
                    {summary[def.key]}
                  </div>
                  <MetricTrendSparkline
                    points={trends?.series?.[def.key]}
                    valueFormat={def.trendFormat}
                  />
                  <div className="mt-1 text-[11px] leading-snug text-slate-600">
                    {def.hint}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-1 gap-2 border-t border-slate-100 pt-3 sm:grid-cols-3">
              {KOL_REPLY_CARDS.map((def) => (
                <div
                  key={def.key}
                  className="rounded border border-slate-100 bg-slate-50 px-3 py-2"
                  title={def.hint}
                >
                  <div className="text-xs text-slate-500">{def.title}</div>
                  <div className={`mt-1 text-xl font-semibold ${def.valueClass}`}>
                    {summary[def.key]}
                  </div>
                  <MetricTrendSparkline
                    points={trends?.series?.[def.key]}
                    valueFormat={def.trendFormat}
                  />
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-2 lg:grid-cols-3">
            {METRIC_HELP.map((def) => (
              <MetricCard
                key={def.key}
                title={def.title}
                value={def.format(data.metrics)}
                hint={def.hint(data)}
                improve={def.improve}
                trendPoints={trends?.series?.[def.key]}
                trendFormat={def.trendFormat}
              />
            ))}
          </div>
          <div className="rounded border border-slate-200 bg-white p-3">
            <div className="mb-2 text-sm font-medium text-slate-800">高频驳回标签</div>
            {data.top_rejection_tags.length === 0 ? (
              <div className="text-xs text-slate-500">暂无结构化标签数据</div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {data.top_rejection_tags.map((row) => (
                  <span
                    key={row.tag}
                    className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
                    title={row.tag}
                  >
                    {tagLabel(row.tag)} · {row.count}
                  </span>
                ))}
              </div>
            )}
            {data.top_rejection_tags.length > 0 && (
              <p className="mt-2 text-[11px] text-slate-500">
                标签来自结构化驳回；可在{' '}
                <Link to="/learning" className="text-sky-700 hover:underline">
                  自主学习
                </Link>{' '}
                查看近期驳回信号，并沉淀到回信策略。
              </p>
            )}
          </div>
          <details
            open={loopOpen}
            onToggle={(e) => setLoopOpen(e.currentTarget.open)}
            className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
          >
            <summary className="cursor-pointer font-medium text-slate-800">
              学习闭环说明（操作员版）
            </summary>
            <ol className="mt-2 list-decimal space-y-1 pl-4">
              <li>
                <strong>驳回回信</strong> → 写入驳回学习 policy，运行时作为 negative few-shot 注入。
              </li>
              <li>
                <strong>编辑后发送</strong> → Gmail 对齐终稿 diff，累积编辑批次 → 生成学习提案。
              </li>
              <li>
                <strong>批准学习提案</strong> → 更新公司风格与回信策略 policy。
              </li>
              <li>
                <strong>策略升格</strong>（可选）→ 稳定段落写入技能参考文件，需 sync skills。
              </li>
            </ol>
          </details>
        </>
      )}
      <KolRegistryTable />
    </div>
  );
}

function MetricCard({
  title,
  value,
  hint,
  improve,
  trendPoints,
  trendFormat,
}: {
  title: string;
  value: string;
  hint: string;
  improve: string;
  trendPoints?: TrendPoint[];
  trendFormat: TrendValueFormat;
}) {
  return (
    <div
      className="rounded border border-slate-200 bg-white p-3"
      title={`${hint}\n\n如何改善：${improve}`}
    >
      <div className="text-xs text-slate-500">{title}</div>
      <div className="mt-1 text-base font-semibold text-slate-900">{value}</div>
      <MetricTrendSparkline points={trendPoints} valueFormat={trendFormat} />
      <div className="mt-1 text-[11px] leading-snug text-slate-600">{hint}</div>
    </div>
  );
}
