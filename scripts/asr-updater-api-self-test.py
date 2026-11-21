#!/usr/bin/env python3
"""Static contract checks for the duplicated updater API boundary."""

from pathlib import Path


root = Path(__file__).resolve().parents[1]
apis = [root / "asr-api.php", root / "server/asr-api.php"]


def updater_function(source: str) -> str:
    start = source.index("function asr_updater_command(")
    end = source.index("\nfunction ", start + 1)
    return source[start:end]


sources = [path.read_text() for path in apis]
blocks = [updater_function(source) for source in sources]
assert blocks[0] == blocks[1], "deployed and source API updater contracts differ"
assert sources[0] == sources[1], "deployed and source API copies differ"
block = blocks[0]
for required in (
    "$operation === 'status'",
    "$exit === 0",
    "($payload['ok'] ?? null) === false",
    "($payload['state'] ?? '') === 'failed'",
    "hash_equals($jobId, (string) ($payload['jobId'] ?? ''))",
    "$exit !== 0 || (empty($payload['ok']) && !$failedStatus)",
    "'diagnostic' => '--diagnostic-json'",
):
    assert required in block, f"missing failed-job API guard: {required}"


def accepted(operation: str, exit_code: int, payload: dict, job_id: str) -> bool:
    failed_status = (
        operation == "status"
        and exit_code == 0
        and payload.get("ok") is False
        and payload.get("state", "") == "failed"
        and payload.get("jobId", "") == job_id
    )
    return exit_code == 0 and (bool(payload.get("ok")) or failed_status)


job = "20261108-120000-aabbccdd"
failed = {"ok": False, "jobId": job, "state": "failed",
          "message": "Update failed; previous installation restored."}
assert accepted("status", 0, failed, job)
assert not accepted("status", 1, failed, job)
assert not accepted("queue", 0, failed, job)
assert not accepted("status", 0, {**failed, "jobId": "20261108-120000-deadbeef"}, job)
assert not accepted("status", 0, {**failed, "state": "installing"}, job)
assert accepted("status", 0, {**failed, "ok": True, "state": "complete"}, job)

route = sources[0][sources[0].index("if ($action === 'update-job-report')"):]
route = route[:route.index("\nif (", 1)]
assert route.index("asr_require_admin();") < route.index("asr_updater_command('diagnostic', $jobId)")
assert "Content-Type: text/plain" in route
assert "Content-Disposition: attachment" in route
assert "$_GET['jobId']" in route

print("ASR updater API self-test: PASS")
