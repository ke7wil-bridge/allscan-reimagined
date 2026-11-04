export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.3',
  label: 'Beta 8.3',
  summary: 'Improves URF configuration, bridge activity reliability, and mobile controls.',
  items: [
    'Adds a configurable URF reflector display name and prevents legacy installation defaults from appearing as another reflector identity.',
    'Improves browser-based URF runtime installation and repair while preserving the configured shared transport node.',
    'Prevents blocking EchoLink CLI operations from stalling live Asterisk status and restores bounded callsign lookup.',
    'Improves bridge activity reporting, status recovery, and mobile Node Controls layout.',
  ],
} as const
