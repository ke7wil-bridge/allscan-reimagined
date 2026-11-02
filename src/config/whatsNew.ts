export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.2',
  label: 'Beta 8.2',
  summary: 'This update hardens URF provisioning, keeps Zello on its own node, and improves bridge reliability.',
  items: [
    'Treats the configured URF transport node as authoritative and repairs stale managed Asterisk blocks without consuming another bridge node.',
    'Preserves and rebuilds Zello independently on its configured node, with validation that both node mappings remain present and distinct.',
    'Restores mobile Node Controls behavior and corrects fast dashboard polling for the Netoholics layout.',
    'Improves protocol enable and listener handling, legacy bridge recovery, native runtime checks, and Docker Apache/AMI reliability.',
  ],
} as const
