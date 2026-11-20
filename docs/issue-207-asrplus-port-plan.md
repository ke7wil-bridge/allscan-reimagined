# Issue 207 — ASRPlus to standard ASR chart port

## Verified sources
- Standard ASR: `compat/allscan-v1.05/lookup/index.php` renders an inline image with 5-minute refresh; older compat variants exist.
- ASRPlus: `/opt/asrplus/current/web/asrplus.js` renders a themed interactive SVG graph in an accessible modal.
- ASRPlus backend: `/opt/asrplus/current/web/network-graph.php` obtains `stats.data.links` from `https://stats.allstarlink.org/api/stats/{node}` and caches responses for 300 seconds. Upstream calls are rate-limited.

## Integration decisions
- Port as standard ASR-specific components, not wholesale ASRPlus source or branding.
- Keep standard ASR lookup, station map, and bridge behavior unchanged.
- Use an authenticated server-side topology proxy with node validation, timeouts, bounded requests, cache and rate limiting. Avoid exposing private ASRPlus endpoints.
- Render SVG graph with ASR theme tokens, accessible node controls, drag, zoom, pan, collapse, and details; keep JPEG fallback.
- Prefer a self-contained module to avoid editing all compat versions by hand. Verify actual deployed lookup version before wiring.
- Do not port ASRPlus hub-specific assumptions or its fixed theme palette.

## Status (November 20)
Implemented in isolated standard ASR v1.05 compatibility worktree. Interactive popout, station details, adaptive ASRPlus-style layered layout, drag, collapse/expand, zoom, 24-hour cached edges, and progressive discovery are in place. PHP lint and browser tests pass with simulated topology, including 47-station collapse/expand. The reapply installer copies the new PHP endpoints; integrity verification includes them.

**Outstanding before parity sign-off:** authenticated end-to-end live ASR test, visual side-by-side comparison with ASRPlus, and review of rate limiting under multi-node discovery. The preview is simulated, not live. No deploy, push, or merge without owner approval.
