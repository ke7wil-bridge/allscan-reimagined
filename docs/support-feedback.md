# Support & Feedback

Flow: ASR browser → central Cloudflare Worker → ASR GitHub App → public issue in `ke7wil-bridge/allscan-reimagined`.

ASR installations contain no GitHub credentials. The frontend only receives the public Worker endpoint at build time as `VITE_ASR_SUPPORT_ENDPOINT`. Bug reports may include sanitized runtime/browser details; local service/log diagnostics remain admin-only. Questions and feature requests do not collect diagnostics automatically.

The review screen states that the issue is public and shows the exact issue text plus screenshot filenames. Screenshots are optional for all request types, limited to 3 PNG/JPEG/WebP files of 2 MB each, stored by the central service in a public R2 bucket and embedded into the issue. Do not use this storage for private material.

The Worker re-validates input, re-redacts common authentication secrets, uses a honeypot, and limits each source IP to 5 submissions/hour. Labels are: bug → `bug, asr-report`; question → `question, asr-support`; feature → `enhancement, asr-feedback`.

## GitHub App setup

Create an ASR Support GitHub App owned by the appropriate account/organization. Grant only repository **Issues: Read and write** permission, with no user permissions. Install it only on `ke7wil-bridge/allscan-reimagined`.

Record the App ID and installation ID. Generate a private key. Store the private key only as a Worker secret; never commit it or distribute it with ASR.

Configure Worker secrets: `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, and `GITHUB_APP_PRIVATE_KEY`. Configure a KV namespace as `RATE_LIMIT`, an R2 bucket as `ATTACHMENTS`, and `ATTACHMENT_BASE_URL` to the bucket's public custom/domain URL. GitHub notifications then work normally for repository issue notifications.

Deploy the Worker, then set the release build's `VITE_ASR_SUPPORT_ENDPOINT` to its public HTTPS URL.