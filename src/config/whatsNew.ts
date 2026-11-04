export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.3.3',
  label: 'Beta 8.3.3',
  summary: 'Allows a display-only URF card to become a fresh managed installation safely.',
  items: [
    'Treats an explicit display-only URF card with no runtime as a fresh install instead of an ownership recovery.',
    'Preserves the selected reflector, private node, TGIF talkgroup, and DMR-only protocol choice.',
    'Still rejects partial managed ownership, conflicting runtime files, and unsafe existing installations.',
    'Explicitly verifies native URF installation compatibility with Debian 13.',
  ],
} as const
