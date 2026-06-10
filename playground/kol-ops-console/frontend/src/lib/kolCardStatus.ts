import type { LaneSnapshot } from '../api';

export type CardStatusKey =
  | 'sent_waiting'
  | 'interested'
  | 'declined'
  | 'progressing'
  | 'blocked'
  | 'draft_pending_approval'
  | 'draft_pending_send'
  | 'idle';

type StatusRow = Pick<
  LaneSnapshot,
  'goals' | 'interest_signal' | 'outreach_sent_at' | 'reply_draft_state' | 'outreach_draft_created'
>;

export function cardStatus(row: StatusRow): CardStatusKey {
  const commerce = row.goals.commerce;
  if (commerce?.state === 'blocked') return 'blocked';
  const signal = (row.interest_signal || '').toLowerCase();
  if (signal === 'declined') return 'declined';
  if (signal === 'confirmed' || signal === 'interested') {
    if (commerce?.goal && commerce.goal !== 'interest_qualification') {
      return 'progressing';
    }
    return 'interested';
  }
  if (!row.outreach_sent_at) {
    if (row.reply_draft_state === 'sent') return 'sent_waiting';
    if (row.reply_draft_state === 'pending') return 'draft_pending_approval';
    if (row.reply_draft_state === 'approved_unsent' || row.outreach_draft_created) {
      return 'draft_pending_send';
    }
  }
  if (row.outreach_sent_at) return 'sent_waiting';
  return 'idle';
}

export const STATUS_BADGE: Record<CardStatusKey, { label: string; cls: string; title: string }> = {
  sent_waiting: {
    label: '等回复',
    cls: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200',
    title: '初邀已发出，尚未收到对方回信',
  },
  interested: {
    label: '已回复·意向',
    cls: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200',
    title: '对方回信，确认有意向',
  },
  progressing: {
    label: '推进中',
    cls: 'bg-sky-100 text-sky-800 ring-1 ring-sky-200',
    title: '已进入选品 / 报价 / 合同等后续阶段',
  },
  declined: {
    label: '已拒绝',
    cls: 'bg-rose-100 text-rose-800 ring-1 ring-rose-200',
    title: '对方明确拒绝',
  },
  blocked: {
    label: '阻塞',
    cls: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
    title: '存在未解决的升级，需要操作员介入',
  },
  draft_pending_approval: {
    label: 'Draft 待审批',
    cls: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200',
    title: '草稿在 Approvals 队列里等通过',
  },
  draft_pending_send: {
    label: 'Draft 待发送',
    cls: 'bg-sky-50 text-sky-700 ring-1 ring-sky-200',
    title: 'Gmail 草稿已就绪，需手动点 Send',
  },
  idle: {
    label: '待发起',
    cls: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200',
    title: '尚未发出初邀',
  },
};
