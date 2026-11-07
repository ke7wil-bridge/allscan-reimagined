export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.4.1',
  label: 'Beta 8.4.1',
  summary: 'Improves bridge connection status and modularizes dashboard styling.',
  items: [
    'Corrects standard bridge link detection using the configured AllStar node.',
    'Shows missing-link warnings and individual reconnect controls when the connection feed is ready.',
    'Uses concise bridge card labels for configured digital bridges.',
    'Splits dashboard styling into ordered modules and adds visual regression qualification.',
  ],
} as const
