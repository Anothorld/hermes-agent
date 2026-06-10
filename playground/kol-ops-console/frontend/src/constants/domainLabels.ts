/**
 * Operator-facing Chinese labels for bridge domain ids (goals, lanes,
 * learning jobs, policy scopes). Raw ids stay in tooltips for advanced mode.
 */

export const GOAL_LABELS: Record<string, string> = {
  outreach: '初邀触达',
  interest_qualification: '意向确认',
  product_selection: '选品',
  deliverables_scope: '交付范围',
  compensation_negotiation: '价格谈判',
  contract_signing: '合同签署',
  logistics: '物流',
  payout_setup: '收款方式',
  content_production: '内容制作',
  content_review_and_golive: '审核上线',
  post_collab_archival: '合作归档',
};

export const LANE_LABELS: Record<string, string> = {
  commerce: '商务',
  fulfillment: '履约',
  publish: '发布',
  meta: '元数据',
};

export const SUITE_LABELS: Record<string, string> = {
  capture: '采集',
  distill: '蒸馏',
  pricing: '定价校准',
  audit: '审计',
  quality: '质量评测',
  nightly: '夜间全套',
  all: '全部任务',
};

/** One-line operator hint for the selected cron suite (Learning page). */
export const SUITE_OPERATOR_HINTS: Record<string, string> = {
  capture: '对齐 Gmail 已发终稿、补录「编辑后发送」记录（不生成学习提案）。',
  distill: '驳回原因写入策略；编辑/复盘/发现决策样本够数时，各自生成待审批学习提案。',
  pricing: '根据历史谈判数据校准定价策略，并可能更新 campaign 出价比例。',
  audit: '记录人工改事实的错题、同步到分类器参考文档。',
  quality: '跑分类器金标评测（检查分类是否退化）。',
  nightly: '夜间常用：蒸馏（含发现标准 + 标签挖掘）+ 定价 + 审计 + 评测（不含 Gmail 采集）。',
  all: '执行全部任务（含 Gmail 采集）；耗时长，建议先预览。',
};

export const JOB_LABELS: Record<string, string> = {
  reconcile_sent: 'Gmail 终稿对齐',
  apply_reject_policy: '驳回策略蒸馏',
  apply_edit_policy: '编辑风格蒸馏',
  apply_edit_user_style: '个人风格蒸馏',
  apply_discovery_policy: 'KOL 发现标准蒸馏',
  mine_discovery_tags: '决策标签挖掘',
  apply_pricing_calibration_policy: '定价策略校准',
  auto_pricing_campaigns: 'LIVE 定价自动推广',
  snapshot_fact_corrections: '事实纠错快照',
  sync_failure_examples: '分类器错题回流',
  classifier_eval_deterministic: '分类器金标评测',
  promote_strategy: '策略升格技能参考',
};

export const JOB_STATUS_LABELS: Record<string, string> = {
  ok: '成功',
  skipped: '跳过',
  error: '失败',
  running: '运行中',
};

export const POLICY_SCOPE_LABELS: Record<string, string> = {
  reply_strategy: '回信策略',
  reply_learning: '驳回学习',
  company_style: '公司邮件风格',
  user_style: '个人邮件风格',
  escalation_rules: '异常处理规则',
  pricing_calibration: '定价校准',
  outcome_strategy: '合作结局指导',
};

/** Goals eligible for strategy promote in Console. */
export const PROMOTABLE_GOALS = [
  'compensation_negotiation',
  'interest_qualification',
  'deliverables_scope',
  'product_selection',
] as const;

export function goalLabel(goal: string | null | undefined): string {
  if (!goal) return '—';
  return GOAL_LABELS[goal] ?? goal.replace(/_/g, ' ');
}

export function goalLabelWithEnglish(goal: string): string {
  const zh = goalLabel(goal);
  return zh === goal ? goal : `${zh} (${goal})`;
}

export function laneLabel(lane: string | null | undefined): string {
  if (!lane) return '—';
  return LANE_LABELS[lane] ?? lane;
}

export function suiteLabel(suite: string): string {
  return SUITE_LABELS[suite] ?? suite;
}

export function suiteOptionLabel(suite: string): string {
  const zh = suiteLabel(suite);
  return zh === suite ? suite : `${zh}（${suite}）`;
}

export function jobLabel(job: string): string {
  return JOB_LABELS[job] ?? job;
}

export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABELS[status] ?? status;
}

export function policyScopeLabel(scope: string): string {
  // Dynamic learned-discovery-criteria scopes: discovery_criteria:spu:<sku>
  // / discovery_criteria:category:<slug>.
  if (scope.startsWith('discovery_criteria:')) {
    const [, kind, ...rest] = scope.split(':');
    const key = rest.join(':');
    return kind === 'category' ? `发现标准（品类 ${key}）` : `发现标准（产品 ${key}）`;
  }
  return POLICY_SCOPE_LABELS[scope] ?? scope;
}

/**
 * Humanize Bridge promote eligibility reason strings (may include counts).
 */
export function promoteReasonLabel(reason: string): string {
  if (!reason) return '—';
  if (reason === 'eligible') return '可升格';
  if (reason === 'no_strategy_section') return '暂无策略段落';
  if (reason.startsWith('below_min_approvals')) {
    const m = reason.match(/\((\d+)<(\d+)\)/);
    if (m) return `批准次数不足（当前 ${m[1]}，需 ≥ ${m[2]}）`;
    return '批准次数不足';
  }
  if (reason.startsWith('below_min_age_days')) {
    const m = reason.match(/\((\d+)<(\d+)\)/);
    if (m) return `稳定天数不足（当前 ${m[1]} 天，需 ≥ ${m[2]} 天）`;
    return '稳定天数不足';
  }
  return reason;
}

export function formatRunSummary(summary: Record<string, number> | null | undefined): string {
  if (!summary || Object.keys(summary).length === 0) return '暂无记录';
  const ok = summary.ok ?? 0;
  const skipped = summary.skipped ?? 0;
  const error = summary.error ?? 0;
  const running = summary.running ?? 0;
  const parts = [`成功 ${ok}`, `跳过 ${skipped}`, `失败 ${error}`];
  if (running > 0) parts.push(`运行中 ${running}`);
  return parts.join(' · ');
}
