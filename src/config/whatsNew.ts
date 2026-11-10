export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.4.3',
  label: 'Beta 8.4.3',
  summary: 'Restores updater recovery and improves administrative client controls.',
  items: [
    'Automatically repairs a missing updater during integrity recovery.',
    'Shows clients for bridges supporting client listing, even without ban capability.',
    'Preserves backend authorization for Kick and Ban actions.',
    'Keeps release descriptions generic across installations.',
  ],
} as const
