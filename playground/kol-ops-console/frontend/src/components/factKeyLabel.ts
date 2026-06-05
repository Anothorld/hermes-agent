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
  'identity.contact_role': {
    short: '联系人角色',
    title: '联系人类型：KOL 本人 / 经纪人 / 机构',
    kind: 'enum',
    enumOptions: [
      { value: 'kol', label: 'KOL 本人' },
      { value: 'manager', label: '经纪人' },
      { value: 'agency', label: '机构' },
    ],
  },
  'identity.manager_name': { short: '经纪人姓名', title: '经纪人或机构联系人姓名', kind: 'string' },
  'identity.manager_email': { short: '经纪人邮箱', title: '经纪人或机构联系邮箱', kind: 'email' },
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
  'identity.profile_og_image_url': {
    short: '主页头像缓存',
    title: 'Console 抓取的主页 OG 头像 URL（7 天有效）',
    kind: 'url',
  },
  'identity.profile_og_title': { short: '主页标题缓存', title: 'OG 标题缓存', kind: 'string' },
  'identity.profile_og_description': {
    short: '主页简介缓存',
    title: 'OG 简介缓存',
    kind: 'string',
  },
  'identity.profile_og_fetched_at': {
    short: 'OG 抓取时间',
    title: '主页 OG 缓存写入时间',
    kind: 'datetime',
  },
  'identity.profile_og_source_url': {
    short: 'OG 来源 URL',
    title: '写入 OG 缓存时对应的主页 URL',
    kind: 'url',
  },

  'identity.nox_creator_id': {
    short: 'Nox 达人 ID',
    title: 'NoxInfluencer 平台内的达人唯一标识',
    kind: 'string',
  },
  'identity.nox_diligence_verdict': {
    short: 'Nox 尽调结论',
    title: 'Gate A 短名单尽调的四档建议（high_priority 等）',
    kind: 'enum',
    enumOptions: [
      { value: 'high_priority', label: '优先合作' },
      { value: 'viable_with_risks', label: '可行（有风险）' },
      { value: 'needs_manual_review', label: '需人工复核' },
      { value: 'not_priority', label: '不建议优先' },
    ],
  },
  'identity.nox_diligence_at': {
    short: 'Nox 尽调时间',
    title: '最近一次 Nox 短名单尽调完成时间',
    kind: 'datetime',
  },
  'identity.nox_cache_month': {
    short: 'Nox 数据月份',
    title: '尽调缓存所属自然月（同月重复尽调可命中缓存）',
    kind: 'string',
  },
  'identity.nox_cache_key': {
    short: 'Nox 缓存键',
    title: '系统内部缓存标识（报告类型|达人ID|维度|语言）',
    kind: 'string',
  },
  'identity.nox_score': {
    short: 'Nox 综合分',
    title: 'Nox 尽调：评分综合分（overall）',
    kind: 'number',
  },
  'identity.nox_score_breakdown': {
    short: 'Nox 评分明细',
    title: 'Nox 尽调：评分分项 JSON（看板自动汇总展示，勿手改）',
    kind: 'string',
  },
  'identity.nox_engagement_rate': {
    short: 'Nox 互动率',
    title: 'Nox 尽调：内容互动率',
    kind: 'number',
  },
  'identity.nox_avg_views': {
    short: 'Nox 平均播放',
    title: 'Nox 尽调：平均播放量',
    kind: 'number',
  },
  'identity.nox_top_region': {
    short: 'Nox 主要地区',
    title: 'Nox 尽调：受众主要国家/地区分布',
    kind: 'string',
  },
  'identity.nox_channel_handle': {
    short: 'Nox 账号',
    title: 'Nox 尽调：频道 handle',
    kind: 'string',
  },
  'identity.nox_median_views': {
    short: 'Nox 中位播放',
    title: 'Nox 尽调：中位播放量',
    kind: 'number',
  },
  'identity.nox_wave': {
    short: 'Nox 播放波动',
    title: 'Nox 尽调：播放量波动系数',
    kind: 'number',
  },
  'identity.nox_avg_active_days': {
    short: 'Nox 活跃天数',
    title: 'Nox 尽调：周均活跃发帖天数',
    kind: 'number',
  },
  'identity.nox_view_per_followers': {
    short: 'Nox 粉播比',
    title: 'Nox 尽调：播放/粉丝比',
    kind: 'number',
  },
  'identity.nox_performance_levels': {
    short: 'Nox 表现等级',
    title: 'Nox 尽调：播放/波动/互动等等级 (L1–L5)',
    kind: 'string',
  },
  'identity.nox_benchmark_ranks': {
    short: 'Nox 对标排名',
    title: 'Nox 尽调：多维度对标百分位',
    kind: 'string',
  },
  'identity.nox_audience_authenticity': {
    short: 'Nox 受众真实度',
    title: 'Nox 尽调：受众真实性评分',
    kind: 'number',
  },
  'identity.nox_audience_authenticity_range': {
    short: 'Nox 真实度区间',
    title: 'Nox 尽调：受众真实度置信区间',
    kind: 'string',
  },
  'identity.nox_audience_quality_score': {
    short: 'Nox 受众质量',
    title: 'Nox 尽调：受众质量分',
    kind: 'number',
  },
  'identity.nox_gender_skew': {
    short: 'Nox 性别分布',
    title: 'Nox 尽调：受众性别占比',
    kind: 'string',
  },
  'identity.nox_audience_age_distribution': {
    short: 'Nox 年龄分布',
    title: 'Nox 尽调：受众年龄分布',
    kind: 'string',
  },
  'identity.nox_audience_adults_split': {
    short: 'Nox 成人儿童',
    title: 'Nox 尽调：成人与儿童受众占比',
    kind: 'string',
  },
  'identity.nox_audience_languages_top': {
    short: 'Nox 受众语言',
    title: 'Nox 尽调：受众语言分布',
    kind: 'string',
  },
  'identity.nox_audience_types_top': {
    short: 'Nox 受众类型',
    title: 'Nox 尽调：真实/可疑/机器人等受众类型占比',
    kind: 'string',
  },
  'identity.nox_audience_positive_pct': {
    short: 'Nox 正面受众',
    title: 'Nox 尽调：正面受众占比',
    kind: 'number',
  },
  'identity.nox_audience_promo_attractiveness': {
    short: 'Nox 推广吸引力',
    title: 'Nox 尽调：推广吸引力评分',
    kind: 'number',
  },
  'identity.nox_audience_promo_interested_pct': {
    short: 'Nox 推广兴趣',
    title: 'Nox 尽调：对推广感兴趣的受众占比',
    kind: 'number',
  },
  'identity.nox_audience_promo_professionalism': {
    short: 'Nox 推广专业度',
    title: 'Nox 尽调：推广专业度评分',
    kind: 'number',
  },
  'identity.nox_audience_interests_top': {
    short: 'Nox 受众兴趣',
    title: 'Nox 尽调：受众兴趣标签 Top',
    kind: 'string',
  },
  'identity.follower_count': {
    short: '粉丝数',
    title: '粉丝量（可能来自历史导入或发现）',
    kind: 'string',
  },
  'identity.nox_creator_name': { short: 'Nox 名称', title: 'Nox 达人显示名', kind: 'string' },
  'identity.nox_followers': { short: 'Nox 粉丝', title: 'Nox 尽调：粉丝数', kind: 'number' },
  'identity.nox_country': { short: 'Nox 国家', title: 'Nox 尽调：国家/地区代码', kind: 'string' },
  'identity.nox_platform': { short: 'Nox 平台', title: 'Nox 尽调：主平台', kind: 'string' },
  'identity.nox_benchmark_rank': { short: 'Nox 排名', title: 'Nox 尽调：表现百分位/排名', kind: 'number' },
  'identity.nox_channel_url': { short: 'Nox 频道', title: 'Nox 尽调：频道 URL', kind: 'url' },
  'identity.nox_content_tags_all': {
    short: 'Nox 全部标签',
    title: 'Nox 尽调：全部内容标签',
    kind: 'string',
  },
  'identity.nox_content_format_counts': {
    short: 'Nox 内容形式',
    title: 'Nox 尽调：帖/Reels/图等内容数量',
    kind: 'string',
  },
  'identity.nox_content_engagement_split': {
    short: 'Nox 分形式互动',
    title: 'Nox 尽调：按内容形式的平均互动',
    kind: 'string',
  },
  'identity.nox_cooperation_score': {
    short: 'Nox 合作分',
    title: 'Nox 尽调：合作倾向评分',
    kind: 'number',
  },
  'identity.nox_cooperation_price_estimate': {
    short: 'Nox 估价',
    title: 'Nox 合作：预估报价区间',
    kind: 'string',
  },
  'identity.nox_cooperation_first_price_range': {
    short: 'Nox 首次报价',
    title: 'Nox 合作：首次报价区间',
    kind: 'string',
  },
  'identity.nox_cooperation_final_price_range': {
    short: 'Nox 最终价',
    title: 'Nox 合作：最终成交报价区间',
    kind: 'string',
  },
  'identity.nox_cooperation_avg_response_hours': {
    short: 'Nox 响应时长',
    title: 'Nox 合作：平均响应小时数',
    kind: 'number',
  },
  'identity.nox_cooperation_contact_efficiency': {
    short: 'Nox 联系效率',
    title: 'Nox 合作：联系天数/轮次/合作周期',
    kind: 'string',
  },
  'identity.nox_cooperation_ad_video_stats': {
    short: 'Nox 广告视频',
    title: 'Nox 合作：广告视频占比与表现',
    kind: 'string',
  },
  'identity.nox_cooperation_brands_top': {
    short: 'Nox 合作品牌',
    title: 'Nox 合作：历史合作品牌',
    kind: 'string',
  },
  'identity.nox_cooperation_confirmation_pct': {
    short: 'Nox 确认率',
    title: 'Nox 合作：合作确认率',
    kind: 'number',
  },
  'identity.nox_cooperation_start_contact_pct': {
    short: 'Nox 联系率',
    title: 'Nox 合作：发起联系转化率',
    kind: 'number',
  },
  'identity.nox_cooperation_promotion_online_pct': {
    short: 'Nox 上线率',
    title: 'Nox 合作：推广内容上线率',
    kind: 'number',
  },
  'identity.nox_cooperation_active_period': {
    short: 'Nox 活跃时段',
    title: 'Nox 合作：活跃时段 (UTC)',
    kind: 'string',
  },
  'identity.nox_cooperation_brand_video_engagement_rate': {
    short: 'Nox 品牌互动',
    title: 'Nox 合作：品牌合作视频互动率',
    kind: 'number',
  },
  'identity.nox_cooperation_pros': {
    short: 'Nox 合作优点',
    title: 'Nox 尽调：合作优点列表',
    kind: 'string',
  },
  'identity.nox_cooperation_cons': {
    short: 'Nox 合作风险',
    title: 'Nox 尽调：合作风险/缺点列表',
    kind: 'string',
  },
  'identity.nox_dispute_types': {
    short: 'Nox 争议类型',
    title: 'Nox 尽调：历史争议类型',
    kind: 'string',
  },
  'identity.nox_dispute_count': { short: 'Nox 争议', title: 'Nox 合作争议次数', kind: 'number' },
  'identity.nox_cache_hit': { short: 'Nox 缓存命中', title: '上次尽调是否命中本月缓存', kind: 'bool' },
  'identity.nox_api_calls_last': { short: 'Nox API 次数', title: '上次尽调消耗的 API 次数', kind: 'number' },
  'identity.nox_diligence_dimensions': { short: '尽调维度', title: 'diligence-pack 拉取的维度', kind: 'string' },
  'identity.nox_diligence_lang': { short: '报告语言', title: 'Nox 报告语言代码', kind: 'string' },
  'identity.nox_email_quality': { short: '邮箱质量', title: 'Nox contacts 邮箱质量', kind: 'string' },
  'identity.nox_contacts_at': { short: 'Nox 查邮时间', title: 'Gate B contacts 完成时间', kind: 'datetime' },

  // offer.* — 我方动作 (we_did)
  'offer.outreach_sent': { short: '已发初邀', title: '我们是否已发出初邀邮件', kind: 'bool' },
  'offer.outreach_sent_at': { short: '初邀时间', title: '我们发出初邀的时间', kind: 'datetime' },
  'offer.outreach_draft_ready': {
    short: '初邀草稿已就绪',
    title: '是否已为该 KOL 起草过初邀',
    kind: 'bool',
  },
  'offer.outreach_draft_created': {
    short: '初邀草稿已创建',
    title: 'Gmail 中是否已创建初邀草稿',
    kind: 'bool',
  },
  'offer.gmail_draft_id': { short: 'Gmail 草稿 ID', title: 'Gmail 草稿标识', kind: 'string' },
  'offer.gmail_thread_id': { short: 'Gmail 线程 ID', title: 'Gmail 邮件线程标识', kind: 'string' },
  'offer.interest_clarify_asked': {
    short: '已追问意向',
    title: '是否已向 KOL 追问合作意向',
    kind: 'bool',
  },
  'offer.interest_clarify_question': {
    short: '意向追问',
    title: '向 KOL 提出的意向澄清问题',
    kind: 'string',
  },
  'offer.sku_requested': { short: '已询 SKU', title: 'KOL 询问或要求的产品 SKU', kind: 'string' },
  'offer.proposed_skus': { short: '提议 SKU 列表', title: '我方提议的产品 SKU 列表', kind: 'json' },
  'offer.deliverable_platforms_proposed': {
    short: '提议平台',
    title: '我方提议的交付平台（待对方确认）',
    kind: 'json',
  },
  'offer.deliverable_count_proposed': {
    short: '提议条数',
    title: '我方提议的每平台稿件条数（待确认）',
    kind: 'json',
  },
  'offer.deliverable_count_per_platform_requested': {
    short: '对方要求条数',
    title: 'KOL 要求的每平台稿件条数',
    kind: 'json',
  },
  'offer.proposed_amount': { short: '我方还价', title: '我方提出的合作金额', kind: 'currency' },
  'offer.proposed_basis': { short: '报价依据', title: '报价计算依据说明', kind: 'string' },
  'offer.proposed_currency': { short: '还价币种', title: '我方还价使用的币种', kind: 'string' },
  'offer.barter_attempted': { short: '已尝试换货', title: '是否已提出换货合作方案', kind: 'bool' },
  'offer.rate_requested': { short: '已索要报价', title: '是否已向 KOL 索要报价', kind: 'bool' },
  'offer.paid_hold_sent': { short: '付费暂缓说明', title: '是否已发送付费合作暂缓说明', kind: 'bool' },
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
      { value: 'needs_more_info', label: '需更多信息' },
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
  'approval.style_learning_proposal': {
    short: '学习提案',
    title: '编辑学习蒸馏提案（待人工批准）',
    kind: 'json',
  },
  'approval.archival_outcome': { short: '归档结果', title: '合作结束后的归档结论', kind: 'string' },
  'approval.relationship_synced': {
    short: '关系已同步',
    title: '红人关系档案是否已同步',
    kind: 'bool',
  },
  'approval.preferred_skus_synced': {
    short: '偏好 SKU 已同步',
    title: '偏好产品 SKU 是否已写入关系档案',
    kind: 'bool',
  },
  'approval.preferred_mode_synced': {
    short: '偏好模式已同步',
    title: '偏好合作模式是否已写入关系档案',
    kind: 'bool',
  },
  'approval.followups_pending': {
    short: '待跟进事项',
    title: '归档后待人工跟进的事项',
    kind: 'json',
  },

  // payout.*
  'payout.method_collected': {
    short: '收款方式已收集',
    title: 'KOL 收款方式是否已收集',
    kind: 'bool',
  },
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
