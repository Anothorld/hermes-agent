import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useEnvStore } from '../lib/store';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { REJECT_TAG_LABELS, type RejectTag } from '../constants/rejectTags';

type MetricsResp = {
  env: 'TEST' | 'LIVE' | string;
  window_days: number;
  metrics: {
    first_pass_approval_rate: number;
    avg_handle_minutes: number;
    re_escalation_rate: number;
    manual_touchpoints_per_campaign: number;
    termination_rate: number;
    live_incident_rate: number;
  };
  top_rejection_tags: Array<{ tag: string; count: number }>;
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
  hint: string;
  improve: string;
}> = [
  {
    key: 'first_pass_approval_rate',
    title: '首轮通过率',
    format: (m) => pct(m.first_pass_approval_rate),
    hint: '待审批回信首次即被批准的比例',
    improve: '偏低时：在「待审批」减少驳回，检查 AI 草稿是否过早谈价或事实错误',
  },
  {
    key: 'avg_handle_minutes',
    title: '平均处理时长',
    format: (m) => `${m.avg_handle_minutes.toFixed(1)} 分钟`,
    hint: '从打开待审批到做出决定的时间',
    improve: '偏高时：优先处理 SLA 超时项；复杂 case 可先驳回补资料',
  },
  {
    key: 're_escalation_rate',
    title: '重复升级率',
    format: (m) => pct(m.re_escalation_rate),
    hint: '同一 campaign 多次进入升级队列的比例',
    improve: '偏高时：在升级台一次性补齐缺失事实，避免反复驳回',
  },
  {
    key: 'manual_touchpoints_per_campaign',
    title: '人工触点 / Campaign',
    format: (m) => m.manual_touchpoints_per_campaign.toFixed(2),
    hint: '每个活动平均需要操作员介入的次数',
    improve: '偏高时：检查是否过多 pending 审批或开放升级',
  },
  {
    key: 'termination_rate',
    title: '终止率',
    format: (m) => pct(m.termination_rate),
    hint: '合作流程被终止的比例',
    improve: '偏高时：回顾驳回标签与终止原因，更新策略或产品匹配',
  },
  {
    key: 'live_incident_rate',
    title: 'LIVE 事故率',
    format: (m) => pct(m.live_incident_rate),
    hint: '生产环境异常或误发相关事件比例',
    improve: '偏高时：先在 TEST 验证流程，LIVE 审批务必逐条核对',
  },
];

export function GateMetricsPage() {
  const env = useEnvStore((s) => s.env);
  const [days, setDays] = useState(7);
  const [data, setData] = useState<MetricsResp | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loopOpen, setLoopOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const out = await api.get<MetricsResp>(`/admin/gate-metrics?env=${env}&days=${days}`);
      setData(out);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env, days]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">门禁效果看板</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value={7}>近 7 天</option>
          <option value={14}>近 14 天</option>
          <option value={30}>近 30 天</option>
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
                hint={def.hint}
                improve={def.improve}
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
    </div>
  );
}

function MetricCard({
  title,
  value,
  hint,
  improve,
}: {
  title: string;
  value: string;
  hint: string;
  improve: string;
}) {
  return (
    <div
      className="rounded border border-slate-200 bg-white p-3"
      title={`${hint}\n\n如何改善：${improve}`}
    >
      <div className="text-xs text-slate-500">{title}</div>
      <div className="mt-1 text-base font-semibold text-slate-900">{value}</div>
      <div className="mt-1 text-[11px] leading-snug text-slate-600">{hint}</div>
    </div>
  );
}
