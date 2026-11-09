import releaseNotes from '../../release-notes/v1.0.0-beta.8.4.3.md?raw'
import { parseReleaseChanges } from '../lib/releaseNotes'

// Read the authoritative notes included in this exact installed release.
// Never maintain a second, shorter or out-of-date highlights array here.
export const ASR_WHATS_NEW = {
  version: '1.0.0-beta.8.4.3',
  label: 'Beta 8.4.3',
  summary: 'Everything fixed and improved in this release:',
  items: parseReleaseChanges(releaseNotes),
}
