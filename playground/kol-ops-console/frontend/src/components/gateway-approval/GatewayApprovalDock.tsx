import { Link } from 'react-router-dom';
import { getToken } from '../../api';
import { formatAbsolute, formatRelativeAgo } from '../../lib/time';
import {
  ApprovalChoice,
  GatewayApproval,
  useGatewayApprovals,
} from '../../hooks/useGatewayApprovals';
import { dialog } from '../dialogs/useDialog';

// Global floating dock for gateway-approval requests. Mounted once at
// the App root next to the existing AgentSessionDock. Renders nothing
// when the operator isn't logged in OR when there are zero pending
// approvals — keeping the surface area zero in the common case avoids
// drawing attention to a feature that's only relevant under unattended
// agent automation.

const CHOICE_LABEL: Record<ApprovalChoice, string> = {
  once: '本次允许',
  session: '本会话允许',
  always: '永久允许',
  deny: '拒绝',
};

const CHOICE_TONE: Record<ApprovalChoice, string> = {
  once: 'border-emerald-700 bg-emerald-700/80 hover:bg-emerald-600 text-white',
  session: 'border-sky-700 bg-sky-700/80 hover:bg-sky-600 text-white',
  always: 'border-amber-700 bg-amber-700/80 hover:bg-amber-600 text-white',
  deny: 'border-rose-700 bg-rose-700/80 hover:bg-rose-600 text-white',
};

const KIND_LABEL: Record<string, string> = {
  outreach: '外联',
  reply: '回复',
  draft: '草稿',
  resume: '恢复',
  refine: '润色',
};

export function GatewayApprovalDock() {
  const { items, inflight, errors, count, open, setOpen, resolve } =
    useGatewayApprovals();

  // Hooks above always run; guard the render so the dock stays mounted
  // through login transitions without violating Rules of Hooks (matches
  // AgentSessionDock.tsx:56).
  if (!getToken()) return null;
  if (count === 0) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-0 bottom-32 z-40 flex h-32 w-7 cursor-pointer items-center justify-center gap-2 rounded-l border-y border-l border-amber-700 bg-amber-900/80 text-[11px] font-medium text-amber-50 shadow-lg hover:bg-amber-800"
        style={{ writingMode: 'vertical-rl' }}
        title={`${count} 个待审批的命令`}
      >
        <span className="inline-block h-2 w-2 rounded-full bg-amber-300 animate-pulse" />
        待审批
        <span className="text-amber-200">·{count}</span>
      </button>
    );
  }

  return (
    <div className="fixed right-0 bottom-4 z-50 flex max-h-[70vh] w-[440px] flex-col rounded-l-lg border-y border-l border-amber-700 bg-slate-950 shadow-2xl">
      <div className="flex items-center justify-between border-b border-amber-800/60 px-3 py-2 bg-amber-900/30">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-300 animate-pulse" />
          <span className="text-sm font-semibold text-amber-100">
            待审批命令
          </span>
          <span className="rounded bg-amber-700/40 px-1.5 text-[11px] text-amber-100">
            {count}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
          title="收起（新请求到达时会再次弹出）"
        >
          ›
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {items.map((item) => (
          <ApprovalRow
            key={item.run_id}
            item={item}
            disabled={inflight.has(item.run_id)}
            error={errors.get(item.run_id) ?? null}
            onResolve={(choice) => void resolve(item.run_id, choice)}
          />
        ))}
      </div>
    </div>
  );
}

type RowProps = {
  item: GatewayApproval;
  disabled: boolean;
  error: string | null;
  onResolve: (choice: ApprovalChoice) => void;
};

function ApprovalRow({ item, disabled, error, onResolve }: RowProps) {
  const capturedIso = normalizeIso(item.captured_at);
  const ago = formatRelativeAgo(capturedIso);
  const absolute = formatAbsolute(capturedIso);
  const shortId = item.run_id.length > 12 ? `${item.run_id.slice(0, 8)}…` : item.run_id;
  const kindLabel = KIND_LABEL[item.kind] ?? item.kind;
  const transcriptHref = item.campaign_id
    ? `/campaigns/${encodeURIComponent(item.campaign_id)}/transcript`
    : null;

  const handleClick = async (choice: ApprovalChoice) => {
    if (disabled) return;
    if (choice === 'always') {
      const ok = await dialog.confirm({
        title: '确认永久允许？',
        description:
          '该选择会让 Agent 在后续所有运行中自动放行这条命令（无需再次审批）。一旦设置将影响其它运行，请确认。',
        confirmLabel: '永久允许',
        cancelLabel: '取消',
        variant: 'danger',
      });
      if (!ok) return;
    }
    onResolve(choice);
  };

  return (
    <div className="border-b border-slate-800 px-3 py-2">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-slate-400">
        <div className="flex items-center gap-1.5">
          <span className="rounded bg-slate-800 px-1 text-slate-200">
            {kindLabel}
          </span>
          {transcriptHref ? (
            <Link
              to={transcriptHref}
              className="font-mono text-slate-300 hover:text-sky-300 hover:underline"
              title={`run_id: ${item.run_id}\ncampaign_id: ${item.campaign_id}`}
            >
              {shortId}
            </Link>
          ) : (
            <span className="font-mono text-slate-400" title={`run_id: ${item.run_id}`}>
              {shortId}
            </span>
          )}
        </div>
        <span title={absolute}>{ago}</span>
      </div>

      {item.description && (
        <div className="mb-1 text-[12px] text-slate-200">{item.description}</div>
      )}
      <pre
        className="mb-2 max-h-[3.6em] overflow-hidden whitespace-pre-wrap break-all rounded bg-slate-900 px-2 py-1 font-mono text-[11px] leading-tight text-slate-100"
        title={item.command}
        style={{
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        }}
      >
        {item.command || '(空命令)'}
      </pre>

      <div className="flex flex-wrap gap-1">
        {(['once', 'session', 'always', 'deny'] as const).map((choice) => {
          if (!item.choices.includes(choice)) return null;
          return (
            <button
              key={choice}
              type="button"
              disabled={disabled}
              onClick={() => {
                void handleClick(choice);
              }}
              className={`rounded border px-2 py-0.5 text-[11px] transition-colors disabled:cursor-wait disabled:opacity-60 ${CHOICE_TONE[choice]}`}
              title={CHOICE_LABEL[choice]}
            >
              {CHOICE_LABEL[choice]}
            </button>
          );
        })}
      </div>

      {error && (
        <div
          role="alert"
          className="mt-1 rounded border border-rose-800 bg-rose-950/60 px-2 py-1 text-[11px] text-rose-200"
        >
          {error}
        </div>
      )}
    </div>
  );
}

// Gateway emits ``timestamp`` as a unix-seconds number; older builds
// may already format it. Coerce either into an ISO string the time
// helpers understand.
function normalizeIso(input: string | number | null | undefined): string {
  if (input == null) return '';
  if (typeof input === 'number') {
    return new Date(input * 1000).toISOString();
  }
  // Pure-numeric string — also treat as unix seconds.
  if (/^\d+(\.\d+)?$/.test(input)) {
    return new Date(Number(input) * 1000).toISOString();
  }
  return input;
}
