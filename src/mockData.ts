export type HeaderStat = {
  label: string
  value: string
  tone: 'idle' | 'source' | 'relay' | 'neutral'
}

export const headerStats: HeaderStat[] = [
  { label: 'Green = Idle', value: 'Idle', tone: 'idle' },
  { label: 'Red = Source/TX', value: 'Source/TX', tone: 'source' },
  { label: 'Amber = Relay', value: 'Relay', tone: 'relay' },
]
