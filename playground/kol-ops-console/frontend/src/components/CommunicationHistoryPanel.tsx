import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { ErrorAlert } from './feedback/ErrorAlert';
import { TimeAgo } from './inputs/TimeAgo';

export type CommunicationMessage = {
  message_id?: string | null;
  ts?: string | null;
  event_type?: string;
  label?: string;
  direction?: 'inbound' | 'outbound';
  status?: string;
  from_addr?: string | null;
  to_addr?: string | null;
  subject?: string | null;
  body?: string | null;
  thread_id?: string | null;
  source?: string;
  body_is_snippet?: boolean;
  operator_edited_before_send?: boolean;
  child_skill?: string | null;
};

type ConversationResponse = {
  messages: CommunicationMessage[];
  count: number;
  gmail_available?: boolean;
  truncated?: boolean;
  thread_ids?: string[];
  error?: string;
};

const COLLAPSED_BODY_CHARS = 900;
const RELOAD_DEBOUNCE_MS = 3_000;

function directionTone(direction: string | undefined): string {
  if (direction === 'inbound') return 'border-emerald-200 bg-emerald-50/50';
  if (direction === 'outbound') return 'border-sky-200 bg-sky-50/50';
  return 'border-slate-200 bg-slate-50';
}

function statusBadge(msg: CommunicationMessage): string {
  if (msg.direction === 'inbound') return 'bg-emerald-100 text-emerald-800';
  if (msg.status === 'sent') return 'bg-sky-100 text-sky-800';
  return 'bg-slate-100 text-slate-700';
}

function MessageBody({ body, bodyIsSnippet }: { body: string; bodyIsSnippet?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const truncated = !expanded && body.length > COLLAPSED_BODY_CHARS;
  const display = truncated ? `${body.slice(0, COLLAPSED_BODY_CHARS)}…` : body;

  return (
    <div className="mt-1">
      <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words font-sans text-[12px] leading-relaxed text-slate-800">
        {display}
      </pre>
      {bodyIsSnippet && (
        <p className="mt-0.5 text-[10px] italic text-slate-500">（Gmail snippet，非完整正文）</p>
      )}
      {truncated && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-0.5 text-[11px] text-sky-700 hover:underline"
        >
          展开全文（{body.length} 字符）
        </button>
      )}
      {!truncated && body.length > COLLAPSED_BODY_CHARS && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-0.5 text-[11px] text-sky-700 hover:underline"
        >
          折叠
        </button>
      )}
    </div>
  );
}

function HistoryMessage({ msg }: { msg: CommunicationMessage }) {
  const addr = msg.direction === 'inbound' ? msg.from_addr : msg.to_addr;
  const addrLabel = msg.direction === 'inbound' ? 'From' : 'To';

  return (
    <li className={`rounded border p-2.5 text-sm ${directionTone(msg.direction)}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${statusBadge(msg)}`}>
          {msg.label || (msg.direction === 'inbound' ? 'KOL 来信' : '我方已发送')}
        </span>
        {msg.ts && (
          <TimeAgo iso={msg.ts} className="text-[10px] text-slate-500" />
        )}
        {msg.source === 'gmail' && (
          <span className="text-[10px] text-slate-400" title="正文来自 Gmail 线程（仅已发送/已收信，不含草稿）">
            Gmail
          </span>
        )}
      </div>
      <div className="mt-1 space-y-0.5 text-xs text-slate-700">
        {addr && (
          <div>
            <span className="font-medium text-slate-500">{addrLabel}: </span>
            <span className="font-mono">{addr}</span>
          </div>
        )}
        {msg.subject && (
          <div>
            <span className="font-medium text-slate-500">Subject: </span>
            <span className="font-medium text-slate-900">{msg.subject}</span>
          </div>
        )}
      </div>
      {msg.body?.trim() ? (
        <MessageBody body={msg.body.trim()} bodyIsSnippet={msg.body_is_snippet} />
      ) : (
        <p className="mt-1 text-xs italic text-slate-500">（Gmail 未返回正文）</p>
      )}
    </li>
  );
}

