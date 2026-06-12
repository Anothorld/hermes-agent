import { Link } from 'react-router-dom';
import { campaignIdQueryFirst, isRealCampaignId } from '../lib/campaignId';

export type EscalationCompletion = {
  status: 'draft_approved' | 'escalation_closed' | string;
  message: string;
  gmail_draft_id?: string | null;
  gmail_thread_id?: string | null;
  linked_escalation_id?: number | null;
};

export function EscalationCompletionPanel({
  completion,
  identityId,
  campaignId,
  handle,
}: {
  completion: EscalationCompletion;
  identityId: number;
  campaignId?: string | null;
  handle?: string | null;
}) {
  const isApproved = completion.status === 'draft_approved';
  return (
    <section
      className={
        'rounded border p-4 text-sm ' +
        (isApproved
          ? 'border-emerald-300 bg-emerald-50 text-emerald-950'
          : 'border-slate-300 bg-slate-50 text-slate-800')
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">
          {isApproved ? '✓ 本升级已处理完成' : '升级已关闭'}
        </h2>
        {isApproved && (
          <span className="rounded bg-emerald-200 px-2 py-0.5 text-[11px] font-medium">
            全部步骤完成
          </span>
        )}
      </div>
      <p className="mt-2 text-sm leading-relaxed">{completion.message}</p>
      {completion.gmail_draft_id && (
        <p className="mt-2 text-xs text-emerald-900">
          Gmail 草稿 ID：<code className="rounded bg-white px-1 py-0.5">{completion.gmail_draft_id}</code>
          {completion.gmail_thread_id && (
            <span className="ml-2 text-emerald-800">
              线程：<code className="rounded bg-white px-1 py-0.5">{completion.gmail_thread_id}</code>
            </span>
          )}
        </p>
      )}
      <ul className="mt-3 list-disc space-y-1 pl-4 text-xs leading-relaxed">
        {isApproved && (
          <>
            <li>打开绑定的 Gmail 账号，在「草稿」中找到此回信，核对后发送。</li>
            <li>无需返回待审批页；本页状态会自动更新。</li>
          </>
        )}
        {isRealCampaignId(campaignId) && (
          <li>
            查看 KOL 进展：{' '}
            <Link
              to={`/kols/${identityId}${campaignIdQueryFirst(campaignId)}`}
              className="font-medium text-sky-800 hover:underline"
            >
              {handle ? `@${handle}` : `KOL #${identityId}`}
            </Link>
          </li>
        )}
        <li>
          <Link to="/escalations" className="font-medium text-sky-800 hover:underline">
            返回升级队列
          </Link>
          {' '}处理下一单。
        </li>
      </ul>
    </section>
  );
}
