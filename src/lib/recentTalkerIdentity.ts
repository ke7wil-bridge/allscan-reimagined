type RecentTalker = { callsign: string; startEpoch: number; eventEpoch: number }

export function matchingRecentTalker<T extends RecentTalker>(entries: readonly T[] | undefined, talker: { startedEpoch: number; eventEpoch: number }): T | undefined {
  const eventTime = talker.eventEpoch || talker.startedEpoch
  return entries
    ?.map((entry) => ({
      entry,
      distance: Math.min(
        talker.startedEpoch > 0 ? Math.abs(entry.startEpoch - talker.startedEpoch) : Number.MAX_SAFE_INTEGER,
        talker.eventEpoch > 0 ? Math.abs(entry.eventEpoch - talker.eventEpoch) : Number.MAX_SAFE_INTEGER,
      ),
    }))
    .filter(({ entry, distance }) => Boolean(entry.callsign) && eventTime > 0 && distance <= 3)
    .sort((left, right) => left.distance - right.distance)[0]?.entry
}
