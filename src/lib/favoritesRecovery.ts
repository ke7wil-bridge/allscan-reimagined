type FavoriteBackup = { path: string; exists: boolean; symlink: boolean; bytes: number | null }

export function canPreviewFavoriteBackup(file: FavoriteBackup): boolean {
  const name = file.path.split('/').pop() || ''
  const recognized = /^favorites[\w.\-[\]]*\.ini(?:\.bak|\.asr-backup-[\w-]+)$|^favorites[\w.-]*before[\w.-]*\.ini$/i.test(name)
  // The privileged helper reads approved backups; Apache need not read their contents.
  return file.exists && !file.symlink && file.bytes !== null && file.bytes > 0 && recognized
}
