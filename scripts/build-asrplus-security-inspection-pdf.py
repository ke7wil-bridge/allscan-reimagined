#!/usr/bin/env python3
"""Build the authoritative ASR Plus security inspection PDF."""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)


NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#1769AA")
CYAN = colors.HexColor("#2CB1BC")
PALE = colors.HexColor("#EAF4FA")
INK = colors.HexColor("#243B53")
MUTED = colors.HexColor("#627D98")
GREEN = colors.HexColor("#16794B")
AMBER = colors.HexColor("#9A6700")
RED = colors.HexColor("#B42318")
LINE = colors.HexColor("#BCCCDC")


def para(text: str, style):
    return Paragraph(text, style)


def bullets(items: list[str], style):
    output = []
    for item in items:
        output.extend([Paragraph(f"- {item}", style), Spacer(1, 3)])
    return output


def table(rows, widths, styles):
    converted = [[cell if hasattr(cell, "wrap") else Paragraph(str(cell), styles["TableCell"])
                  for cell in row] for row in rows]
    result = Table(converted, colWidths=widths, repeatRows=1, hAlign="LEFT")
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return result


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleASR", parent=styles["Title"], fontName="Helvetica-Bold",
                              fontSize=25, leading=29, textColor=NAVY, alignment=TA_LEFT,
                              spaceAfter=12))
    styles.add(ParagraphStyle(name="SubtitleASR", parent=styles["Normal"], fontSize=12,
                              leading=17, textColor=BLUE, spaceAfter=16))
    styles.add(ParagraphStyle(name="H1ASR", parent=styles["Heading1"], fontName="Helvetica-Bold",
                              fontSize=17, leading=21, textColor=NAVY, spaceBefore=6,
                              spaceAfter=9, keepWithNext=True))
    styles.add(ParagraphStyle(name="H2ASR", parent=styles["Heading2"], fontName="Helvetica-Bold",
                              fontSize=12, leading=15, textColor=BLUE, spaceBefore=8,
                              spaceAfter=5, keepWithNext=True))
    styles.add(ParagraphStyle(name="BodyASR", parent=styles["BodyText"], fontName="Helvetica",
                              fontSize=9.2, leading=13, textColor=INK, spaceAfter=6))
    styles.add(ParagraphStyle(name="BulletASR", parent=styles["BodyText"], fontSize=8.9,
                              leading=12.5, leftIndent=10, firstLineIndent=-8, textColor=INK))
    styles.add(ParagraphStyle(name="TableCell", parent=styles["BodyText"], fontSize=7.6,
                              leading=10, textColor=INK))
    styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"], fontSize=10,
                              leading=14, textColor=NAVY, backColor=PALE, borderColor=CYAN,
                              borderWidth=1, borderPadding=9, spaceBefore=5, spaceAfter=10))
    styles.add(ParagraphStyle(name="Status", parent=styles["BodyText"], fontName="Helvetica-Bold",
                              fontSize=10, leading=14, textColor=GREEN, spaceAfter=5))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.5,
                              leading=10, textColor=MUTED))

    def header_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(CYAN)
        canvas.setLineWidth(1.2)
        canvas.line(0.65 * inch, 10.35 * inch, 7.85 * inch, 10.35 * inch)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(0.65 * inch, 10.48 * inch, "ASR PLUS SECURITY INSPECTION")
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(7.85 * inch, 10.48 * inch, "Issues #252 - #254 | 2026-11-23")
        canvas.line(0.65 * inch, 0.55 * inch, 7.85 * inch, 0.55 * inch)
        canvas.drawString(0.65 * inch, 0.35 * inch, "Review package - no deployment authorization")
        canvas.drawRightString(7.85 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(output), pagesize=letter, rightMargin=0.65 * inch, leftMargin=0.65 * inch,
        topMargin=0.8 * inch, bottomMargin=0.7 * inch,
        title="ASR Plus Security Completion and Texas Readiness",
        author="Netoholics / AllScan Reimagined",
        subject="Issues 252, 253 and 254 security inspection package",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="inspection", frames=[frame], onPage=header_footer)])

    story = [
        Spacer(1, 0.42 * inch),
        para("ASR Plus", styles["Status"]),
        para("Security Completion and Texas Readiness", styles["TitleASR"]),
        para("Authoritative inspection package for Kentucky 674982, KY2 674980, Phoenix 641894, and planned Texas 674984", styles["SubtitleASR"]),
        para("DECISION: CONDITIONAL - KEEP OPTIONAL MODE", styles["Callout"]),
        para("The authenticated command stack is healthy and source-parity verified on the three current hubs. Required mode is not ready for activation until KY2's control service receives and passes the prepared sandbox hardening. Kentucky API tokens are complete in source but not installed. Texas remains design-only and was not contacted.", styles["BodyASR"]),
        Spacer(1, 8),
        table([
            ["Control boundary", "Current state", "Decision"],
            ["Command signing", "Installed on Kentucky, KY2 and Phoenix; optional mode", "Healthy; required cutover needs separate approval"],
            ["API tokens", "Implemented and tested in source; absent from production", "Kentucky-only install transaction required"],
            ["Texas", "Required-mode config and checklist prepared", "No contact or install; operator and fixture gates open"],
        ], [1.45 * inch, 2.75 * inch, 2.8 * inch], styles),
        Spacer(1, 12),
        para("Inspection basis", styles["H2ASR"]),
        para("Issues #252, #253 and #254; related #134; merged foundation 4b43851; origin/main e9641b7; local PR #266 correction 918a995. Production activity was read-only. No service, queue, credential, firewall, radio link, token package, push or merge was changed.", styles["Small"]),
        PageBreak(),

        para("1. Production security posture", styles["H1ASR"]),
        table([
            ["Node", "Installed state", "Signing", "Readiness"],
            ["Kentucky 674982", "Central control, endpoint and integrity module match reviewed source; queues empty; forced SSH identities restricted", "Phoenix and KY2 optional", "Remain optional; tokens and required mode unapproved"],
            ["KY2 674980", "Canonical ky2 agent, separate key, strict SSH and durable ledger healthy", "Optional", "Blocked on control-service sandbox qualification"],
            ["Phoenix 641894", "Canonical phoenix agent, separate key, strict SSH, hardened unit and durable ledger healthy", "Optional", "Ready for approved config-only cutover after KY2 gate"],
            ["Texas 674984", "Repository configuration only", "Required from first install", "Not install-ready; no host contact"],
        ], [1.05 * inch, 2.8 * inch, 1.1 * inch, 2.05 * inch], styles),
        Spacer(1, 10),
        para("Trust evidence", styles["H2ASR"]),
        *bullets([
            "KY2 and Phoenix independently pin Kentucky ED25519 fingerprint SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs.",
            "Per-hub HMAC keys are root-owned mode 0600. Key contents were never read or logged.",
            "Remote ledgers are persistent and root-only. Historical audit identity is preserved, while active routing uses ky2.",
            "Asterisk and dashboard telemetry remained healthy. The unrelated retired endpoint was not contacted.",
        ], styles["BulletASR"]),
        para("Production token discrepancy", styles["H2ASR"]),
        Spacer(1, 4),
        para("MasterHub still serves the session-only API. The token helper, token sudoers rule, protected token store and token audit log are absent. Tokens are therefore a tested source capability, not an installed production control.", styles["Callout"]),
        PageBreak(),

        para("2. Command-integrity controls", styles["H1ASR"]),
        table([
            ["Threat", "Protection", "Result"],
            ["Forged or modified command", "Per-hub HMAC-SHA-256, canonical digest, key ID and hub/node binding", "Rejected"],
            ["Replay or duplicate execution", "Nonce, expiry, one lease and durable execute-once ledger", "Exact retry idempotent; changed replay rejected"],
            ["Forged acknowledgment", "Signed result bound to exact command ID and digest", "Forgery/conflict rejected"],
            ["Cross-hub execution", "Forced SSH identity, isolated path, signed hub/node and separate key", "Rejected"],
            ["Stale recovery", "Maximum validity window, quarantine/archive and fail-closed sync", "No stale execution"],
            ["Queue saturation/tampering", "Bounded depth, verification before lease and atomic files", "Rejected or quarantined"],
            ["Unsafe signing key", "Regular-file, owner-only and minimum 256-bit checks", "Missing, short, group-readable and symlink keys rejected"],
        ], [1.45 * inch, 4.15 * inch, 1.4 * inch], styles),
        Spacer(1, 10),
        para("Least-privilege model", styles["H2ASR"]),
        *bullets([
            "One canonical identity, node mapping, HMAC key, SSH identity, forced endpoint and isolated state path per hub.",
            "No shell, PTY, forwarding, arbitrary command, service restart or software update through the remote protocol.",
            "Dashboard automation uses Kentucky-issued bearer tokens only; no SSH keys for AI agents.",
            "Texas begins telemetry-only and required, with control permissions enabled separately after qualification.",
        ], styles["BulletASR"]),
        PageBreak(),

        para("3. API token security", styles["H1ASR"]),
        para("The implementation defaults to status:read, requires explicit authorized hubs and expiry, stores only SHA-256 secret digests, supports immediate revocation, enforces HTTPS, and audits successful and denied authentication without recording bearer values.", styles["BodyASR"]),
        table([
            ["Operation", "Permission", "Scope"],
            ["Status", "status:read", "Exactly one authorized hub per token request"],
            ["Connect / permanent connect", "control:connect", "Explicit grant"],
            ["Disconnect", "control:disconnect", "Explicit grant"],
            ["Ban", "control:ban", "Explicit grant"],
            ["Unban", "control:unban", "Explicit grant"],
            ["Ban list/result", "Not exposed to bearer tokens", "Session/control qualification path only"],
        ], [2.0 * inch, 2.0 * inch, 3.0 * inch], styles),
        Spacer(1, 10),
        para("Kentucky install transaction", styles["H2ASR"]),
        *bullets([
            "Back up API and sudoers state; validate the exact approved commit and file hashes.",
            "Install API, root-owned helper, authentication-only sudoers rule, protected store and audit rotation together.",
            "Validate PHP and sudoers syntax, TLS detection, default read-only scope, node isolation and permission denial.",
            "Create a short-lived read-only test token; test expiry and revocation; remove it after evidence capture.",
            "On failure, restore the whole session-only API set. Never leave a partial token stack.",
        ], styles["BulletASR"]),
        Spacer(1, 4),
        para("Tokens exist centrally on Kentucky only. Phoenix, KY2 and Texas do not receive a bearer-token store or token-authentication sudo rule.", styles["Callout"]),
        PageBreak(),

        para("4. Required-mode activation", styles["H1ASR"]),
        para("Optional mode is a compatibility window and accepts bounded unsigned legacy work. It is not the final security posture.", styles["Callout"]),
        table([
            ["Stage", "Action", "Acceptance"],
            ["Preflight", "Approve window; back up configs/unit state; confirm empty queues, NTP, fresh telemetry, key-hash parity and unchanged links", "All gates recorded; no secret output"],
            ["KY2 sandbox", "Install reviewed control-service drop-in; reload systemd; let existing timer invoke", "Telemetry, watcher, polling, ledger and links unchanged"],
            ["Remote required", "Change Phoenix, then KY2 agent mode only", "Signed read-only ban-list succeeds independently"],
            ["Central required", "Change Kentucky Phoenix/KY2 hub modes only", "Signed command and ack, empty queue, fresh dashboard and audit continuity"],
        ], [1.15 * inch, 3.8 * inch, 2.05 * inch], styles),
        Spacer(1, 9),
        para("Rollback", styles["H2ASR"]),
        *bullets([
            "Stop new controls and preserve queue, inflight, result, archive, audit and ledger evidence.",
            "Return Kentucky's affected hub to optional, then return the affected agent to its backed-up optional config.",
            "Restore KY2 unit state if its sandbox caused the regression; reload systemd only. Never clear the ledger.",
            "Verify telemetry, queue depth, signed read-only lifecycle, audit continuity and unchanged Asterisk links.",
        ], styles["BulletASR"]),
        PageBreak(),

        para("5. Texas 674984 readiness", styles["H1ASR"]),
        table([
            ["Boundary", "Requirement"],
            ["VM", "Dedicated workload, fixed resources, provider console, pre-install snapshot, patched OS"],
            ["Network", "Deny unsolicited inbound; outbound TCP 22 to Kentucky; management allowlist; local-only AMI; mode-specific ports only after review"],
            ["Identity", "New texas HMAC and SSH keys; strict pinned Kentucky host key; source-restricted forced endpoint; no AI SSH keys"],
            ["Service", "Required mode, isolated state and ledger, hardened systemd; dedicated account feasibility before install"],
            ["Integration", "Telemetry-only first; no production radio link; supervised test and incremental controls later"],
        ], [1.25 * inch, 5.75 * inch], styles),
        Spacer(1, 8),
        para("Failure and capacity qualification", styles["H2ASR"]),
        para("Use a disposable Texas fixture only. Record VM size, commit, latency, CPU, RSS, file growth, queue depth and recovery. Exercise Kentucky outage, DNS failure, clock skew, packet loss, duplicate delivery, lost acknowledgment, restart, stale inflight work, malformed payload, disk-full writes and queue saturation. Require no duplicate, cross-hub or expired execution; retries must be bounded and ledger/audit history intact.", styles["BodyASR"]),
        para("Operator agreements still required", styles["H2ASR"]),
        *bullets([
            "Node ownership, approved operators, incident contacts, maintenance windows and credential revocation authority.",
            "Log retention, evidence sharing, outage notification, provider cost/jurisdiction and backup ownership.",
            "Capacity limits, abuse thresholds, isolation authority, permanent-link policy and control permissions.",
        ], styles["BulletASR"]),
        PageBreak(),

        para("6. Validation and remaining risks", styles["H1ASR"]),
        para("ALL 12 ASR PLUS SELF-TEST ENTRYPOINTS PASSED", styles["Status"]),
        para("Coverage includes signed command/ack lifecycle, replay and expiry, cross-hub rejection, duplicate acknowledgment, queue recovery and saturation, Kentucky outage, scoped token authorization, Phoenix/KY2 operating-mode compatibility, KY2 identity/watcher behavior, directory permissions, staged manifest and Texas parity. Python compilation, PHP syntax, JSON validation and composed systemd drop-in verification passed. No production load or failure injection was run.", styles["BodyASR"]),
        table([
            ["Remaining risk", "Disposition"],
            ["Optional downgrade window", "Close only through approved staged required-mode cutover"],
            ["KY2 root service sandbox gap", "Prepared drop-in; production qualification required"],
            ["Token stack absent", "Separate Kentucky installation approval"],
            ["Remote agents run as root", "Dedicated-account feasibility required for Texas; later migration for existing hubs"],
            ["Kentucky single dependency", "Dashboard/control fail stale and closed; no automatic regional failover"],
            ["Texas operational policy incomplete", "KJ5IRQ agreements and isolated qualification required"],
        ], [2.05 * inch, 4.95 * inch], styles),
        PageBreak(),

        para("7. Approval statement", styles["H1ASR"]),
        para("No approval is needed for the completed local development and read-only audit.", styles["Status"]),
        Spacer(1, 4),
        para("Before the next live step, Josh must explicitly approve KY2 674980 installation of the named systemd drop-in and systemd reload, followed by config-only optional-to-required changes on Phoenix 641894, KY2 674980 and Kentucky 674982, including supervised signed read-only ban-list checks.", styles["Callout"]),
        para("The approval must name the maintenance window, backup locations, affected files, expected timer/service effects, verification and rollback. It must explicitly confirm no Asterisk restart or radio-link change.", styles["BodyASR"]),
        para("Not authorized by that approval", styles["H2ASR"]),
        *bullets([
            "Kentucky API token installation or issuance.",
            "Texas contact, installation, firewall changes, radio linking or control enablement.",
            "Asterisk restart, production radio-link change, push, merge or required-mode activation beyond the named nodes and window.",
        ], styles["BulletASR"]),
        Spacer(1, 12),
        para("Supporting technical record", styles["H2ASR"]),
        para("Repository files: asrplus-security-audit-253.md; asrplus-security-activation-gates.md; asrplus-live-deployment-inventory-2026-11-23.md; asrplus-command-integrity-test-report.md; texas-hub-674984-readiness-254.md; asrplus-security-inspection-evidence-2026-11-23.json.", styles["Small"]),
        Spacer(1, 18),
        para("Prepared for KJ5IRQ review. This package is evidence and a staged plan; it is not deployment authorization.", styles["Callout"]),
    ]
    doc.build(story)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
