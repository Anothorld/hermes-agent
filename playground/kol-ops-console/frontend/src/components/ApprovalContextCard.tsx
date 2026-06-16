import { useEffect, useState } from 'react';
import { api } from '../api';
import { goalLabel, laneLabel, policyScopeLabel } from '../constants/domainLabels';
import ConversationSummaryCard, {
  parseConversationSummaryBullets,
} from './ConversationSummaryCard';
import InboundEmailCard, { type InboundEmail } from './InboundEmailCard';
import ContractAttachmentPreview, {
  attachmentDisplayName,
  hasDocxAttachments,
} from './ContractAttachmentPreview';
import { PolicyMergeDiffPreview } from './PolicyMergeDiffPreview';

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

type ContributingSkill = {
  lane?: string;
  goal?: string;
  skill?: string;
};

function ContributingSkillsChips({ items }: { items: ContributingSkill[] }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium text-slate-600">
        合成主题 ({items.length})
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((row, i) => {
          const lane = row.lane ?? '?';
          const goal = row.goal ?? '?';
          const skill = row.skill ?? '?';
          const label = `${laneLabel(lane)} · ${goalLabel(goal)}`;
          return (
            <span
              key={`${lane}-${goal}-${skill}-${i}`}
              title={`${lane} · ${goal} · ${skill}`}
              className="inline-flex max-w-full flex-col rounded border border-violet-200 bg-violet-50 px-2 py-1 text-[10px] leading-tight text-violet-900"
            >
              <span className="font-medium">{label}</span>
              <span className="truncate font-mono text-violet-700">{skill}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function parseContributingSkills(ctx: Ctx): ContributingSkill[] {
  const raw = ctx.contributing_skills;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is ContributingSkill => isObj(item));
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

/**
 * Render an HTML email body produced by kol-cold-outreach /
 * kol-reengagement-outreach. The skills are constrained to a tiny tag
 * set (<p>, <br>, <a>, <strong>, <em>) so we hand-parse instead of
 * pulling in DOMPurify / dangerouslySetInnerHTML. Anything we don't
 * recognise renders as plain text.
 *
 * Anchor hrefs are accepted only when they start with http:// or
 * https:// — keeps `javascript:` and `data:` URIs out of the operator
 * preview even if a future skill regression produces them.
 */
function HtmlBodyView({ body }: { body: string }) {
  type Node =
    | { kind: 'text'; value: string }
    | { kind: 'br' }
    | { kind: 'anchor'; href: string; text: string }
    | { kind: 'strong'; text: string }
    | { kind: 'em'; text: string };

  const parseInline = (chunk: string): Node[] => {
    const out: Node[] = [];
    const re = /<(a)\s+href=(?:"([^"]*)"|'([^']*)')[^>]*>([\s\S]*?)<\/a>|<br\s*\/?\s*>|<(strong|em)\s*>([\s\S]*?)<\/\5>/gi;
    let cursor = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(chunk)) !== null) {
      if (m.index > cursor) {
        out.push({ kind: 'text', value: chunk.slice(cursor, m.index) });
      }
      if (m[1]?.toLowerCase() === 'a') {
        const href = m[2] ?? m[3] ?? '';
        const safe = /^https?:\/\//i.test(href) ? href : '';
        out.push({ kind: 'anchor', href: safe, text: stripTags(m[4] ?? '') });
      } else if (m[5]?.toLowerCase() === 'strong') {
        out.push({ kind: 'strong', text: stripTags(m[6] ?? '') });
      } else if (m[5]?.toLowerCase() === 'em') {
        out.push({ kind: 'em', text: stripTags(m[6] ?? '') });
      } else {
        out.push({ kind: 'br' });
      }
      cursor = m.index + m[0].length;
    }
    if (cursor < chunk.length) {
      out.push({ kind: 'text', value: chunk.slice(cursor) });
    }
    return out;
  };

  const stripTags = (s: string) => s.replace(/<[^>]+>/g, '');

  // Split into <p> blocks; treat content outside any <p> as a single block too
  // so a body that uses only <br> still renders.
  const blocks: string[] = [];
  const pRe = /<p\s*>([\s\S]*?)<\/p>/gi;
  let lastIdx = 0;
  let pm: RegExpExecArray | null;
  while ((pm = pRe.exec(body)) !== null) {
    if (pm.index > lastIdx) {
      const tail = body.slice(lastIdx, pm.index).trim();
      if (tail) blocks.push(tail);
    }
    blocks.push(pm[1]);
    lastIdx = pm.index + pm[0].length;
  }
  if (lastIdx < body.length) {
    const tail = body.slice(lastIdx).trim();
    if (tail) blocks.push(tail);
  }
  if (blocks.length === 0) blocks.push(body);

  return (
    <div className="space-y-2 font-sans text-[12.5px] leading-relaxed text-slate-800">
      {blocks.map((block, bi) => (
        <p key={bi} className="whitespace-pre-wrap break-words">
          {parseInline(block).map((node, ni) => {
            switch (node.kind) {
              case 'text':
                return <span key={ni}>{node.value}</span>;
              case 'br':
                return <br key={ni} />;
              case 'anchor':
                return node.href ? (
                  <a
                    key={ni}
                    href={node.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-emerald-700 underline hover:text-emerald-900"
                  >
                    {node.text}
                  </a>
                ) : (
                  <span key={ni}>{node.text}</span>
                );
              case 'strong':
                return <strong key={ni}>{node.text}</strong>;
              case 'em':
                return <em key={ni}>{node.text}</em>;
              default:
                return null;
            }
          })}
        </p>
      ))}
    </div>
  );
}

function ReplyDraftView({
  ctx,
  identityId,
  campaignId,
  env,
}: {
  ctx: Ctx;
  identityId: number;
  campaignId: string;
  env: string;
}) {
  const draft = isObj(ctx.draft) ? ctx.draft : null;
  const childSkill = asString(ctx.child_skill);
  const primaryGoal = asString(ctx.primary_goal);
  const primaryLane = asString(ctx.primary_lane);
  const sourceMessageId = asString(ctx.source_message_id);
  const decision = asString(ctx.decision);
  const contributing = parseContributingSkills(ctx);
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
  const isHtml = draft.html === true || /<\s*a\s+href=|<\s*p\s*>|<\s*br\s*\/?\s*>/i.test(body);
  const attachments = Array.isArray(draft.attachments)
    ? draft.attachments.filter((a): a is string => typeof a === 'string')
    : [];
  const showContractPreview =
    primaryGoal === 'contract_signing'
    || hasDocxAttachments(attachments);
  const chaseSupersede = isObj(ctx.chase_supersede) ? ctx.chase_supersede : null;
  const priorChaseMsg = chaseSupersede ? asString(chaseSupersede.prior_source_message_id) : null;
  const orphanDiscard = chaseSupersede && isObj(chaseSupersede.orphan_gmail_discard)
    ? chaseSupersede.orphan_gmail_discard
    : null;
  const orphanDiscardAction = orphanDiscard ? asString(orphanDiscard.action) : null;
  return (
    <div className="space-y-2">
      {priorChaseMsg && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          本条为<strong className="font-medium">追信换新稿</strong>（已替换上一版回复，原来信
          msg-id: <span className="font-mono">{priorChaseMsg}</span>）。请确认正文已回应对方最新跟进后再批准。
          {orphanDiscardAction === 'deleted' && (
            <span className="mt-1 block text-amber-800">
              上一版 Gmail 草稿已自动删除，请勿在 Gmail 草稿箱里找旧稿发送。
            </span>
          )}
          {orphanDiscardAction === 'failed' && (
            <span className="mt-1 block text-red-800">
              未能自动删除上一版 Gmail 草稿，请在 Gmail 草稿箱手动删除后再批准。
            </span>
          )}
        </div>
      )}
      <PillRow
        items={[
          ['子技能', childSkill],
          ['阶段', primaryGoal ? goalLabel(primaryGoal) : null],
          ['泳道', primaryLane ? laneLabel(primaryLane) : null],
          ['路由', decision],
        ]}
      />
      {contributing.length > 0 && <ContributingSkillsChips items={contributing} />}
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
                    {attachmentDisplayName(a)}
                  </span>
                ))}
              </span>
            </div>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto px-3 py-2">
          {body ? (
            isHtml ? (
              <HtmlBodyView body={body} />
            ) : (
              <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-slate-800">
                {body}
              </pre>
            )
          ) : (
            <em className="text-slate-400">(空白草稿)</em>
          )}
        </div>
      </div>
      {showContractPreview && (
        <ContractAttachmentPreview
          identityId={identityId}
          campaignId={campaignId}
          env={env}
          attachmentPath={attachments[0]}
        />
      )}
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

