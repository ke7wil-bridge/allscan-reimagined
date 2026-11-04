export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.3.2',
  label: 'Beta 8.3.2',
  summary: 'Restores safe, targeted URF DMR client Disconnect and Ban controls.',
  items: [
    'Shows Disconnect and Ban controls only when the DMR administration runtime is actually writable.',
    'Targets the selected current callsign and DMR ID while rejecting stale or ambiguous client references.',
    'Refreshes Connected Clients after each action without disturbing unrelated clients.',
    'Preserves Beta 8.3.1 URF ownership recovery, protocol visibility, and existing bridge configuration.',
  ],
} as const
