/**
 * Operator-facing labels for escalation rule_id and short reason codes.
 * Agent-generated English prose stays as secondary detail / tooltip.
 */

/** Matched escalation_rules policy rule_id → 中文类型名 */
export const ESCALATION_RULE_LABELS: Record<string, string> = {
  product_flexibility_requested: 'KOL 申请更换产品',
  package_lost: '包裹丢失',
  not_received: '未收到货',
  off_cap_price: '报价超上限',
  variant_swap: '换款/换色',
  variant_swap_and_scope_change: '换款且改交付范围',
  contact_email_not_found: '找不到联系邮箱',
  inbound_mailbox_mismatch: '收件邮箱不匹配',
  discovery_floor_unmet: '发现数量未达下限',
  campaign_config_incomplete: 'Campaign 配置不完整',
  fragment_fact_conflict: '多技能事实冲突',
  nox_auth_missing: 'Nox 授权缺失',
  nox_quota_exhausted: 'Nox 配额用尽',
  paid_over_ceiling: '报价超过预算上限',
  kol_demands_off_whitelist: 'KOL 要求白名单外条件',
  ambiguous_request: '诉求不明确',
  missing_test_mode_to_in_cal: '缺少测试收件邮箱配置',
  missing_public_email_for_initial_outreach: '缺少对外发信邮箱',
};

/** Short snake_case reason stored in DB → 中文（非 agent 长句） */
export const ESCALATION_REASON_CODE_LABELS: Record<string, string> = {
  discovery_floor_unmet: '发现数量未达下限',
  campaign_config_incomplete: 'Campaign 配置不完整',
  inbound_mailbox_mismatch: '收件邮箱与绑定邮箱不一致',
  contact_email_not_found: '未找到 KOL 联系邮箱',
  paid_over_ceiling: '报价超过预算上限',
  kol_demands_off_whitelist: 'KOL 要求超出白名单/政策',
  fragment_fact_conflict: '并行技能提议的事实互相冲突',
  draft_rejected: '草稿被驳回后需人工介入',
  max_auto_retries_exceeded: '自动重试次数已用尽',
};

const REASON_CODE_RE = /^[a-z][a-z0-9_]{2,80}$/;

export function isEscalationReasonCode(reason: string): boolean {
  return REASON_CODE_RE.test(reason.trim());
}

export function escalationRuleLabel(ruleId: string | null | undefined): string | null {
  if (!ruleId) return null;
  return ESCALATION_RULE_LABELS[ruleId] ?? null;
}

export function escalationReasonCodeLabel(reason: string): string | null {
  const key = reason.trim();
  if (!isEscalationReasonCode(key)) return null;
  return ESCALATION_REASON_CODE_LABELS[key] ?? key.replace(/_/g, ' ');
}

export type EscalationDisplaySummary = {
  /** Primary Chinese line for lists / headers */
  primary: string;
  /** Optional English or raw detail (agent prose) */
  secondary?: string;
  /** Full tooltip for advanced mode */
  title: string;
};

/**
 * Pick operator-facing summary: prefer rule_id label, then reason code,
 * else generic Chinese + English prose as secondary.
 */
export function escalationDisplaySummary(
  reason: string,
  ruleId?: string | null,
): EscalationDisplaySummary {
  const ruleZh = escalationRuleLabel(ruleId);
  const codeZh = escalationReasonCodeLabel(reason);
  const trimmed = reason.trim();

  if (ruleZh) {
    const secondary =
      trimmed && !isEscalationReasonCode(trimmed) && trimmed !== ruleId ? trimmed : undefined;
    return {
      primary: ruleZh,
      secondary,
      title: [ruleId, trimmed].filter(Boolean).join(' · '),
    };
  }
  if (codeZh) {
    return { primary: codeZh, title: trimmed };
  }
  if (/[\u4e00-\u9fff]/.test(trimmed)) {
    return { primary: trimmed, title: trimmed };
  }
  return {
    primary: '需人工处理',
    secondary: trimmed,
    title: trimmed,
  };
}

/** True when suggested_question looks like English agent prose (no CJK). */
export function isLikelyEnglishOperatorText(text: string | null | undefined): boolean {
  if (!text?.trim()) return false;
  return !/[\u4e00-\u9fff]/.test(text);
}
