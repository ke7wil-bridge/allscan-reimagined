export type SortDirection = 'asc' | 'desc'

export function nextSort<K extends string>(current: { key: K; direction: SortDirection }, key: K): { key: K; direction: SortDirection } {
  return current.key === key
    ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
    : { key, direction: 'asc' }
}

export function hslToFavoriteHex(hue: number, saturation: number, lightness: number): string {
  const a = saturation * Math.min(lightness, 1 - lightness)
  const f = (n: number) => {
    const k = (n + hue / 30) % 12
    return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
  }
  return '#' + [f(0), f(8), f(4)].map((v) => Math.round(255 * v).toString(16).padStart(2, '0')).join('')
}

export function pickFavoriteHex(clientX: number, clientY: number, rect: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>, hue: number): string {
  const saturation = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
  const lightness = Math.max(0, Math.min(1, 1 - ((clientY - rect.top) / rect.height)))
  return hslToFavoriteHex(hue, saturation, lightness)
}
