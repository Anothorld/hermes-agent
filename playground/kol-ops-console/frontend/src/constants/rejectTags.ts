/** Controlled reject tags — keep in sync with bridge reject_tags.py */
export const REJECT_TAGS = [
  'tone_too_salesy',
  'premature_pricing',
  'wrong_sku',
  'over_promised',
  'ignored_question',
  'wrong_language',
  'too_long',
  'factual_error',
  'other',
] as const;

export type RejectTag = (typeof REJECT_TAGS)[number];

export const REJECT_TAG_LABELS: Record<RejectTag, string> = {
  tone_too_salesy: '语气太推销',
  premature_pricing: '过早谈价',
  wrong_sku: 'SKU/产品错误',
  over_promised: '过度承诺',
  ignored_question: '未回答问题',
  wrong_language: '语言不对',
  too_long: '太长',
  factual_error: '事实错误',
  other: '其他',
};

export type RejectCorrection = {
  tags: RejectTag[];
  note: string;
  suggested_fix: string;
};

export const EMPTY_REJECT_CORRECTION: RejectCorrection = {
  tags: ['other'],
  note: '',
  suggested_fix: '',
};