// Shows the CURRENT approved policy so the operator can see what the proposed
// delta will refine before approving (Stage C diff preview).
function CurrentPolicyPreview({ scope, env }: { scope: string; env: string }) {
  const [md, setMd] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .get<{ policy: { content_md?: string } | null }>(
        `/learning/policies/${scope}?env=${env}`,
      )
      .then((r) => {
        if (alive) setMd(r.policy?.content_md ?? '');
      })
      .catch(() => {
        if (alive) setMd('');
      });
    return () => {
      alive = false;
    };
  }, [scope, env]);
  return (
    <div className="rounded border border-slate-200 bg-slate-50/60 p-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[11px] font-medium text-slate-600 hover:text-slate-900"
      >
        {open ? '收起' : '查看'}当前 {policyScopeLabel(scope)}（批准后在此基础上调整）
      </button>
      {open && (
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 text-[11px] text-slate-700">
          {md == null ? '加载中…' : md.trim() || '(当前为空，批准后将首次写入)'}
        </pre>
      )}
    </div>
  );
}

function StyleLearningProposalView({ ctx, env }: { ctx: Ctx; env: string }) {
  const md = asString(ctx.proposed_markdown) ?? '';
  const styleMd = asString(ctx.proposed_style_markdown) ?? '';
  const strategyMd = asString(ctx.proposed_strategy_markdown) ?? '';
  const scope = asString(ctx.scope) ?? 'company_style';
  const sampleCount = ctx.sample_count;
  const batchThreshold = ctx.batch_threshold;
  const llmUsed = ctx.llm_used === true;
  const eventIds = Array.isArray(ctx.source_event_ids) ? ctx.source_event_ids : [];
  const kolCount = ctx.sample_identity_count;
  const campaignCount = ctx.sample_campaign_count;
  const operatorIds = Array.isArray(ctx.sample_operator_ids)
    ? ctx.sample_operator_ids
    : [];
  const ownerUserId = ctx.owner_user_id;
  return (
    <div className="space-y-2 text-xs">
      <div className="rounded border border-violet-200 bg-violet-50 px-2 py-1 text-violet-900">
        <div className="font-medium">编辑学习提案（批准后写入 policy，供 AI 回信参考）</div>
        <div className="mt-0.5 text-[11px]">
          本批为<strong>跨多位 KOL</strong>的编辑汇总，不是单一 KOL 的审批；列表上的 @handle 仅为系统挂载用，可忽略。
        </div>
        <div className="mt-0.5 text-[11px]">
          范围：{policyScopeLabel(scope)}
          {typeof kolCount === 'number' && kolCount > 0 && (
            <> · 涉及 {String(kolCount)} 位 KOL</>
          )}
          {typeof campaignCount === 'number' && campaignCount > 0 && (
            <> · {String(campaignCount)} 个 campaign</>
          )}
          {sampleCount != null && <> · 编辑样本 {String(sampleCount)} 条</>}
          {scope === 'user_style' && ownerUserId != null && (
            <> · 操作员 #{String(ownerUserId)}</>
          )}
          {scope !== 'user_style' && operatorIds.length > 0 && (
            <> · 来自 {operatorIds.length} 位操作员</>
          )}
          {batchThreshold != null && <> · 批次阈值 {String(batchThreshold)}</>}
          {llmUsed ? ' · LLM 蒸馏' : ' · 非 LLM 提案（历史数据；当前版本已禁止规则回退）'}
          {eventIds.length > 0 && <> · 来源事件 {eventIds.length} 条</>}
        </div>
      </div>
      <div className="rounded border border-slate-100 bg-white px-2 py-1 text-[11px] text-slate-600">
        提案为<strong>增量修订（delta）</strong>：批准后默认由 <strong>LLM 智能合并</strong>
        进现有 policy（去重、处理 <code className="mx-0.5">ADJUST:</code>/
        <code className="mx-0.5">REMOVE:</code>）；失败时自动回退确定性 patch。
        <strong>Context notes</strong> 仅供审批参考，不会写入 policy；无实质规则则跳过写入。
        下方预览为 patch 近似，批准结果以 LLM 合并为准。
      </div>
      <PolicyMergeDiffPreview env={env} proposal={ctx} />
      {strategyMd ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-slate-700">
            策略段落（批准后 → {policyScopeLabel('reply_strategy')}）
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-amber-200 bg-amber-50/50 p-2 text-[11px] text-slate-800">
            {strategyMd}
          </pre>
          <CurrentPolicyPreview scope="reply_strategy" env={env} />
        </div>
      ) : null}
      {styleMd ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-slate-700">
            邮件风格（批准后 → {policyScopeLabel(scope)}）
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 text-[11px] text-slate-800">
            {styleMd}
          </pre>
          <CurrentPolicyPreview scope={scope} env={env} />
        </div>
      ) : md ? (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 text-[11px] text-slate-800">
          {md}
        </pre>
      ) : (
        <div className="italic text-slate-500">(无 proposed_markdown)</div>
      )}
    </div>
  );
}

