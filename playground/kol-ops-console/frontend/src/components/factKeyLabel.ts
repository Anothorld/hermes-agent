// Human-readable rendering of bridge fact keys. Operators rarely need
// to read the raw `offer.outreach_sent_at` form — the chip should say
// "初邀时间" with the raw key tucked into the hover tooltip. This file
// is the single dictionary all chip/input components consult.
//
// Adding new entries is cheap; missing entries fall back to the
// namespace-stripped key so the UI degrades gracefully.

export type FactKind =
  | 'bool'
  | 'datetime'
  | 'string'
  | 'number'
  | 'enum'
  | 'url'
  | 'json'
  | 'email'
  | 'currency';

export type FactEnumOption = { value: string; label: string };

export type FactLabel = {
  // Short label used inside the chip (must stay tight).
  short: string;
  // Tooltip shown on hover — explains what fills this field. Includes
  // the raw key as a prefix so devs/operators in advanced mode can
  // still see it without toggling preferences.
  title: string;
  // Optional type hint for FactInput. When absent, FactInput falls
  // back to a plain text input.
  kind?: FactKind;
  // For ``kind === 'enum'`` only: the allowed values + their labels.
  enumOptions?: ReadonlyArray<FactEnumOption>;
};

type DictEntry = {
  short: string;
  title: string;
  kind?: FactKind;
  enumOptions?: ReadonlyArray<FactEnumOption>;
};

