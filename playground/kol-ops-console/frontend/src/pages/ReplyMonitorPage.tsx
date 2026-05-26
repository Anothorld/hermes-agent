import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { useEnvStore, toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

type EventRow = {
  id: number;
  ts: string;
  event_type: string;
  kol_identity_id: number;
  stage: string | null;
  sub_status: string | null;
  actor: string;
  env?: string;
};

type Escalation = {
  id: number;
  reason: string;
  ts: string;
  kol_identity_id?: number | null;
  campaign_id?: string | null;
  classifier_confidence?: number | null;
  ai_recommendation?: string | null;
  env?: string | null;
};

type NextReplyType =
  | 'brief_clarification'
  | 'negotiation'
  | 'product_pitch'
  | 'content_followup'
  | 'close_no_reply';

const NEXT_REPLY_OPTIONS: { value: NextReplyType; label: string }[] = [
  { value: 'brief_clarification', label: '澄清 brief / 预算' },
  { value: 'negotiation', label: '价格 / 条款谈判' },
  { value: 'product_pitch', label: '产品介绍' },
  { value: 'content_followup', label: '内容跟进' },
  { value: 'close_no_reply', label: '终止 / 不再跟进' },
];

export function ReplyMonitorPage() {
  const envFilter = useEnvStore((s) => s.env);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [escs, setEscs] = useState<Escalation[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [nextTypes, setNextTypes] = useState<Record<number, NextReplyType>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busyEscalation, setBusyEscalation] = useState<number | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const refresh = useCallback(() => {
    api
      .get<EventRow[]>(`/events/recent?env=${envFilter}&limit=100`)
      .catch(() => [])
      .then((rows) => {
        setEvents(rows ?? []);
        setLastRefresh(new Date());
      });
    api
      .get<Escalation[]>(`/escalations/open?env=${envFilter}`)
      .catch(() => [])
      .then((rows) => setEscs(rows ?? []));
  }, [envFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useDataChannel({
    onMatch: () => {
      refresh();
    },
  });
  usePollingFallback(refresh, 20_000);

  const submitNextAction = async (escalation: Escalation) => {
    const nextReplyType = nextTypes[escalation.id] || 'brief_clarification';
    setBusyEscalation(escalation.id);
    setActionError(null);
    try {
      await api.post(`/escalations/${escalation.id}/next-action`, {
        next_reply_type: nextReplyType,
        human_note: notes[escalation.id] || undefined,
        env: envFilter,
      });
      setNotes((prev) => {
        const next = { ...prev };
        delete next[escalation.id];
        return next;
      });
      toast.success('已记录下一步');
      refresh();
    } catch (ex) {
      setActionError(ex);
      toast.error('记录失败', errorSummary(ex));
    } finally {
      setBusyEscalation(null);
    }
  };

  return (
    <div className="grid grid-cols-3 gap-4">
      <section className="col-span-2 rounded border bg-white p-3">
        <div className="mb-2 flex items-center gap-3">
          <h2 className="font-medium">实时事件流 ({events.length})</h2>
          <button
            type="button"
            onClick={refresh}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
            title="手动刷新（自动每 20 秒）"
          >
            ↻
          </button>
          {lastRefresh && (
            <TimeAgo
              iso={lastRefresh.toISOString()}
              prefix="刷新于"
              className="text-xs text-slate-400"
            />
          )}
        </div>
        <ul className="space-y-1 text-sm">
          {events.map((e) => (
            <li key={e.id} className="flex gap-2 border-b border-slate-100 py-1">
              <TimeAgo iso={e.ts} className="text-slate-400" />
              <span className="font-medium">{e.event_type}</span>
              <span className="text-emerald-700">
                {e.stage}/{e.sub_status}
              </span>
              <span className="ml-auto text-xs text-slate-500">
                KOL #{e.kol_identity_id} · {e.actor}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <aside data-editing className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">未解决升级 ({escs.length})</h2>
        {!!actionError && <ErrorAlert error={actionError} compact />}
        <ul className="space-y-3 text-sm">
          {escs.map((e) => (
            <li key={e.id} className="border-b border-slate-100 pb-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{e.reason}</div>
                  <TimeAgo iso={e.ts} className="text-xs text-slate-400" />
                </div>
                {e.kol_identity_id && (
                  <a
                    href={`/kols/${e.kol_identity_id}`}
                    className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                  >
                    KOL #{e.kol_identity_id}
                  </a>
                )}
              </div>
              <div className="mt-1 space-y-1 text-xs text-slate-500">
                {e.campaign_id && <div>campaign: {e.campaign_id}</div>}
                {typeof e.classifier_confidence === 'number' && (
                  <div>置信度: {(e.classifier_confidence * 100).toFixed(0)}%</div>
                )}
                {e.ai_recommendation && <div className="text-slate-700">{e.ai_recommendation}</div>}
              </div>
              <div className="mt-2 grid gap-2">
                <select
                  value={nextTypes[e.id] || 'brief_clarification'}
                  onChange={(evt) =>
                    setNextTypes((prev) => ({
                      ...prev,
                      [e.id]: evt.target.value as NextReplyType,
                    }))
                  }
                  className="rounded border px-2 py-1 text-xs"
                >
                  {NEXT_REPLY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <input
                  value={notes[e.id] || ''}
                  onChange={(evt) =>
                    setNotes((prev) => ({ ...prev, [e.id]: evt.target.value }))
                  }
                  className="rounded border px-2 py-1 text-xs"
                  placeholder="操作员备注"
                />
                <button
                  type="button"
                  onClick={() => submitNextAction(e)}
                  disabled={busyEscalation === e.id}
                  className="rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busyEscalation === e.id ? '记录中…' : '记录下一步'}
                </button>
              </div>
            </li>
          ))}
          {!escs.length && <li className="text-xs text-slate-400">没有未解决的升级。</li>}
        </ul>
      </aside>
    </div>
  );
}
