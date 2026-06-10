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

type KolFunnel = {
  maturity_days?: number;
  funnel_window_days?: number | null;
  discovered_total: number;
  prior_collab_excluded: number;
  eligible_total: number;
  mature_eligible_total?: number;
  mature_adopted_within_window_count?: number;
  pending_mature_backlog_count?: number;
  pending_immature_count?: number;
  mature_draft_total?: number;
  mature_replied_within_window_count?: number;
  pending_draft_mature_no_reply_count?: number;
  pending_draft_immature_count?: number;
  initial_outreach_draft_count: number;
  initial_outreach_reply_count: number;
};

type MetricsResp = {
  env: 'TEST' | 'LIVE' | string;
  window_days: number;
  funnel_window_days?: number | null;
  metrics: {
    first_pass_approval_rate: number;
    avg_handle_minutes: number;
    re_escalation_rate: number;
    manual_touchpoints_per_campaign: number;
    termination_rate: number;
    live_incident_rate: number;
    kol_candidate_adoption_rate: number;
    initial_outreach_reply_rate: number;
  };
  audit_meta?: {
    first_pass_decisions_total?: number;
    touched_campaign_count?: number;
    child_escalation_opens?: number;
    escalation_opens_total?: number;
  };
  kol_funnel: KolFunnel;
  top_rejection_tags: Array<{ tag: string; count: number }>;
};

type TrendBucket = 'day' | 'week' | 'month' | 'year';

type TrendsResp = {
  env: string;
  bucket: TrendBucket;
  periods: number;
  series: Partial<Record<keyof MetricsResp['metrics'], TrendPoint[]>>;
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
    key: 're_escalation_rate',
    title: '重复升级率',
    format: (m) => pct(m.re_escalation_rate),
    hint: (data) => {
      const child = data.audit_meta?.child_escalation_opens ?? 0;
      const total = data.audit_meta?.escalation_opens_total ?? 0;
      return `近 ${data.window_days} 天 · 子升级打开数 ÷ 全部升级打开数（${child} / ${total}）`;
    },
    improve: '偏高时：在升级台一次性补齐缺失事实，避免反复驳回',
    trendFormat: 'percent',
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

const KOL_FUNNEL_HELP: Array<{
  key: 'kol_candidate_adoption_rate' | 'initial_outreach_reply_rate';
  title: string;
  format: (m: MetricsResp['metrics']) => string;
  hint: (f: KolFunnel, data: MetricsResp) => string;
  improve: string;
  trendFormat: TrendValueFormat;
}> = [
  {
    key: 'kol_candidate_adoption_rate',
    title: 'KOL候选采纳率',
    format: (m) => pct(m.kol_candidate_adoption_rate),
    hint: (f, data) => {
      const matureDays = f.maturity_days ?? 14;
      const funnelDays = f.funnel_window_days ?? data.funnel_window_days ?? 30;
      const matureEligible = f.mature_eligible_total ?? 0;
      const matureAdopted = f.mature_adopted_within_window_count ?? 0;
      const backlog = f.pending_mature_backlog_count ?? 0;
      const immature = f.pending_immature_count ?? 0;
      const parts = [
        `${matureAdopted} / ${matureEligible} 在发现后 ${matureDays} 天内生成初邀草稿`,
        `（成熟 cohort：发现 ${funnelDays}～${matureDays} 天前）`,
      ];
      if (backlog > 0) {
        parts.push(`待处理积压 ${backlog} 人`);
      }
      if (immature > 0) {
        parts.push(`处理中（未满 ${matureDays} 天）${immature} 人，不计入采纳率`);
      }
      return parts.join('；');
    },
    improve: '偏低时：先清积压，再检查 shortlist、邮箱发现与初邀草稿审批',
    trendFormat: 'percent',
  },
  {
    key: 'initial_outreach_reply_rate',
    title: '初邀回信率',
    format: (m) => pct(m.initial_outreach_reply_rate),
    hint: (f, data) => {
      const matureDays = f.maturity_days ?? 14;
      const funnelDays = f.funnel_window_days ?? data.funnel_window_days ?? 30;
      const matureDrafts = f.mature_draft_total ?? 0;
      const matureReplies = f.mature_replied_within_window_count ?? 0;
      const backlog = f.pending_draft_mature_no_reply_count ?? 0;
      const immature = f.pending_draft_immature_count ?? 0;
      const parts = [
        `${matureReplies} / ${matureDrafts} 在初邀后 ${matureDays} 天内收到回信`,
        `（成熟 cohort：初邀 ${funnelDays}～${matureDays} 天前）`,
      ];
      if (backlog > 0) {
        parts.push(`待回信积压 ${backlog} 人`);
      }
      if (immature > 0) {
        parts.push(`等待期（初邀未满 ${matureDays} 天）${immature} 人，不计入回信率`);
      }
      return parts.join('；');
    },
    improve: '偏低时：核对邮件是否已发出、邮箱是否有效、产品与红人匹配度',
    trendFormat: 'percent',
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

  const emptyFunnel: KolFunnel = {
    discovered_total: 0,
    prior_collab_excluded: 0,
    eligible_total: 0,
    initial_outreach_draft_count: 0,
    initial_outreach_reply_count: 0,
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">门禁效果看板</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          title="审批/升级类指标窗口；KOL 漏斗自动至少按 30 天成熟 cohort"
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
        汇总数字受「近 N 天」控制；趋势图按所选粒度独立统计。KOL 采纳/回信率使用 14 天成熟
        cohort（窗口至少 30 天）。
      </p>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      {!data && !err && <div className="text-sm text-slate-500">加载中…</div>}
      {data && (
        <>
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
            {KOL_FUNNEL_HELP.map((def) => (
              <MetricCard
                key={def.key}
                title={def.title}
                value={def.format(data.metrics)}
                hint={def.hint(data.kol_funnel ?? emptyFunnel, data)}
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
