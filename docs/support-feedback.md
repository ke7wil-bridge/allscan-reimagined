# Support & Feedback

Flow: ASR browser → central Cloudflare Worker → ASR GitHub App → public issue in `ke7wil-bridge/allscan-reimagined`.

ASR installations contain no GitHub credentials. The frontend only receives the public Worker endpoint at build time as `VITE_ASR_SUPPORT_ENDPOINT`. Bug reports may include sanitized runtime/browser details; local service/log diagnostics remain admin-only. Questions and feature requests do not collect diagnostics automatically.

The review screen states that the issue is public, shows the exact issue text and screenshot previews, and lets the submitter remove individual screenshots. Screenshots are optional for all request types and limited to 3 PNG/JPEG/WebP files of 2 MB each. The central service verifies each file's decoded size and magic bytes before storing it in a private Workers KV namespace. The Worker serves stored images from its `/attachments/` route with an explicit image content type and `X-Content-Type-Options: nosniff`.

The Worker re-validates input, re-redacts common authentication secrets, uses a honeypot, and limits each source to 5 submissions/hour. It derives the KV rate-limit key from an HMAC of the source IP and a secret salt; raw IP addresses are not stored in KV. If any screenshot upload or GitHub issue creation fails, the Worker deletes screenshots uploaded for that failed submission. Labels are: bug → `bug, asr-report`; question → `question, asr-support`; feature → `enhancement, asr-feedback`.

Screenshots and their contents become public with the GitHub issue. Text redaction cannot detect secrets inside images, so submitters must review every image before submission.

## GitHub App setup

Create an ASR Support GitHub App owned by the appropriate account/organization. Grant only repository **Issues: Read and write** permission, with no user permissions. Install it only on `ke7wil-bridge/allscan-reimagined`.

Record the App ID and installation ID. Generate a private key. Store the private key only as a Worker secret; never commit it or distribute it with ASR.

Configure Worker secrets: `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, and a randomly generated `RATE_LIMIT_SALT`. Configure separate private Workers KV namespaces as `RATE_LIMIT` and `ATTACHMENTS`. Set `ATTACHMENT_BASE_URL` to the Worker's public HTTPS URL followed by `/attachments`; screenshots are never exposed directly from storage. GitHub notifications then work normally for repository issue notifications.

The card-free Workers Free plan currently limits KV to 1 GB stored data, 1,000 writes/day, 1,000 deletes/day, and 100,000 reads/day. A KV value may be up to 25 MiB, which is above this service's 2 MiB per-screenshot limit. When a free-plan daily quota or storage limit is reached, new attachment operations fail instead of generating overage charges. The service then returns an upload error and does not create a GitHub issue with missing screenshots. KV is eventually consistent, so a newly uploaded screenshot may take a short time to appear in every region.

Deploy the Worker, then set the release build's `VITE_ASR_SUPPORT_ENDPOINT` to its public HTTPS URL.