const FACT_DICT: Record<string, DictEntry> = {
  // identity.*
  'identity.email': { short: '邮箱', title: 'KOL 的联系邮箱', kind: 'email' },
  'identity.primary_email': { short: '主邮箱', title: 'KOL 的主联系邮箱', kind: 'email' },
  'identity.primary_handle': { short: '主账号', title: 'KOL 的主账号 handle', kind: 'string' },
  'identity.creator_type': { short: '账号类型', title: 'KOL 的创作者类型', kind: 'string' },
  'identity.region': { short: '地区', title: 'KOL 所在地区', kind: 'string' },
  'identity.followers': { short: '粉丝数', title: 'KOL 当前粉丝量', kind: 'number' },
  'identity.language': { short: '语言', title: 'KOL 沟通语言', kind: 'string' },
  'identity.last_outreach_draft_at': {
    short: '上次起稿',
    title: '上次为该 KOL 起草初邀的时间',
    kind: 'datetime',
  },
  'identity.outreach_path': {
    short: '触达路径',
    title: '触达分路：cold (新)、reengage (老朋友)、re_reach',
    kind: 'enum',
    enumOptions: [
      { value: 'cold', label: '冷启动' },
      { value: 'reengage', label: '回访' },
      { value: 're_reach', label: '二次触达' },
    ],
  },

  // identity.* — 社交主页 URL (快速跳转栏 + Confirmed Facts 双重渲染)
  'identity.instagram_profile_url': { short: 'IG 主页', title: 'Instagram 主页 URL', kind: 'url' },
  'identity.tiktok_profile_url': { short: 'TikTok 主页', title: 'TikTok 主页 URL', kind: 'url' },
  'identity.youtube_profile_url': { short: 'YouTube 频道', title: 'YouTube 频道 URL', kind: 'url' },
  'identity.facebook_profile_url': { short: 'Facebook 主页', title: 'Facebook 主页 / Page URL', kind: 'url' },
  'identity.twitter_profile_url': { short: 'X / Twitter', title: 'X (原 Twitter) 主页 URL', kind: 'url' },
  'identity.threads_profile_url': { short: 'Threads', title: 'Threads 主页 URL', kind: 'url' },
  'identity.linktree_url': { short: 'Link-in-bio', title: 'Linktree / Beacons / bio.link / lnk.bio / solo.to', kind: 'url' },
  'identity.personal_site_url': { short: '个人站', title: '个人网站 / 工作室站点', kind: 'url' },

  // offer.* — 我方动作 (we_did)
  'offer.outreach_sent': { short: '已发初邀', title: '我们是否已发出初邀邮件', kind: 'bool' },
  'offer.outreach_sent_at': { short: '初邀时间', title: '我们发出初邀的时间', kind: 'datetime' },
  'offer.outreach_draft_ready': {
    short: '初邀草稿已就绪',
    title: '是否已为该 KOL 起草过初邀',
    kind: 'bool',
  },
  'offer.outreach_path': {
    short: '初邀类型',
    title: '初邀路径：cold / reengage',
    kind: 'enum',
    enumOptions: [
      { value: 'cold', label: '冷启动' },
      { value: 'reengage', label: '回访' },
    ],
  },
  'offer.sku_locked': { short: '已锁 SKU', title: '与 KOL 锁定的产品 SKU', kind: 'string' },
  'offer.color_or_variant_locked': {
    short: '已锁配色',
    title: '锁定的颜色 / 变体',
    kind: 'string',
  },
  'offer.deliverable_platforms': {
    short: '交付平台',
    title: '约定的发稿平台清单',
    kind: 'json',
  },
  'offer.deliverable_count_per_platform': {
    short: '每平台条数',
    title: '每个平台的稿件条数',
    kind: 'json',
  },
  'offer.deliverables_scope': {
    short: '交付范围',
    title: '已约定的交付范围',
    kind: 'string',
  },
  'offer.usage_rights_discussed': {
    short: '使用权',
    title: '内容使用权的讨论结果',
    kind: 'string',
  },
  'offer.compensation_amount': {
    short: '我方报价',
    title: '我方报价金额',
    kind: 'currency',
  },
  'offer.compensation_currency': {
    short: '币种',
    title: '报价币种 (USD / CNY ...)',
    kind: 'string',
  },
  'offer.compensation_mode': {
    short: '合作模式',
    title: '合作模式：barter / paid / hybrid / gifted',
    kind: 'enum',
    enumOptions: [
      { value: 'barter', label: '换货 (barter)' },
      { value: 'paid', label: '付费 (paid)' },
      { value: 'hybrid', label: '混合 (hybrid)' },
      { value: 'gifted', label: '赠品 (gifted)' },
    ],
  },
  'offer.contract_sent': { short: '合同已发', title: '我们是否已经把合同发出去', kind: 'bool' },
  'offer.brief_sent': { short: '已发 brief', title: '我们是否已发出内容 brief', kind: 'bool' },
  'offer.boost_assets_status': {
    short: '投放素材',
    title: '广告投放素材的准备状态',
    kind: 'string',
  },

  // offer.* — 对方反馈 (they_replied)
  'offer.interest_signal': {
    short: '对方意向',
    title: '对方对合作的态度。需要 KOL 回信后由 reply-dispatcher 写入',
    kind: 'enum',
    enumOptions: [
      { value: 'confirmed', label: '已确认' },
      { value: 'interested', label: '有意向' },
      { value: 'declined', label: '已拒绝' },
      { value: 'unsure', label: '不确定' },
    ],
  },
  'offer.fit_confirmed': {
    short: '匹配确认',
    title: '产品与 KOL 是否匹配（人工或对方确认）',
    kind: 'bool',
  },
  'offer.kol_paid_quote': { short: '对方报价', title: 'KOL 报出的合作价', kind: 'currency' },
  'offer.kol_quote': { short: '对方报价', title: 'KOL 报出的合作价', kind: 'currency' },
  'offer.agreed_terms': {
    short: '已达成条款',
    title: '双方就费用和交付达成的条款',
    kind: 'string',
  },
  'offer.contract_signed': { short: '合同已签', title: '对方是否已签署合同', kind: 'bool' },
  'offer.contract_declined_reason': {
    short: '合同被拒',
    title: '对方拒签合同的原因',
    kind: 'string',
  },
  'offer.draft_submitted': {
    short: '草稿已交',
    title: '对方是否已提交内容草稿',
    kind: 'bool',
  },
  'offer.review_verdict': {
    short: '审核结论',
    title: '内容审核结论',
    kind: 'enum',
    enumOptions: [
      { value: 'approved', label: '通过' },
      { value: 'rejected', label: '驳回' },
      { value: 'changes_requested', label: '要求修改' },
    ],
  },
  'offer.posted_url': { short: '发布链接', title: '内容上线后的链接', kind: 'url' },

  // fulfillment.*
  'fulfillment.address_collected': {
    short: '已收地址',
    title: '收件地址是否到手',
    kind: 'bool',
  },
  'fulfillment.shipping_address': {
    short: '收件地址',
    title: 'KOL 收件地址',
    kind: 'string',
  },
  'fulfillment.shipping_method': {
    short: '运输方式',
    title: '约定的物流方式',
    kind: 'string',
  },
  'fulfillment.tracking_filled': {
    short: '物流单号',
    title: '是否已填入物流追踪号',
    kind: 'bool',
  },
  'fulfillment.tracking_no': { short: '追踪号', title: '物流追踪号', kind: 'string' },
  'fulfillment.tracking_carrier': { short: '承运商', title: '物流承运商', kind: 'string' },
  'fulfillment.delivered_confirmed': {
    short: '签收确认',
    title: 'KOL 是否已签收',
    kind: 'bool',
  },

  // approval.*
  'approval.reply_draft': {
    short: '回信草稿',
    title: '等待人工审核的回信草稿',
    kind: 'json',
  },
  'approval.contract_change_request': {
    short: '改合同请求',
    title: '对方对合同条款的修改请求',
    kind: 'json',
  },
  'approval.logistics_anomaly': {
    short: '物流异常',
    title: '物流出现需要人工处理的异常',
    kind: 'json',
  },
  'approval.compensation_cap_breach': {
    short: '报价超限',
    title: 'KOL 报价超过预算上限，需操作员审批',
    kind: 'json',
  },
  'approval.identity_drift_review': {
    short: '账号异常审查',
    title: 'KOL 账号信息变更，需人工复核',
    kind: 'json',
  },
  'approval.over_budget_request': {
    short: '超预算申请',
    title: '提交超预算请求等待审批',
    kind: 'json',
  },
  'approval.paid_ceiling_override': {
    short: '提价上限',
    title: '允许把报价上限提到 X',
    kind: 'currency',
  },
  'approval.request': { short: '审批请求', title: '通用审批请求', kind: 'json' },
  'approval.responded': { short: '审批结果', title: '审批结论', kind: 'json' },
};

export function factKeyLabel(key: string): FactLabel {
  const hit = FACT_DICT[key];
  if (hit) {
    return {
      short: hit.short,
      title: `${key} — ${hit.title}`,
      kind: hit.kind,
      enumOptions: hit.enumOptions,
    };
  }
  // Fall back to the key with namespace stripped so chips stay compact
  // even for un-mapped keys.
  const stripped = key.includes('.') ? key.split('.').slice(1).join('.') : key;
  return { short: stripped, title: key };
}