function OutcomeLearningProposalView({ ctx, env }: { ctx: Ctx; env: string }) {
  const md = asString(ctx.proposed_markdown) ?? '';
  const sampleCount = ctx.sample_count;
  const failureCount = ctx.failure_count;
  const segment = asString(ctx.segment);
  const llmUsed = ctx.llm_used === true;
  const eventIds = Array.isArray(ctx.source_event_ids) ? ctx.source_event_ids : [];
  return (
    <div className="space-y-2 text-xs">
      <div className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-emerald-900">
        <div className="font-medium">合作复盘提案（批准后写入回信流程的「结局指导」policy）</div>
        <div className="mt-0.5 text-[11px]">
          汇总多次合作的成/败<strong>根因</strong>，指导后续外联/谈判；批准后并入
          {' '}{policyScopeLabel('outcome_strategy')}（按 goal 注入 AI 参考）。
        </div>
        <div className="mt-0.5 text-[11px]">
          {segment && <>阶段/segment：{goalLabel(segment) !== segment ? goalLabel(segment) : segment} · </>}
          {sampleCount != null && <>复盘样本 {String(sampleCount)} 次</>}
          {failureCount != null && <> · 其中失败 {String(failureCount)} 次</>}
          {llmUsed ? ' · LLM 综合' : ' · 规则聚合（未配置 Hermes/LLM 凭据）'}
          {eventIds.length > 0 && <> · 来源复盘 {eventIds.length} 条</>}
        </div>
      </div>
      <PolicyMergeDiffPreview env={env} proposal={ctx} />
      {md ? (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 text-[11px] text-slate-800">
          {md}
        </pre>
      ) : (
        <div className="italic text-slate-500">(无 proposed_markdown)</div>
      )}
      <CurrentPolicyPreview scope="outcome_strategy" env={env} />
    </div>
  );
}

