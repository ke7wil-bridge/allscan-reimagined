# ASRPlus source boundary

The historical dashboard JavaScript, status collector and staging fixtures in the public ASR repository are not an authoritative production web bundle. Do not deploy those files over an existing ASRPlus dashboard or treat a passing public ASR release gate as proof of dashboard parity. The separately managed dashboard must be deployed from its reviewed, versioned private source with an integrity manifest, regression tests, approved backups and explicit deployment approval.

Keep generic ASR moderation helpers and regression fixtures here; do not copy private dashboard assets, live host configuration, keys, credentials, runtime data or backups into this repository. Reconcile any changes to shared interfaces with the private dashboard before promoting either component. See #202 and #299 for coordination.
