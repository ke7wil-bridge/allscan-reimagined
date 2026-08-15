<?php
// AllScan Reimagined operator instructions
require_once('../include/common.php');
$html = new Html();
$msg = [];

asInit($msg);
asrInitAuthenticatedUser($msg);
if(!adminUser())
	asExit('Admin permission required.');
pageInit();
?>
<main class="asr-instructions-page">
	<header class="asr-instructions-intro">
		<p class="asr-instructions-eyebrow">AllScan Reimagined</p>
		<h1>Help &amp; Instructions</h1>
		<p>This is the complete guide to the ASR dashboard, Reimagined Settings, bridge cards, updates, and recovery tools.</p>
	</header>

	<nav class="asr-instructions-jump" aria-label="Instruction topics">
		<a href="#getting-started">Getting Started</a>
		<a href="#dashboard-controls">Dashboard &amp; Favorites</a>
		<a href="#appearance-access">Appearance &amp; Access</a>
		<a href="#bridge-cards">Bridge Cards</a>
		<a href="#bridge-setup">Bridge Setup</a>
		<a href="#dmr-net-bridge">DMR Net Bridge</a>
		<a href="#ysf-net-bridge">YSF Net Bridge</a>
		<a href="#next-digital-bridges">P25, NXDN, and M17</a>
		<a href="#lookup-map">Lookup &amp; Map</a>
		<a href="#updates">Update Notices</a>
		<a href="#rollback">Rollback</a>
		<a href="#diagnostics">Diagnostics &amp; Help</a>
	</nav>

	<section id="getting-started" class="asr-instructions-section">
		<h2>Getting Started</h2>
		<p>Beta 7.3 keeps the original AllScan and AllScan Reimagined side by side:</p>
		<div class="asr-instructions-compare">
			<article>
				<h3>Original AllScan</h3>
				<p><strong>/allscan/</strong> is the stock AllScan interface. ASR does not replace it.</p>
			</article>
			<article>
				<h3>AllScan Reimagined</h3>
				<p><strong>/asr/</strong> is the Reimagined dashboard and its administration pages.</p>
			</article>
		</div>
		<p>Both interfaces use the same AllScan users, Favorites, database, and node settings, but their login sessions are separate. Signing in to one path does not automatically sign you in to the other.</p>
		<p class="asr-instructions-callout"><strong>Saving changes:</strong> Reimagined Settings has Save buttons at the top and bottom. Either button saves the entire page to <strong>/etc/allscan-reimagined/config.json</strong>. Rollback uses its own button and is never started by Save.</p>
	</section>

	<section id="dashboard-controls" class="asr-instructions-section">
		<h2>Dashboard, Node Controls &amp; Favorites</h2>
		<div class="asr-instructions-topic-grid">
			<article>
				<h3>Node Controls</h3>
				<p>Enter an AllStar node number, then use Connect or Disconnect. Disconnect-before-Connect is remembered by that browser and device in both its checked and unchecked states.</p>
			</article>
			<article>
				<h3>Connection Status</h3>
				<p>Selecting a public connected node copies its number into Node Controls. Private bridge numbers from 1000 through 1999 stay protected from public-node lookup behavior.</p>
			</article>
			<article>
				<h3>Favorites</h3>
				<p>One click selects a Favorite and closes the menu. It does not connect automatically. Add and Delete Favorite remain separate actions, and double-click does not send a Connect command.</p>
			</article>
			<article>
				<h3>Timers and Counts</h3>
				<p>TX timers come from authoritative SawStat data. Connection totals combine direct and propagated links without double-counting visible direct nodes.</p>
			</article>
		</div>
	</section>

	<section id="appearance-access" class="asr-instructions-section">
		<h2>Appearance, Access &amp; Power Use</h2>
		<div class="asr-instructions-topic-grid">
			<article>
				<h3>Header</h3>
				<p>The title can use <strong>{CALLSIGN}</strong> and <strong>{NODE}</strong>. The logo can use a local ASR path, an http/https URL, or an uploaded PNG, JPEG, or WebP image under 1 MB.</p>
			</article>
			<article>
				<h3>Require Login</h3>
				<p>When enabled, viewers must sign in before opening the ASR dashboard. Existing users and permissions are retained. Stock <strong>/allscan/</strong> has its own access setting.</p>
			</article>
			<article>
				<h3>Low-Power Node Mode</h3>
				<p>Use this on smaller nodes to reduce background work and disable animated themes. It does not change Asterisk or bridge audio settings.</p>
			</article>
		</div>
	</section>

	<section id="bridge-cards" class="asr-instructions-section">
		<h2>Bridge Cards</h2>
		<p>Bridge Cards tell ASR which already-working bridges to display. A card does not install or configure the underlying digital bridge.</p>
		<dl class="asr-instructions-definitions">
			<div><dt>Digital Mode</dt><dd>Choose DMR, YSF, Zello, P25, NXDN, or M17. The Card Title remains freely editable.</dd></div>
			<div><dt>Card Type</dt><dd>Use Standard Bridge for a fixed path. Use Net Bridge only for a separately installed, isolated path whose destination may be changed.</dd></div>
			<div><dt>Fixed Bridge Recovery</dt><dd>For a Standard Bridge that should always remain linked, enable automatic recovery. ASR checks that the configured bridge node is local, restores only a missing link, and stays out of the way when Asterisk already has a native permanent link. Net Bridges are excluded.</dd></div>
			<div><dt>Node</dt><dd>The AllStar bridge node ASR matches against live connection status. This is not a DMR talkgroup.</dd></div>
			<div><dt>Card Title</dt><dd>The heading displayed on the main ASR bridge card.</dd></div>
			<div><dt>Detail Title</dt><dd>The label above the card’s client or activity details, usually Connected Clients.</dd></div>
			<div><dt>Connection Status Name</dt><dd>The friendly label shown for the bridge node in Connection Status.</dd></div>
		</dl>
		<p><strong>Maintain bridge friendly names</strong> keeps the configured Connection Status names in place across updates, restarts, and reboots.</p>
		<p><strong>Startup bridge summary</strong> is optional. At startup it waits for Asterisk and fixed-link recovery, then announces only configured Standard bridges that are actually established. It never announces Net Bridges, display-only cards, destinations, ports, credentials, or arbitrary remote nodes.</p>
		<p><strong>Connected Client Source</strong> should stay Disabled unless the bridge supplies a real client list. Local JSON / file accepts a readable local JSON source. HTTP API accepts a JSON status endpoint. ASR caches the result so every browser does not repeatedly contact the bridge.</p>
		<p><strong>Deleting a card:</strong> ASR shows both what it will remove and what it will not touch. Only a managed bridge created by ASR with an intact bridge-specific ownership manifest can have its dedicated resources retired. Without that proof, deleting the card removes ASR metadata only and leaves manual or pre-existing services, Asterisk configuration, files, firewall rules, ports, packages, and shared components untouched.</p>
	</section>

	<section id="bridge-setup" class="asr-instructions-section">
		<h2>Bridge Setup and Safety</h2>
		<ul class="asr-instructions-list">
			<li>Install and test the bridge software, private AllStar node, services, ports, IDs, credentials, and network forwarding before adding its ASR card.</li>
			<li>Do not share USRP, TLV, DMR-network, or vocoder ports between active bridge instances.</li>
			<li>ASR shows real bridge-client identities only when the bridge provides real data. It does not invent Zello or other client names from local metadata.</li>
			<li>Local loopback bridge plumbing is not a public connected client.</li>
			<li>Use Bridge Diagnostics after saving to confirm paths, services, and optional client sources without displaying passwords or tokens.</li>
			<li>ASR never adopts a pre-existing bridge resource merely because its path, unit name, node, or port matches. Bridge ownership is recorded only when ASR creates a new dedicated resource.</li>
		</ul>
	</section>

	<section id="dmr-net-bridge" class="asr-instructions-section">
		<h2>DMR Net Bridge</h2>
		<p>A DMR Net Bridge is a second, selectable DMR path for scheduled or outside nets. It does not replace the fixed DMR Home Bridge.</p>
		<div class="asr-instructions-compare">
			<article>
				<h3>DMR Home Bridge</h3>
				<p>Stays on the network’s normal home talkgroup so regular DMR users remain connected throughout the day.</p>
			</article>
			<article>
				<h3>DMR Net Bridge</h3>
				<p>Temporarily connects the AllStar network to another talkgroup for a net, then disconnects when the net is finished.</p>
			</article>
		</div>
		<ol class="asr-instructions-steps">
			<li><strong>Enter the talkgroup.</strong> Use digits only.</li>
			<li><strong>Select Connect.</strong> ASR selects that talkgroup on the dedicated DMR bridge and links its private AllStar bridge node to the main node.</li>
			<li><strong>Confirm the result.</strong> Current TG shows the talkgroup and the DMR Net Bridge appears in Connection Status.</li>
			<li><strong>Select Disconnect when finished.</strong> ASR disconnects the DMR network path, unlinks the private bridge node, clears the talkgroup box, and removes the bridge from Connection Status.</li>
		</ol>
		<div class="asr-instructions-status-grid">
			<article class="is-idle"><h3>Green — Idle</h3><p>No audio is passing. Talking displays <strong>-</strong>.</p></article>
			<article class="is-relay"><h3>Amber — Relay</h3><p>AllStar audio is going out through the DMR bridge. Talking displays <strong>-</strong> because the card is relaying another source.</p></article>
			<article class="is-source"><h3>Red — TX Active</h3><p>A DMR user is transmitting into AllStar. Talking shows the callsign when the bridge provides it.</p></article>
		</div>
		<p class="asr-instructions-callout"><strong>Important:</strong> Talkgroup changes affect everyone using the DMR Net Bridge. AllStar node linking and DMR talkgroup selection are separate controls.</p>
	</section>

	<section id="ysf-net-bridge" class="asr-instructions-section">
		<h2>YSF Net Bridge</h2>
		<p>A YSF Net Bridge is a separate selectable YSF path. It never replaces or retunes the fixed/home YSF Bridge.</p>
		<ol class="asr-instructions-steps">
			<li><strong>Install a reflector list.</strong> Open <a href="https://hostfiles.refcheck.radio/" target="_blank" rel="noopener noreferrer">RefCheck Hostfiles</a>, complete its access form, choose <strong>YSF Plain Text</strong>, and download YSFHosts.txt. In Reimagined Settings, open the saved YSF Net Bridge card and import that file. ASR validates the complete file before replacing an existing valid list.</li>
			<li><strong>Keep the list current.</strong> The list is a snapshot. If a newly registered reflector cannot be found, download a current YSF Plain Text file and import it again. Settings shows the current valid-reflector count and list date.</li>
			<li><strong>No list means no destination controls.</strong> ASR keeps Connect unavailable until a valid reflector list exists. A rejected upload never erases the previous valid list.</li>
			<li><strong>Enter a reflector.</strong> Type its exact name, such as <code>US-CUSTOM-TEST</code>, or its five-digit ID. The dashboard does not show or search a dropdown list.</li>
			<li><strong>Add an unlisted reflector once.</strong> In Reimagined Settings, enter its name, five-digit ID, hostname or IP, and UDP port. ASR combines it with the imported list in a separate root-owned catalog.</li>
			<li><strong>Select Connect.</strong> ASR sends the destination to the dedicated YSFGateway and waits for the Gateway log to confirm the exact link before linking the bridge’s private AllStar node.</li>
			<li><strong>Confirm the result.</strong> The card shows the current reflector from the cached watcher after refresh. Source/TX, Relay, caller, warning, and error information appears only when the dedicated logs provide real evidence.</li>
			<li><strong>Select Disconnect when finished.</strong> ASR attempts both the YSF unlink and local AllStar unlink and reports a partial failure rather than claiming success.</li>
		</ol>
		<p class="asr-instructions-callout"><strong>Isolation required:</strong> The YSF Net Bridge needs its own AllStar node, YSFGateway, MMDVM Bridge, Analog Bridge, emulator, configuration files, ports, logs, and RemoteCommand path. ASR does not install or remove that external bridge stack. When custom reflectors are configured, ASR points only the dedicated YSFGateway at its separate merged catalog and safely reloads that Gateway when the imported or custom catalog changes.</p>
	</section>

	<section id="next-digital-bridges" class="asr-instructions-section">
		<h2>P25, NXDN, and M17 Bridges</h2>
		<p>Each supported mode can use a Standard Bridge card or an isolated Net Bridge card. P25 and NXDN use numeric destination designators. M17 uses an unencrypted reflector and module through a qualified Codec2/USRP audio path.</p>
		<ol class="asr-instructions-steps">
			<li><strong>Install and isolate the bridge first.</strong> Every card needs its own local node, configuration, services, ports, and runtime identity. P25/NXDN also require authenticated local MQTT, per-instance topic permissions, and root-only ASR controller credentials. ASR does not create the external gateway stack or display those credentials.</li>
			<li><strong>Confirm permission.</strong> Choose Self-owned target or Target owner approved. Being listed in a public directory is not permission to cross-mode bridge a destination.</li>
			<li><strong>Use only permitted destinations.</strong> DMR talkgroups and YSF reflector names or IDs are entered manually; the selected permission confirms that every destination entered is authorized. P25, NXDN, and M17 remain limited to saved approved destinations. ASR still blocks invalid, reserved, encrypted, duplicate, or conflicting destinations.</li>
			<li><strong>Verify real state.</strong> Gateway command acceptance is not proof that a remote reflector is reachable. A card must not report fully linked until both digital-side evidence and the AllStar link agree.</li>
		</ol>
		<p class="asr-instructions-callout"><strong>Compatibility:</strong> P25, NXDN, and M17 are not categorically incompatible with the other ASR modes. The deciding factors are target-owner policy, isolated resources, and loop-free topology. If a target forbids cross-mode or external bridges, ASR must not connect it.</p>
	</section>

	<section id="lookup-map" class="asr-instructions-section">
		<h2>Lookup &amp; Station Map</h2>
		<p>Lookup refreshes connected-station information in place and shows the update time in the viewer’s local time. Public node and callsign links open their appropriate lookup services; private four-digit bridge nodes are not treated as public AllStar lookup targets.</p>
		<p>The station map uses orange approximate markers. QRZ XML coordinates are preferred when valid credentials are saved. Without QRZ, ASR can use a public city/region fallback. Browser-visible coordinates are rounded, fallback requests are rate-limited, and the cache survives package updates.</p>
	</section>

	<section id="updates" class="asr-instructions-section">
		<h2>Update Notifications</h2>
		<p>When a newer ASR release is available, a prominent notice appears near the top of the main dashboard. It shows the installed version, available version, release-notes link, package name, and SHA-256 checksum.</p>
		<ul class="asr-instructions-list">
			<li>The node checks at a low frequency using a cached background service. Opening more browser tabs does not create more GitHub checks.</li>
			<li><strong>Nothing installs automatically.</strong> The notice is informational and installation still requires deliberate manual approval.</li>
			<li>If GitHub is offline, rate-limited, or the release is incomplete, ASR quietly keeps the previous valid result instead of displaying an unverified package.</li>
			<li>Before installing, use the exact package and checksum shown for that release, then run the package lint and self-tests followed by the interactive installer.</li>
		</ul>
	</section>

	<section id="rollback" class="asr-instructions-section">
		<h2>Rolling Back ASR</h2>
		<p>Open <strong>Admin → Reimagined Settings → Roll Back ASR Version</strong>, the final expandable section above Save. It can offer up to the five newest valid previous ASR versions.</p>
		<ol class="asr-instructions-steps">
			<li>Select the previous version.</li>
			<li>Select <strong>Roll Back to Selected Version</strong>.</li>
			<li>Review the current and target versions, then confirm.</li>
			<li>Keep the page open while ASR creates a new safety backup and restores the selected version.</li>
		</ol>
		<p>Rollback preserves users, Favorites, the database, Reimagined settings, bridge settings, map cache, and protected secrets. It does not use the normal Save button, and unsaved settings edits are not saved during rollback.</p>
		<p class="asr-instructions-callout"><strong>Note:</strong> If the restored version predates the on-screen rollback feature, this menu will no longer appear there. The safety backup and command-line recovery helper remain available.</p>
	</section>

	<section id="diagnostics" class="asr-instructions-section">
		<h2>Diagnostics, Refreshing &amp; Support</h2>
		<ul class="asr-instructions-list">
			<li><strong>Normal refresh:</strong> Reload the page after saving settings.</li>
			<li><strong>Hard refresh:</strong> Use Ctrl+Shift+R on Windows/Linux or Command+Shift+R on Mac. On a phone, close the ASR tab and reopen it.</li>
			<li><strong>Bridge Diagnostics:</strong> Check card configuration, services, paths, and client sources without revealing credentials.</li>
			<li><strong>Performance Stats:</strong> Review browser-feed and server-cache performance when status is slow.</li>
			<li><strong>Report a Bug:</strong> Create a redacted support bundle from the Admin menu. Review it before sending; protected secret values are excluded.</li>
			<li>If DMR audio is distorted, confirm the net bridge has its own vocoder and that its gains match the known-good DMR bridge.</li>
			<li>If TX Active never appears, confirm the dedicated bridge log is current and readable.</li>
		</ul>
	</section>
</main>
<?php pageEnd(); ?>
