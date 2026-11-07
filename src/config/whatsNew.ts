export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.4',
  label: 'Beta 8.4',
  summary: 'Improves bridge reliability, installation diagnostics, and node health visibility.',
  items: [
    'Correlates the browser request, API validation, and privileged helper stage without recording credentials.',
    'Records whether installation started, failed, or completed instead of logging only successful installs.',
    'Automatically checks runtime files, managed services, startup state, and bounded TGIF authentication evidence after a failure.',
    'Shows the supported failure stage and runtime diagnosis directly in the Bridge Setup Wizard.',
  ],
} as const
