// Only the handled flags are removed by the caller; keep other query values and the hash.
export function replaceDashboardQuery(params: URLSearchParams): void {
  const query = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
}
