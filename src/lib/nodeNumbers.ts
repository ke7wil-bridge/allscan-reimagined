export function canPopulateNodeControl(value: string) {
  const node = value.trim()
  if (!/^\d{4,}$/.test(node)) return false
  return Number(node) >= 2000
}
