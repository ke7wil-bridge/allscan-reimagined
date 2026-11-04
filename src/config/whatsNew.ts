export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.3.1',
  label: 'Beta 8.3.1',
  summary: 'Corrects URF recovery, protocol visibility, and connection-management menus.',
  items: [
    'Shows only the URF protocols enabled in Settings while retaining legacy all-protocol configurations.',
    'Keeps authorized Disconnect and Ban menus visible above dashboard stacking contexts on desktop and mobile.',
    'Safely restores missing ownership records only when an existing URF runtime is proven to be ASR-managed.',
    'Preserves the existing URF installation, transport node, protocol settings, and bridge credentials during browser updates.',
  ],
} as const
