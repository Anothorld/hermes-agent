import { useEffect, useState } from 'react';
import { api } from '../api';
import InboundEmailCard, { type InboundEmail } from './InboundEmailCard';

/**
 * Structured renderers for the contents of pending ``approval.*`` facts.
 *
 * Replaces the raw ``<pre>{JSON.stringify(context)}</pre>`` dump with
 * fact-path-specific UI: a draft email preview for ``approval.reply_draft``,
 * a "from → to" diff for ``approval.identity_drift_review``, a structured
 * change-request panel for ``approval.contract_change_request``, etc.
 *
 * Unknown fact paths fall back to a key/value table — still nicer than a
 * raw JSON blob — so a future skill that adds a new approval type still
 * gets a readable view without code changes here.
 */

type Ctx = Record<string, unknown>;

function isObj(v: unknown): v is Ctx {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function clipText(s: string, max = 4000): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max)}…`;
}

function asString(v: unknown): string | null {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return null;
}

function PillRow({ items }: { items: Array<[string, string | null | undefined]> }) {
  const filled = items.filter(([, v]) => v != null && v !== '');
  if (filled.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-[11px]">
      {filled.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5">
          <span className="text-slate-500">{k}:</span>
          <span className="font-medium text-slate-800">{v}</span>
        </span>
      ))}
    </div>
  );
}

function KeyValueTable({ ctx, skip = [] }: { ctx: Ctx; skip?: string[] }) {
  const entries = Object.entries(ctx).filter(
    ([k, v]) => !skip.includes(k) && v !== null && v !== undefined && v !== '',
  );
  if (entries.length === 0) return null;
  return (
    <table className="w-full table-fixed border-collapse text-xs">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-b border-slate-100 last:border-b-0">
            <td className="w-44 py-1 pr-2 align-top font-mono text-[11px] text-slate-500">
              {k}
            </td>
            <td className="py-1 align-top text-slate-800">
              {renderValue(v)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function renderValue(v: unknown): React.ReactNode {
  if (v === null || v === undefined) return <span className="italic text-slate-400">—</span>;
  if (typeof v === 'string') {
    if (v.length > 120) {
      return <pre className="whitespace-pre-wrap break-words font-sans">{clipText(v, 1200)}</pre>;
    }
    return <span>{v}</span>;
  }
  if (typeof v === 'number' || typeof v === 'boolean') {
    return <span className="font-mono">{String(v)}</span>;
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="italic text-slate-400">[]</span>;
    return (
      <ul className="list-inside list-disc space-y-0.5">
        {v.map((item, i) => (
          <li key={i}>{renderValue(item)}</li>
        ))}
      </ul>
    );
  }
  if (isObj(v)) {
    return (
      <details className="cursor-pointer">
        <summary className="text-[11px] text-slate-500 hover:text-slate-800">
          object · {Object.keys(v).length} keys
        </summary>
        <div className="mt-1 rounded border border-slate-200 bg-slate-50 p-2">
          <KeyValueTable ctx={v} />
        </div>
      </details>
    );
  }
  return <span className="font-mono text-[11px]">{String(v)}</span>;
}

function AddressBlock({ value, label }: { value: unknown; label: string }) {
  if (!isObj(value)) {
    return (
      <div>
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
        <div className="italic text-slate-400">—</div>
      </div>
    );
  }
  const fields: Array<[string, string[]]> = [
    ['Full Name', ['full_name', 'name']],
    ['Street', ['street', 'street_1', 'address_line_1']],
    ['City', ['city']],
    ['State', ['state', 'region']],
    ['Zip', ['zip', 'postal_code', 'zip_code', 'postcode']],
    ['Country', ['country']],
    ['Email', ['email']],
    ['Phone', ['phone', 'phone_number']],
  ];
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <ul className="space-y-0.5 text-xs">
        {fields.map(([disp, keys]) => {
          let v: string | null = null;
          for (const k of keys) {
            const candidate = value[k];
            if (typeof candidate === 'string' && candidate.trim()) {
              v = candidate.trim();
              break;
            }
          }
          if (!v) return null;
          return (
            <li key={disp}>
              <span className="text-slate-500">{disp}:</span>{' '}
              <span className="text-slate-800">{v}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ReplyDraftView({ ctx }: { ctx: Ctx }) {
  const draft = isObj(ctx.draft) ? ctx.draft : null;
  const childSkill = asString(ctx.child_skill);
  const primaryGoal = asString(ctx.primary_goal);
  const primaryLane = asString(ctx.primary_lane);
  const sourceMessageId = asString(ctx.source_message_id);
  const decision = asString(ctx.decision);
  if (!draft) {
    return (
      <div className="text-xs italic text-slate-500">
        (此 approval.reply_draft 没有 draft 对象)
      </div>
    );
  }
  const to = asString(draft.to);
  const subject = asString(draft.subject);
  const body = asString(draft.body) ?? '';
  const attachments = Array.isArray(draft.attachments) ? draft.attachments : [];
  return (
    <div className="space-y-2">
      <PillRow
        items={[
          ['skill', childSkill],
          ['goal', primaryGoal],
          ['lane', primaryLane],
          ['decision', decision],
        ]}
      />
      <div className="rounded border border-emerald-200 bg-white">
        <div className="border-b border-emerald-100 bg-emerald-50/60 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-baseline gap-1">
            <span className="font-medium text-emerald-800">To:</span>
            <span className="font-mono">{to || <em className="text-rose-700">(missing — bridge will fill from inbound)</em>}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-baseline gap-1">
            <span className="font-medium text-emerald-800">Subject:</span>
            <span className="font-medium text-slate-800">
              {subject || <em className="text-slate-500">(no subject — bridge will derive Re: …)</em>}
            </span>
          </div>
          {attachments.length > 0 && (
            <div className="mt-0.5 flex flex-wrap items-baseline gap-1">
              <span className="font-medium text-emerald-800">Attachments:</span>
              <span className="text-slate-700">
                {attachments.map((a, i) => (
                  <span key={i} className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 font-mono text-[11px]">
                    {String(a).split('/').pop()}
                  </span>
                ))}
              </span>
            </div>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto px-3 py-2">
          <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-slate-800">
            {body || <em className="text-slate-400">(空白草稿)</em>}
          </pre>
        </div>
      </div>
      {sourceMessageId && (
        <div className="text-[10px] text-slate-400">
          回复自 msg-id: <span className="font-mono">{sourceMessageId}</span>
        </div>
      )}
    </div>
  );
}

function CompensationCapView({ ctx }: { ctx: Ctx }) {
  return (
    <div className="space-y-2 text-xs">
      <PillRow
        items={[
          ['requested', asString(ctx.requested_amount) ?? asString(ctx.kol_quote)],
          ['ceiling', asString(ctx.current_ceiling) ?? asString(ctx.paid_ceiling)],
          ['delta', asString(ctx.delta)],
          ['mode', asString(ctx.compensation_mode)],
        ]}
      />
      <KeyValueTable
        ctx={ctx}
        skip={[
          'requested_amount', 'kol_quote', 'current_ceiling', 'paid_ceiling',
          'delta', 'compensation_mode', 'opened_by', 'source',
          'linked_escalation_id', 'escalation_id',
        ]}
      />
    </div>
  );
}

function ContractChangeRequestView({ ctx }: { ctx: Ctx }) {
  const kind = asString(ctx.kind);
  const severity = asString(ctx.severity);
  const excerpt = asString(ctx.excerpt) ?? asString(ctx.detail);
  return (
    <div className="space-y-2 text-xs">
      <PillRow items={[['kind', kind], ['severity', severity]]} />
      {excerpt && (
        <blockquote className="border-l-2 border-amber-300 bg-amber-50/60 px-3 py-2 text-slate-800">
          {excerpt}
        </blockquote>
      )}
      <KeyValueTable
        ctx={ctx}
        skip={[
          'kind', 'severity', 'excerpt', 'detail',
          'opened_by', 'source', 'linked_escalation_id', 'escalation_id',
        ]}
      />
    </div>
  );
}

function IdentityDriftView({ ctx }: { ctx: Ctx }) {
  const oldAddr = ctx.old ?? ctx.old_address ?? ctx.previous;
  const newAddr = ctx.new ?? ctx.new_address ?? ctx.proposed;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded border border-slate-200 bg-white p-2">
          <AddressBlock value={oldAddr} label="previous address" />
        </div>
        <div className="rounded border border-emerald-200 bg-emerald-50/40 p-2">
          <AddressBlock value={newAddr} label="proposed new address" />
        </div>
      </div>
      <KeyValueTable
        ctx={ctx}
        skip={[
          'old', 'old_address', 'previous', 'new', 'new_address', 'proposed',
          'opened_by', 'source', 'linked_escalation_id', 'escalation_id',
        ]}
      />
    </div>
  );
}

function LogisticsAnomalyView({ ctx }: { ctx: Ctx }) {
  return (
    <div className="space-y-2 text-xs">
      <PillRow
        items={[
          ['carrier', asString(ctx.carrier) ?? asString(ctx.tracking_carrier)],
          ['tracking', asString(ctx.tracking_no)],
          ['status', asString(ctx.shipment_status) ?? asString(ctx.status)],
        ]}
      />
      <KeyValueTable
        ctx={ctx}
        skip={[
          'carrier', 'tracking_carrier', 'tracking_no', 'shipment_status', 'status',
          'opened_by', 'source', 'linked_escalation_id', 'escalation_id',
        ]}
      />
    </div>
  );
}

function GenericApprovalView({ ctx }: { ctx: Ctx }) {
  return (
    <KeyValueTable
      ctx={ctx}
      skip={['opened_by', 'source', 'linked_escalation_id', 'escalation_id']}
    />
  );
}

export default function ApprovalContextCard({
  factPath,
  context,
  identityId,
  campaignId,
  env,
}: {
  factPath: string;
  context: Ctx | null;
  identityId: number;
  campaignId: string;
  env: string;
}) {
  if (!context) {
    return <div className="text-xs italic text-slate-500">(no context)</div>;
  }
  const isReplyDraft = factPath === 'approval.reply_draft';

  // For reply drafts, pull the inbound that the agent is responding to so
  // the operator can compare the draft to the original message side-by-side.
  const [inbound, setInbound] = useState<InboundEmail | null>(null);
  const [loadedInbound, setLoadedInbound] = useState(false);
  useEffect(() => {
    if (!isReplyDraft) {
      setLoadedInbound(true);
      return;
    }
    let alive = true;
    const sourceMessageId = asString(context.source_message_id);
    const params = new URLSearchParams({
      identity_id: String(identityId),
      campaign_id: campaignId,
      env,
    });
    if (sourceMessageId) params.set('message_id', sourceMessageId);
    api
      .get<{ inbound: InboundEmail | null }>(`/approvals/inbound-context?${params}`)
      .then((r) => {
        if (!alive) return;
        setInbound(r.inbound ?? null);
      })
      .catch(() => {})
      .finally(() => alive && setLoadedInbound(true));
    return () => {
      alive = false;
    };
  // We intentionally key on the factPath + ids; the context object is
  // immutable per approval row, so referencing context.source_message_id
  // here would just churn re-fetches.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [factPath, identityId, campaignId, env]);

  let body: React.ReactNode;
  switch (factPath) {
    case 'approval.reply_draft':
      body = <ReplyDraftView ctx={context} />;
      break;
    case 'approval.paid_ceiling_override':
    case 'approval.over_budget_request':
    case 'approval.compensation_cap_breach':
      body = <CompensationCapView ctx={context} />;
      break;
    case 'approval.contract_change_request':
      body = <ContractChangeRequestView ctx={context} />;
      break;
    case 'approval.identity_drift_review':
      body = <IdentityDriftView ctx={context} />;
      break;
    case 'approval.logistics_anomaly':
      body = <LogisticsAnomalyView ctx={context} />;
      break;
    default:
      body = <GenericApprovalView ctx={context} />;
  }

  return (
    <div className="space-y-2">
      {isReplyDraft && loadedInbound && (
        <InboundEmailCard
          inbound={inbound}
          title="对方刚发来的邮件（agent 正在回复这条）"
          variant="sky"
        />
      )}
      {body}
    </div>
  );
}
