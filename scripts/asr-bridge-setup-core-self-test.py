#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("setup_core", HERE / "asr-bridge-setup-core.py")
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


def fake_asl3(root: Path) -> None:
    etc = root / "etc"
    ast = etc / "asterisk"
    ast.mkdir(parents=True)
    (etc / "os-release").write_text("ID=debian\n")
    (ast / "rpt.conf").write_text("[641890]\nfoo=bar\n[1001]\n")
def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake_asl3(root)
        adapter = core.ASL3Adapter(root)
        facts = adapter.detect()
        assert facts.platform == "asl3"
        assert facts.node_numbers == (641890, 1001)
        assert core.allocate_node(facts.node_numbers) == 1002
        assert core.allocate_ports({17100, 17102}, 2, 17100, 17104) == (17101, 17103)
        m17_ports = core.derived_m17_ports("m17_wil")
        assert len(m17_ports) == 3 and m17_ports[1] == m17_ports[0] + 1
        assert m17_ports[2] == m17_ports[0] + 2
        m17_plan = core.m17_plan(facts, "m17_wil", "KE7WIL")
        assert m17_plan.bridge_type == "m17"
        assert any("m17_wil" in action.target for action in m17_plan.actions)
        collision_facts = core.HostFacts(
            facts.platform, facts.asterisk_config_dir, facts.rpt_config,
            facts.node_numbers, facts.systemd, facts.docker, facts.docker_compose,
            facts.hostname, (), (m17_ports[0],),
        )
        try:
            core.m17_plan(collision_facts, "m17_wil", "KE7WIL")
            raise AssertionError("M17 port collision accepted")
        except core.ProvisioningError:
            pass

        plan = core.make_plan(
            facts, "m17", "m17_wil",
            [core.PlanAction("create", "/etc/asr/m17_wil.json", "create bridge config")],
        )
        adapter.validate_plan(plan)
        assert len(plan.digest()) == 64
        bad = core.make_plan(
            facts, "m17", "bad",
            [core.PlanAction("modify", facts.rpt_config, "overwrite rpt.conf")],
        )
        try:
            adapter.validate_plan(bad)
            raise AssertionError("protected path accepted")
        except core.ProvisioningError:
            pass

        duplicate = core.make_plan(
            facts, "m17", "dup",
            [
                core.PlanAction("create", "/etc/asr/dup", "first"),
                core.PlanAction("create", "/etc/asr/dup", "second"),
            ],
        )
        try:
            adapter.validate_plan(duplicate)
            raise AssertionError("duplicate action target accepted")
        except core.ProvisioningError:
            pass

        state = []
        tx = core.Transaction()
        tx.step(lambda: state.append("a"), lambda: state.remove("a"))
        tx.step(lambda: state.append("b"), lambda: state.remove("b"))
        assert state == ["a", "b"]
        assert tx.rollback() == []
        assert state == []

    print("bridge setup core self-test: PASS")


if __name__ == "__main__":
    main()
