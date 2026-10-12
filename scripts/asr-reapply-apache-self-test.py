#!/usr/bin/env python3
"""Guard the Apache worker replacement required after an ASR release swap."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
    block_start = reapply.index("if command -v apache2ctl >/dev/null 2>&1; then")
    block_end = reapply.index("\nfi", block_start)
    apache_block = reapply[block_start:block_end]

    configtest = apache_block.index("apache2ctl configtest")
    restart = apache_block.index("systemctl restart apache2")
    assert configtest < restart, "Apache must pass configtest before restart"
    assert "systemctl reload apache2" not in apache_block, (
        "A graceful reload can preserve stale long-lived PHP/SSE workers"
    )
    assert "systemctl restart asterisk" not in apache_block
    assert "bridge" not in apache_block[restart:], (
        "The Apache refresh must not restart bridge services"
    )
    print("Apache SSE worker replacement self-test: PASS")


if __name__ == "__main__":
    main()