/**
 * Collapsible email thread on Kol detail — loads Gmail SENT + KOL inbound
 * messages for the active campaign (drafts excluded).
 */
export function CommunicationHistoryPanel({
  identityId,
  campaignId,
  env,
  reloadAt = 0,
}: {
  identityId: number;
  campaignId: string;
  env: string;
  reloadAt?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [data, setData] = useState<ConversationResponse | null>(null);
  const lastLoadAtRef = useRef(0);
  const lastReloadAtRef = useRef(0);

  useEffect(() => {
    setData(null);
    setError(null);
    lastLoadAtRef.current = 0;
    lastReloadAtRef.current = 0;
  }, [identityId, campaignId, env]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<ConversationResponse>(
        `/kols/${identityId}/communication-history?`
        + `campaign_id=${encodeURIComponent(campaignId)}&env=${env}`,
      );
      setData(r);
      lastLoadAtRef.current = Date.now();
    } catch (ex) {
      if (ex instanceof ApiError && ex.status === 403) {
        setError(
          new Error(
            '仅本 campaign 绑定的发件邮箱负责人可查看完整沟通历史。请在设置中连接 Gmail，或由负责人接管。',
          ),
        );
      } else {
        setError(ex);
      }
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [identityId, campaignId, env]);

  useEffect(() => {
    if (!expanded) return;
    const needsInitial = data === null && !error;
    const reloadDue =
      reloadAt > 0
      && reloadAt !== lastReloadAtRef.current
      && Date.now() - lastLoadAtRef.current >= RELOAD_DEBOUNCE_MS;
    if (needsInitial || reloadDue) {
      lastReloadAtRef.current = reloadAt;
      load();
    }
  }, [expanded, reloadAt, data, error, load]);

  const messages = data?.messages ?? [];
  const count = data?.count ?? messages.length;
  const gmailAvailable = data?.gmail_available !== false;
  const truncated = Boolean(data?.truncated);
  const noThreads = gmailAvailable && (data?.thread_ids?.length ?? 0) === 0;

  return (
    <div className="rounded border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-slate-50"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-600">
            沟通历史
          </span>
          <span className="text-[10px] text-slate-400">Gmail 已发送 / 来信</span>
          {data !== null && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
              {count} 条
            </span>
          )}
          {loading && (
            <span className="text-[10px] text-slate-400">加载中…</span>
          )}
        </div>
        <span className="text-xs text-slate-400">{expanded ? '收起' : '展开'}</span>
      </button>

      {expanded && (
        <div className="border-t border-slate-200 px-3 py-2.5">
          {error && (
            <div className="mb-2">
              <ErrorAlert
                error={error}
                onRetry={() => {
                  setData(null);
                  setError(null);
                  load();
                }}
              />
            </div>
          )}

          {!loading && !error && data !== null && !gmailAvailable && (
            <p className="text-xs text-amber-800">
              Gmail 未连接或暂时不可用，无法拉取已发送邮件。请检查 Hermes Google 授权后重试。
            </p>
          )}

          {!loading && !error && data !== null && gmailAvailable && noThreads && (
            <p className="text-xs text-slate-500">
              还没有关联的 Gmail 线程（审批通过创建草稿或 KOL 回信后会绑定 thread_id）。
            </p>
          )}

          {!loading && !error && data !== null && gmailAvailable && !noThreads && count === 0 && (
            <p className="text-xs text-slate-500">
              已找到 Gmail 线程，但还没有「我方 SENT」或「KOL 来信」消息（草稿不会显示）。
            </p>
          )}

          {truncated && (
            <p className="mb-2 text-xs text-amber-800">
              仅展示最近 {count} 条邮件记录，更早的消息未加载。
            </p>
          )}

          {messages.length > 0 && (
            <ul className="space-y-2">
              {messages.map((msg) => (
                <HistoryMessage
                  key={msg.message_id ?? `${msg.ts}-${msg.direction}-${msg.subject}`}
                  msg={msg}
                />
              ))}
            </ul>
          )}

          <div className="mt-2 flex justify-end">
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="text-[11px] text-sky-700 hover:underline disabled:text-slate-400"
            >
              刷新
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
