/**
 * Extract the complete user-visible change list from the installed release's
 * authoritative Markdown notes. Other sections (known issues, install steps)
 * are not changes and must not appear as shipped improvements.
 * @param {string} markdown
 * @returns {string[]}
 */
export function parseReleaseChanges(markdown) {
  const items = []
  let inChanges = false
  let sections = 0

  for (const line of markdown.replace(/\r\n?/g, '\n').split('\n')) {
    const heading = /^##\s+(.+?)\s*$/.exec(line)
    if (heading) {
      inChanges = /^(?:changes|fixes|improvements|enhancements|features|updates|what[’']s (?:new|included))\b/i.test(heading[1])
      if (inChanges) sections += 1
      continue
    }
    if (!inChanges) continue
    // Include nested and numbered release bullets too; none may be discarded.
    const item = /^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$/.exec(line)
    if (item) {
      items.push(item[1])
    } else if (/^\s{2,}\S/.test(line) && items.length) {
      // Keep wrapped notes instead of silently dropping their second line.
      items[items.length - 1] += ` ${line.trim()}`
    }
  }

  if (!sections || !items.length) {
    throw new Error('Release notes must include a complete bulleted Changes section.')
  }
  return items
}
