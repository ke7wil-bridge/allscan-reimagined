export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.1.5.15',
  label: 'Beta 8.1.5.15',
  summary: 'This update adds a 48-hour node health recorder and warns before memory pressure becomes a service problem.',
  items: [
    'Records memory, swap, system load, and process memory every minute for up to 48 hours so problems can be tied to recorded evidence.',
    'Warns on the dashboard when available memory is low or swap use is rising.',
    'Protects the radio service from resource pressure and only attempts recovery of another service after repeated measurements identify it.',
    'Keeps recovery conservative: restarting a service means it is ready again, not that end-to-end radio audio has been confirmed.',
  ],
} as const
