export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.3.4',
  label: 'Beta 8.3.4',
  summary: 'Corrects the URF Install Bridge workflow and makes failures visible.',
  items: [
    'Keeps the validated setup plan when you enter the install-only TGIF hotspot security key.',
    'Explains that requirement before installation instead of silently stopping before the helper runs.',
    'Keeps browser, network, and server errors visible so failed installations can be corrected safely.',
    'Preserves URF073, private node 1001, TGIF talkgroup 42450, and the DMR-only selection.',
  ],
} as const
