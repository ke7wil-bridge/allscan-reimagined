#!/usr/bin/env python3
"""Temp-only regression tests for the installer's release swap rollback."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


class InjectedFailure(RuntimeError):
    pass


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def inject(step: str, failure_step: str | None) -> None:
    if step == failure_step:
        raise InjectedFailure(step)


def marker(path: Path) -> str:
    return (path / "marker.txt").read_text(encoding="utf-8").strip()


def exercise_release_swap(
    root: Path,
    *,
    initial_version: str | None,
    new_version: str,
    failure_step: str | None,
) -> None:
    releases = root / "releases"
    releases.mkdir()
    current = root / "current"
    initial_release: Path | None = None
    if initial_version is not None:
        initial_release = releases / initial_version
        initial_release.mkdir()
        (initial_release / "marker.txt").write_text("old\n", encoding="utf-8")
        current.symlink_to(initial_release)

    release_dir = releases / new_version
    stage = releases / f"{new_version}.new.test"
    stage.mkdir()
    (stage / "marker.txt").write_text("new\n", encoding="utf-8")
    previous = releases / f"{new_version}.previous.test"

    captured_target = current.resolve(strict=True) if current.exists() else None
    previous_armed = False
    release_replaced = False
    link_restore_armed = False
    failed = False

    try:
        if release_dir.is_dir():
            release_dir.rename(previous)
            previous_armed = True
        inject("after_previous_move", failure_step)

        # The shell installer arms release cleanup before the stage rename.
        release_replaced = True
        inject("before_release_install", failure_step)
        stage.rename(release_dir)
        inject("after_release_install", failure_step)

        # The shell installer arms current-link restoration before `ln -sfn`.
        link_restore_armed = True
        if current.is_symlink() or current.exists():
            current.unlink()
        inject("during_current_link_swap", failure_step)
        current.symlink_to(release_dir)
        inject("after_current_link_swap", failure_step)
    except InjectedFailure:
        failed = True
        if stage.exists():
            shutil.rmtree(stage)
        if release_replaced and release_dir.exists():
            shutil.rmtree(release_dir)
        if previous_armed and previous.exists():
            if release_dir.exists():
                shutil.rmtree(release_dir)
            previous.rename(release_dir)
        if link_restore_armed:
            if current.is_symlink() or current.exists():
                current.unlink()
            if captured_target is not None and captured_target.is_dir():
                current.symlink_to(captured_target)

    if failure_step is None:
        assert not failed
        assert current.resolve(strict=True) == release_dir.resolve(strict=True)
        assert marker(release_dir) == "new"
        if previous.exists():
            shutil.rmtree(previous)
        return

    assert failed, f"failure step was not reached: {failure_step}"
    if initial_release is None:
        assert not current.exists() and not current.is_symlink()
        assert not release_dir.exists()
    else:
        expected = releases / initial_version
        assert expected.is_dir()
        assert current.resolve(strict=True) == expected.resolve(strict=True)
        assert marker(expected) == "old"


def assert_installer_order(installer: Path) -> None:
    text = installer.read_text(encoding="utf-8")
    capture = text.index(
        "CURRENT_LINK_PREVIOUS=$(readlink -f /opt/allscan-reimagined/current"
    )
    move_previous = text.index('mv "$RELEASE_DIR" "$RELEASE_PREVIOUS"')
    arm_previous = text.index("RELEASE_PREVIOUS_ARMED=1", move_previous)
    arm_release = text.index("RELEASE_REPLACED=1", move_previous)
    install_release = text.index('mv "$RELEASE_STAGE" "$RELEASE_DIR"', move_previous)
    arm_link = text.index("CURRENT_LINK_CHANGED=1", install_release)
    swap_link = text.index(
        'ln -sfn "$RELEASE_DIR" /opt/allscan-reimagined/current', install_release
    )
    assert capture < move_previous
    assert move_previous < arm_previous < arm_release < install_release
    assert install_release < arm_link < swap_link
    assert (
        'if [ "$RELEASE_PREVIOUS_ARMED" -eq 1 ] && '
        '[ -d "$RELEASE_PREVIOUS" ]; then'
    ) in text
    assert "trap 'rollback_on_error 130' INT TERM" in text
    disarm = text.index("trap - ERR INT TERM", swap_link)
    cleanup = text.index('rm -rf "$RELEASE_PREVIOUS"', swap_link)
    assert disarm < cleanup
    assert 'mv "$WEB_ROOT/allscan-old" "$ALLSCAN_OLD_BACKUP"' in text
    assert "station-map-cache.json" in text
    assert '--exclude="$BACKUP_WEB_NAME/astdb.txt.*"' in text
    assert 'if [ "$ASR_WEB_WAS_PRESENT" -eq 0 ]; then' in text
    assert '--exclude="$BACKUP_WEB_NAME/astdb.txt"' in text
    assert "restore_reapply_unit_states" in text
    assert "restore_prior_reapply_units" in text
    assert "PRIOR_ASR_CAN_REAPPLY=1" in text
    assert "PRIOR_ASR_WIRING_VALID=1" in text
    assert '"$BACKUP_DIR/runtime/systemd"' in text
    assert "allscan-reimagined-reapply.path" in text
    assert "allscan-reimagined-reapply.timer" in text
    assert "allscan-reimagined-ysf-net-live.service" in text
    assert "allscan-reimagined-ysf-bridge-control" in text
    assert 'php "$RELEASE_DIR/scripts/asr-bridge-clients.php" --self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-ysf-bridge-control.py" --self-test' in text
    assert 'php "$RELEASE_DIR/scripts/asr-settings-bridge-self-test.php"' in text
    assert 'php "$RELEASE_DIR/scripts/asr-echolink-self-test.php"' in text
    assert 'php -l "$ASR_WEB_DIR/astapi/AMI.php"' in text
    assert 'php -l "$ASR_WEB_DIR/astapi/asrEchoLink.php"' in text
    assert 'php -l "$ASR_WEB_DIR/include/asrBridgeStatus.php"' in text
    assert 'php "$RELEASE_DIR/scripts/asr-bridge-status-privacy-self-test.php"' in text
    assert 'sh -n /usr/local/sbin/allscan-reimagined-asterisk-read' in text
    assert 'allscan-reimagined-asterisk-read --self-test' in text
    assert 'allscan-reimagined-asterisk-read echolink-nodes' in text
    assert 'scripts/asr-asterisk-read.sh:/usr/local/sbin/allscan-reimagined-asterisk-read' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-p25-bridge-control.py" self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-nxdn-bridge-control.py" self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-m17-bridge-control.py" --self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-m17-usrp-connector.py" --self-test' in text
    assert "allscan-reimagined-m17-bridge@.service" in text
    assert "allscan-reimagined-p25-bridge-status.service" in text
    assert "allscan-reimagined-nxdn-bridge-status.service" in text
    assert "allscan-reimagined-fixed-bridge-recovery.service" in text
    assert "allscan-reimagined-fixed-bridge-recovery.timer" in text
    assert "FIXED_RECOVERY_TIMER_WAS_ENABLED" in text
    assert "FIXED_RECOVERY_TIMER_WAS_ACTIVE" in text
    assert "allscan-reimagined-fixed-bridge-recovery.timer 2>/dev/null" in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-fixed-bridge-recovery.py" --self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-bridge-lifecycle.py" self-test' in text
    assert 'python3 "$RELEASE_DIR/scripts/asr-startup-bridge-summary.py" --self-test' in text
    assert "allscan-reimagined-startup-bridge-summary.service" in text
    assert "allscan-reimagined-bridge-lifecycle" in text
    assert "remove_asr_managed_wiring" in text
    assert "allscan-reimagined-friendly-names.conf" in text
    assert (
        'if [ "$PRIOR_ASR_WIRING_VALID" -eq 1 ]; then\n'
        "      restore_prior_reapply_units"
    ) in text
    assert "Removed orphaned ASR persistence wiring" in text
    state_capture = text.index("REAPPLY_STATES_CAPTURED=1")
    backup_creation = text.index('install -d -o root -g root -m 700 "$BACKUP_DIR"')
    assert state_capture < backup_creation
    assert 'if [ "$CHANGES_STARTED" -eq 1 ]; then\n    remove_asr_managed_wiring' in text
    assert (
        'bash "$CURRENT_LINK_PREVIOUS/scripts/asr-reapply.sh" >/dev/null 2>&1 || true'
        not in text
    )
    assert "monitoring is disabled." in text
    assert 'validate_command "release-check timer is active"' in text
    assert 'validate_command "initial release check"' in text
    assert "Initial release status is pending; the daily timer will retry automatically." in text
    stock_policy_check = text.index('validate_command "stock /allscan access policy"')
    assert text.index("new CfgModel($db);", stock_policy_check) > stock_policy_check
    assert "Validation failed: /asr runtime-config endpoint" in text


def assert_lifecycle_reapply_contract(reapply: Path, integrity: Path) -> None:
    text = reapply.read_text(encoding="utf-8")
    assert '[ "$ROLLBACK_MODE" != "1" ] && [ "${ASR_INSTALL_LOCK_HELD:-0}" != "1" ]' in text
    assert "bridge-deletion-queue" in text and "bridge-creation-intents" in text
    assert "allscan-reimagined-bridge-lifecycle queue-deletion" in text
    assert "User=asterisk" in text and "Group=asterisk" in text
    assert "Wants=network-online.target allscan-reimagined-fixed-bridge-recovery.service" in text
    assert "ReadWritePaths=/usr/share/asterisk/sounds/en/custom/allscan-reimagined" in text
    integrity_text = integrity.read_text(encoding="utf-8")
    assert "bridge-deletion-queue/*.json" in integrity_text
    assert "ASR_ROLLBACK_MODE" in integrity_text
    assert "allscan-reimagined-bridge-lifecycle reconcile" in integrity_text
    assert "astapi/asrEchoLink.php" in integrity_text
    assert "astapi/AMI.php" in integrity_text
    assert "include/asrBridgeStatus.php" in integrity_text
    assert "allscan-reimagined-asterisk-read" in integrity_text
    assert "[ ! -L /usr/local/sbin/allscan-reimagined-asterisk-read ]" in integrity_text
    assert (
        "stat -c '%U:%G:%a:%h' /usr/local/sbin/allscan-reimagined-asterisk-read"
        in integrity_text
    )
    assert '"root:root:755:1"' in integrity_text
    assert "[ ! -L /etc/sudoers.d/allscan-reimagined ]" in integrity_text
    assert "stat -c '%U:%G:%a:%h' /etc/sudoers.d/allscan-reimagined" in integrity_text
    assert '"root:root:440:1"' in integrity_text


def exercise_migration_failure(root: Path) -> None:
    web = root / "www"
    backup = root / "backup"
    protected = root / "etc"
    web.mkdir()
    backup.mkdir()
    protected.mkdir()
    stock = web / "allscan"
    old_alias = web / "allscan-old"
    stock.mkdir()
    old_alias.mkdir()
    (stock / "marker.txt").write_text("legacy-overlay\n", encoding="utf-8")
    (old_alias / "marker.txt").write_text("original-old\n", encoding="utf-8")
    (protected / "config.json").write_text("original-config\n", encoding="utf-8")
    (protected / "allscan.db").write_text("original-db\n", encoding="utf-8")

    migrated = backup / "migrated-allscan-overlay"
    preserved_old = backup / "preserved-allscan-old"
    stock.rename(migrated)
    old_alias.rename(preserved_old)
    shutil.copy2(protected / "config.json", backup / "config.json")
    shutil.copy2(protected / "allscan.db", backup / "allscan.db")

    stock.mkdir()
    (stock / "marker.txt").write_text("new-stock\n", encoding="utf-8")
    (web / "asr").mkdir()
    (protected / "config.json").write_text("changed-config\n", encoding="utf-8")
    (protected / "allscan.db").write_text("changed-db\n", encoding="utf-8")

    shutil.rmtree(stock)
    migrated.rename(stock)
    shutil.rmtree(web / "asr")
    preserved_old.rename(old_alias)
    shutil.copy2(backup / "config.json", protected / "config.json")
    shutil.copy2(backup / "allscan.db", protected / "allscan.db")

    assert marker(stock) == "legacy-overlay"
    assert marker(old_alias) == "original-old"
    assert not (web / "asr").exists()
    assert (protected / "config.json").read_text(encoding="utf-8") == "original-config\n"
    assert (protected / "allscan.db").read_text(encoding="utf-8") == "original-db\n"


def exercise_live_schema1_cleanup(root: Path) -> None:
    web = root / "www"
    managed = root / "managed"
    previous = root / "previous"
    web.mkdir()
    managed.mkdir()
    previous.mkdir()
    (web / "allscan").mkdir()
    # Live-derived baseline: legacy overlay had no separate /asr and no
    # release-check/rollback capabilities.
    prior_files = {
        "reapply.path": "legacy path\n",
        "reapply.timer": "legacy timer\n",
        "friendly-names.cron": "legacy cron\n",
    }
    for name, payload in prior_files.items():
        (previous / name).write_text(payload, encoding="utf-8")
        shutil.copy2(previous / name, managed / name)
    prior_states = {
        "reapply.path": (True, False),
        "reapply.timer": (False, True),
    }

    # Simulate incomplete artifacts left by an interrupted older migration.
    (web / "asr").mkdir()
    for name in (
        "release-check.service",
        "release-check.timer",
        "rollback@.service",
        "reapply.path",
        "reapply.timer",
        "friendly-names.cron",
    ):
        (managed / name).write_text("new\n", encoding="utf-8")
    current_states = {
        "reapply.path": (False, True),
        "reapply.timer": (False, True),
    }

    # Model rollback: absent marker removes /asr, all managed wiring is
    # cleared, only prior-release wiring returns, then exact unit state returns.
    shutil.rmtree(web / "asr")
    for child in tuple(managed.iterdir()):
        child.unlink()
    for name in prior_files:
        shutil.copy2(previous / name, managed / name)
    current_states = dict(prior_states)

    assert not (web / "asr").exists()
    assert sorted(path.name for path in managed.iterdir()) == sorted(prior_files)
    assert not (managed / "release-check.service").exists()
    assert not (managed / "rollback@.service").exists()
    assert current_states == prior_states


def exercise_installer_webroot_backup_exclusions(
    root: Path, webroot_name: str
) -> None:
    web_root = root / "www"
    webroot = web_root / webroot_name
    webroot.mkdir(parents=True)
    (webroot / "index.html").write_text("existing webroot\n", encoding="utf-8")
    (webroot / "favorites.ini").symlink_to("/etc/allscan/favorites.ini")
    (webroot / "astdb.txt").symlink_to("/var/lib/asterisk/astdb.txt")
    (webroot / "astdb.txt.before-local-labels").symlink_to(
        "/var/lib/asterisk/astdb.txt"
    )
    (webroot / "astdb.txt.bak-local-labels-20260723-173659").symlink_to(
        "/var/lib/asterisk/astdb.txt"
    )

    archive_path = root / f"{webroot_name}-webroot.tar.gz"
    subprocess.run(
        [
            "tar",
            f"--exclude={webroot_name}/astdb.txt",
            f"--exclude={webroot_name}/astdb.txt.*",
            "-czf",
            str(archive_path),
            "-C",
            str(web_root),
            webroot_name,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    with tarfile.open(archive_path, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
    assert f"{webroot_name}/index.html" in members
    assert f"{webroot_name}/favorites.ini" in members
    assert members[f"{webroot_name}/favorites.ini"].issym()
    assert (
        members[f"{webroot_name}/favorites.ini"].linkname
        == "/etc/allscan/favorites.ini"
    )
    assert not any(
        name.startswith(f"{webroot_name}/astdb.txt") for name in members
    )


def exercise_orphan_wiring_cleanup(root: Path) -> None:
    managed = root / "managed"
    managed.mkdir()
    for name in (
        "allscan-reimagined-reapply.service",
        "allscan-reimagined-reapply.path",
        "allscan-reimagined-reapply.timer",
        "allscan-reimagined-friendly-names.conf",
    ):
        (managed / name).write_text("orphaned\n", encoding="utf-8")

    # Model rollback with no valid prior release and no recognized legacy
    # overlay: exact ASR-managed persistence wiring must stay removed.
    for child in tuple(managed.iterdir()):
        child.unlink()

    assert not tuple(managed.iterdir())


def self_test(*, model_only: bool = False) -> None:
    if not model_only:
        script_path = Path(__file__).resolve()
        layout_candidates = [
            (
                script_path.parents[1] / "install.sh",
                script_path.parents[1] / "scripts",
            ),
            (
                script_path.parents[2] / "install.sh",
                script_path.parent,
            ),
        ]
        installer, scripts_dir = next(
            (
                (candidate_installer, candidate_scripts)
                for candidate_installer, candidate_scripts in layout_candidates
                if candidate_installer.is_file()
                and (candidate_scripts / "asr-reapply.sh").is_file()
                and (candidate_scripts / "asr-integrity-check.sh").is_file()
            ),
            layout_candidates[0],
        )
        assert_installer_order(installer)
        assert_lifecycle_reapply_contract(
            scripts_dir / "asr-reapply.sh",
            scripts_dir / "asr-integrity-check.sh",
        )
    cases = (
        ("1.0.0-beta.5.11", "1.0.0-beta.5.11", "after_previous_move"),
        ("1.0.0-beta.5.11", "1.0.0-beta.5.11", "before_release_install"),
        ("1.0.0-beta.5.11", "1.0.0-beta.5.11", "after_release_install"),
        ("1.0.0-beta.5.11", "1.0.0-beta.5.11", "during_current_link_swap"),
        ("1.0.0-beta.6", "1.0.0-beta.6.1", "after_current_link_swap"),
        (None, "1.0.0-beta.6.1", "after_release_install"),
        (None, "1.0.0-beta.6.1", "before_release_install"),
        (None, "1.0.0-beta.6.1", "during_current_link_swap"),
        ("1.0.0-beta.5.11", "1.0.0-beta.5.11", None),
        ("1.0.0-beta.6", "1.0.0-beta.6.1", None),
    )
    for initial, new, failure in cases:
        with tempfile.TemporaryDirectory(
            prefix="asr-installer-rollback-self-test."
        ) as temporary:
            exercise_release_swap(
                Path(temporary),
                initial_version=initial,
                new_version=new,
                failure_step=failure,
            )
    with tempfile.TemporaryDirectory(
        prefix="asr-installer-migration-failure-self-test."
    ) as temporary:
        exercise_migration_failure(Path(temporary))
    with tempfile.TemporaryDirectory(
        prefix="asr-installer-live-schema1-failure-self-test."
    ) as temporary:
        exercise_live_schema1_cleanup(Path(temporary))
    with tempfile.TemporaryDirectory(
        prefix="asr-installer-legacy-overlay-backup-self-test."
    ) as temporary:
        exercise_installer_webroot_backup_exclusions(Path(temporary), "allscan")
    with tempfile.TemporaryDirectory(
        prefix="asr-installer-schema2-backup-self-test."
    ) as temporary:
        exercise_installer_webroot_backup_exclusions(Path(temporary), "asr")
    with tempfile.TemporaryDirectory(
        prefix="asr-installer-orphan-wiring-self-test."
    ) as temporary:
        exercise_orphan_wiring_cleanup(Path(temporary))
    print("ASR installer rollback self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="Run temp-only regression tests"
    )
    parser.add_argument(
        "--model-only",
        action="store_true",
        help="Skip the source-order contract when install.sh is not packaged",
    )
    args = parser.parse_args()
    if not args.self_test:
        parser.error("this helper only supports --self-test")
    self_test(model_only=args.model_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