function DiscoveryLearningProposalView({ ctx, env }: { ctx: Ctx; env: string }) {
  const md = asString(ctx.proposed_markdown) ?? '';
  const groupKind = asString(ctx.group_kind) ?? '';
  const groupKey = asString(ctx.group_key) ?? '';
  const scope = asString(ctx.scope) ?? '';
  const sampleCount = ctx.sample_count;
  const kolCount = ctx.sample_identity_count;
  const batchThreshold = ctx.batch_threshold;
  const llmUsed = ctx.llm_used === true;
  const actionMix =
    typeof ctx.action_mix === 'object' && ctx.action_mix !== null
      ? (ctx.action_mix as Record<string, number>)
      : {};
  const eventIds = Array.isArray(ctx.source_event_ids) ? ctx.source_event_ids : [];
  const ACTION_ZH: Record<string, string> = {
    approve: '批准',
    remove: '移除',
    transfer: '转移',
  };
  const mixLabel = Object.entries(actionMix)
    .filter(([, n]) => typeof n === 'number' && n > 0)
    .map(([a, n]) => `${ACTION_ZH[a] ?? a} ${n}`)
    .join(' / ');
  return (
    <div className="space-y-2 text-xs">
      <div className="rounded border border-indigo-200 bg-indigo-50 px-2 py-1 text-indigo-900">
        <div className="font-medium">
          KOL 发现标准提案（批准后用于下一轮 KOL 发现）
        </div>
        <div className="mt-0.5 text-[11px]">
          根据您在 shortlist 上的批准/移除/转移理由汇总而成；批准后 AI 会按这些标准
          挑选更符合预期的 KOL。列表上的 @handle 仅为系统挂载用，可忽略。
        </div>
        <div className="mt-0.5 text-[11px]">
          学习层级：{groupKind === 'category' ? `品类 ${groupKey}` : `产品 ${groupKey}`}
          {typeof kolCount === 'number' && kolCount > 0 && (
            <> · 涉及 {String(kolCount)} 位 KOL</>
          )}
          {sampleCount != null && <> · 决策样本 {String(sampleCount)} 条</>}
          {mixLabel && <> · {mixLabel}</>}
          {batchThreshold != null && <> · 批次阈值 {String(batchThreshold)}</>}
          {llmUsed ? ' · LLM 蒸馏' : ''}
          {eventIds.length > 0 && <> · 来源事件 {eventIds.length} 条</>}
        </div>
      </div>
      <div className="rounded border border-slate-100 bg-white px-2 py-1 text-[11px] text-slate-600">
        提案为<strong>增量修订（delta）</strong>：批准后并入该
        {groupKind === 'category' ? '品类' : '产品'}的发现标准，可能含
        <code className="mx-0.5">ADJUST:</code>/<code className="mx-0.5">REMOVE:</code>
        指令。展开下方可对比当前标准。提案只调整评分侧重与软性排除信号，
        <strong>不会放宽硬性门槛</strong>（粉丝数 / 地区 / 互动率等）。
      </div>
      <PolicyMergeDiffPreview env={env} proposal={ctx} />
      {md ? (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 text-[11px] text-slate-800">
          {md}
        </pre>
      ) : (
        <div className="italic text-slate-500">(无 proposed_markdown)</div>
      )}
      {scope && <CurrentPolicyPreview scope={scope} env={env} />}
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
  replyDraftKind,
}: {
  factPath: string;
  context: Ctx | null;
  identityId: number;
  campaignId: string;
  env: string;
  /** When set, conversation summary is shown only for inbound replies. */
  replyDraftKind?: 'initial_outreach' | 'inbound_reply' | null;
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
      body = (
        <ReplyDraftView
          ctx={context}
          identityId={identityId}
          campaignId={campaignId}
          env={env}
        />
      );
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
    case 'approval.style_learning_proposal':
      body = <StyleLearningProposalView ctx={context} env={env} />;
      break;
    case 'approval.outcome_learning_proposal':
      body = <OutcomeLearningProposalView ctx={context} env={env} />;
      break;
    case 'approval.discovery_learning_proposal':
      body = <DiscoveryLearningProposalView ctx={context} env={env} />;
      break;
    default:
      body = <GenericApprovalView ctx={context} />;
  }

  const showConversationSummary = isReplyDraft
    && (replyDraftKind == null || replyDraftKind === 'inbound_reply');
  const summaryBullets = showConversationSummary
    ? parseConversationSummaryBullets(context)
    : [];

  return (
    <div className="space-y-2">
      {showConversationSummary && summaryBullets.length > 0 && (
        <ConversationSummaryCard bullets={summaryBullets} />
      )}
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
