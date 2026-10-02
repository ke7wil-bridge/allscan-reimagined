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
		<p>This is the complete guide to the ASR dashboard, Settings, bridge cards, updates, and recovery tools.</p>
	</header>

	<nav class="asr-instructions-jump" aria-label="Instruction topics">
		<a href="#getting-started">Getting Started</a>
		<a href="#dashboard-controls">Dashboard &amp; Controls</a>
		<a href="#appearance-access">Settings &amp; Appearance</a>
		<a href="#bridge-cards">Digital Bridge Status</a>
		<a href="#bridge-setup">Bridge Setup Wizard</a>
		<a href="#net-bridge">Unified Net Bridge</a>
		<a href="#standard-bridges">Standard Digital Bridges</a>
		<a href="#lookup-map">Lookup &amp; Map</a>
		<a href="#updates">Update Notices</a>
		<a href="#rollback">Rollback</a>
		<a href="#diagnostics">Diagnostics &amp; Help</a>
	</nav>

	<section id="getting-started" class="asr-instructions-section">
		<h2>Getting Started</h2>
		<p>Beta 8 keeps the original AllScan and AllScan Reimagined side by side:</p>
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
		<p class="asr-instructions-callout"><strong>Saving changes:</strong> Each Settings category names the scope of its Save button. Rollback uses its own confirmation workflow and is never started by a Settings save.</p>
	</section>

	<section id="dashboard-controls" class="asr-instructions-section">
		<h2>Dashboard, Talkers, Node Controls &amp; Favorites</h2>
		<div class="asr-instructions-topic-grid">
			<article><h3>Talker Cards</h3><p>The Talkers module shows Current Talker plus Last Talker 1–4. History headings include the local last-heard time in 24-hour HH:MM format. Duration stays at the bottom of each card. The Talkers checkbox shows or hides this module and that choice is remembered by the current browser.</p></article>
			<article><h3>Node Controls</h3><p>Enter an AllStar node number and use Connect or Disconnect. <strong>Disc. before Connect</strong> is remembered by that browser. <strong>Permanent</strong> applies only to the current action and is not remembered. <strong>Commands</strong> opens configured custom DTMF commands; administrators can manage the command list there.</p></article>
			<article><h3>Favorites</h3><p>The Favs checkbox opens the Favorites module and is remembered by the browser. One click selects a Favorite and copies its node into Node Controls; <strong>double-click connects once</strong>. Drag Favorites to reorder them, edit descriptions, and use the color control to set each Favorite's tab color. Status indicators show networking, recent TX, Rx Busy, and scanning state.</p></article>
			<article><h3>Dashboard Layout</h3><p>Talkers, Node Controls, Favorites, Connection Status, and Digital Bridge Status can be reordered with their six-dot handles. Module order and open/closed UI preferences are browser-local, so changing browsers or devices can have a different layout.</p></article>
		</div>
	</section>

	<section id="appearance-access" class="asr-instructions-section">
		<h2>Settings, Appearance &amp; Access</h2>
		<p>The current Settings interface is organized into <strong>My Account</strong>, <strong>Settings Home</strong>, <strong>Appearance &amp; Display</strong>, <strong>Bridges</strong>, <strong>Lookup &amp; Map</strong>, <strong>Access &amp; Administration</strong>, and <strong>System &amp; Recovery</strong>.</p>
		<div class="asr-instructions-topic-grid">
			<article><h3>Appearance &amp; Display</h3><p>Header title and logo, station display filters, and Display Performance live here. The title can use <strong>{CALLSIGN}</strong> and <strong>{NODE}</strong>. Low-Power Node Mode reduces background work and disables animated themes without changing Asterisk or bridge audio.</p></article>
			<article><h3>Access &amp; Administration</h3><p>ASR and stock AllScan login requirements are controlled here. Administrator Tools links to Users, Advanced Configuration, and Performance Stats. Existing users and permissions are retained when access policy changes.</p></article>
			<article><h3>Themes</h3><p>Theme selection is a browser preference rather than a node-level Settings value. Available Beta 8 themes include Dark Side, Bright Side, Deep Ocean, Matrix, and ST:ASL.</p></article>
			<article><h3>Shared Menu</h3><p>The main menu provides Admin, Theme, Resources, Lookup, Support &amp; Feedback, and <strong>Donate to ASR</strong>. Donate to ASR is optional and opens the configured PayPal contribution link; it is separate from Support &amp; Feedback.</p></article>
		</div>
	</section>

	<section id="bridge-cards" class="asr-instructions-section">
		<h2>Digital Bridge Status &amp; Bridge Cards</h2>
		<p>Digital Bridge Status contains the configured bridge cards. Standard cards represent fixed or externally managed digital paths; the unified Net Bridge is the selectable outbound bridge. Cards use evidence-backed state and do not fabricate talkers, clients, or activity.</p>
		<dl class="asr-instructions-definitions">
			<div><dt>Talking</dt><dd>Shows the current inbound digital source when the bridge supplies fresh evidence. Relay means local AllStar audio is being sent outward through the bridge.</dd></div>
			<div><dt>Last Talker</dt><dd>The most recent verified inbound talker for that bridge. Stale or fallback local identity is not treated as a remote talker.</dd></div>
			<div><dt>Connected Clients</dt><dd>Shows real remote clients only when that bridge provides a supported roster. Local loopback plumbing is not counted as a public client.</dd></div>
			<div><dt>Recent Activity</dt><dd>Recent evidence-backed bridge activity. Missing data remains empty rather than being invented.</dd></div>
			<div><dt>Fixed Bridge Recovery</dt><dd>A Standard Bridge can optionally restore a missing local fixed link. If Asterisk already maintains a native permanent link, ASR stays out of the way. Net Bridges are excluded.</dd></div>
		</dl>
		<p><strong>Maintain bridge friendly names</strong> preserves configured Connection Status labels across updates and restarts. Startup bridge summary is optional and announces only configured Standard bridges that are actually established.</p>
		<p><strong>D-Star is Standard-only.</strong> Its card uses managed heartbeat, gateway/link, MMDVM activity, and local reflector evidence. The fallback local INI callsign is not a remote talker and does not create talker history.</p>
		<p><strong>Deleting a card:</strong> ASR removes managed bridge resources only when bridge-specific ownership is proven. Otherwise it removes ASR metadata and leaves pre-existing services, Asterisk configuration, files, ports, packages, and shared components untouched.</p>
	</section>

	<section id="bridge-setup" class="asr-instructions-section">
		<h2>Bridge Setup Wizard &amp; Safety</h2>
		<p>Open <strong>Settings → Bridges → + Add Bridge</strong>. The wizard supports Net Bridge, M17, P25, NXDN, YSF, DMR, D-Star, and Zello. It detects the ASL3 system, validates the plan, previews what will change, installs/configures supported managed resources, and performs health checks before the bridge is treated as ready.</p>
		<ul class="asr-instructions-list">
			<li>Use the unified <strong>Net Bridge</strong> choice for the Beta 8 outbound multi-mode bridge. Its private AllStar transport node is managed internally rather than chosen during normal operation.</li>
			<li>Standard bridges normally stay on one destination. Display-only cards monitor externally installed services without operating them.</li>
			<li>Do not share USRP, TLV, DMR-network, MQTT, emulator, or vocoder ports between active bridge instances.</li>
			<li>ASR never adopts a pre-existing bridge resource merely because a path, unit name, node, or port matches. Ownership is recorded only when ASR creates a dedicated managed resource.</li>
			<li>Use Bridge Diagnostics after saving to verify configuration, services, paths, and optional client sources without displaying credentials.</li>
		</ul>
	</section>

	<section id="net-bridge" class="asr-instructions-section">
		<h2>Unified Net Bridge</h2>
		<p>Beta 8 uses one general-purpose Net Bridge on the private AllStar transport node <strong>1999</strong>. It is separate from fixed/home reflector infrastructure. Only one Net Bridge mode is active at a time, and each mode keeps its own last-used destination so switching modes does not overwrite another mode's destination.</p>
		<div class="asr-instructions-compare">
			<article><h3>Supported Modes</h3><p><strong>DMR</strong> uses a talkgroup. <strong>YSF</strong> uses a reflector. <strong>P25</strong> and <strong>NXDN</strong> use numeric destinations. <strong>M17</strong> uses a reflector and module.</p></article>
			<article><h3>Mode Switching</h3><p>Selecting a mode safely stops the previous backend, reconfigures node 1999 for the selected mode, starts the selected backend, restores that mode's destination, and verifies readiness. If a switch fails, ASR rolls back or leaves the Net Bridge safely disconnected.</p></article>
		</div>
		<ol class="asr-instructions-steps">
			<li>Select DMR, YSF, P25, NXDN, or M17 on the Net Bridge card.</li>
			<li>Enter the destination required by that mode. DMR accepts a valid talkgroup; YSF accepts a valid reflector; P25/NXDN accept their destination; M17 uses reflector plus module.</li>
			<li>Select Connect and wait for bridge-side and AllStar-side evidence. Command acceptance alone is not proof of a completed connection.</li>
			<li>Use Disconnect when finished. Changing digital destination and linking ordinary AllStar nodes are separate operations.</li>
		</ol>
		<p class="asr-instructions-callout"><strong>Loop safety:</strong> Do not point the Net Bridge at the same destination already carried by a fixed bridge on the node. That can create an audio feedback loop.</p>
	</section>

	<section id="standard-bridges" class="asr-instructions-section">
		<h2>Standard Digital Bridges</h2>
		<p>Standard cards are for fixed/home or externally managed bridge paths. DMR, YSF, P25, NXDN, M17, D-Star, Zello, and the shared URF reflector can be represented when their backend is installed and provides the required evidence.</p>
		<ul class="asr-instructions-list">
			<li><strong>URF:</strong> one shared reflector transport can expose separate DMR, YSF, P25, NXDN, and M17 mode cards. Configure only the modes actually used.</li>
			<li><strong>DMR/TGIF:</strong> TGIF website session authentication and the DMR network connection are separate. Secured DMR uses the configured hotspot key; legacy operation can use the DMR identity without a personal key.</li>
			<li><strong>YSF:</strong> tunable YSF paths can import a YSF Plain Text host list. The list is a snapshot; re-import it when a newly registered reflector is missing. A rejected upload never erases the previous valid list.</li>
			<li><strong>P25/NXDN/M17:</strong> use isolated resources and valid destinations. M17 requires its reflector/module and a qualified Codec2/USRP audio path.</li>
			<li><strong>D-Star:</strong> reflector configuration remains in the installed D-Star gateway; ASR displays evidence-backed status and has no D-Star destination control.</li>
			<li><strong>Zello:</strong> account/sign-in management remains outside ASR. ASR displays current/recent talker information only when the external bridge exposes it.</li>
		</ul>
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
			<li><strong>Start in a root shell.</strong> Run <code>sudo -i</code> first, then confirm the prompt begins with <code>root@</code> and ends with <code>#</code>. Stop if it does not.</li>
			<li><strong>Check root before preflight.</strong> Run <code>[ "$(id -u)" -eq 0 ] || { echo "ERROR: Root is required. Run sudo -i first." &gt;&amp;2; exit 1; }</code> before the package lint and self-tests.</li>
			<li><strong>Use the complete release command.</strong> Copy the full install block from that release so the package URL, SHA-256 check, preflight tests, and interactive <code>bash ./install.sh</code> stay together.</li>
			<li><strong>Fresh installation.</strong> If stock <code>/allscan/</code> is absent, ASR offers to run the official AllScan installer first. It verifies the installed stock version and an exact compatibility layer before creating <code>/asr/</code>. Declining or failing that step leaves <code>/asr/</code> uninstalled; review the official installer output before retrying.</li>
			<li><strong>No bridge is required.</strong> ASR installs normally without a configured digital bridge; bridge-specific service checks run only for bridge types present in Settings.</li>
			<li>The node checks at a low frequency using a cached background service. Opening more browser tabs does not create more GitHub checks.</li>
			<li><strong>Nothing installs automatically.</strong> The notice is informational and installation still requires deliberate manual approval.</li>
			<li>If GitHub is offline, rate-limited, or the release is incomplete, ASR quietly keeps the previous valid result instead of displaying an unverified package.</li>
			<li>Before installing, use the exact package and checksum shown for that release, then run the package lint and self-tests followed by the interactive installer.</li>
		</ul>
	</section>

	<section id="rollback" class="asr-instructions-section">
		<h2>Rolling Back ASR</h2>
		<p>Open <strong>Admin → Settings</strong>, then choose <strong>System &amp; Recovery</strong>. Rollback can offer up to the five newest valid previous ASR versions.</p>
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
			<li><strong>Support &amp; Feedback:</strong> Report a bug, ask a question, or suggest a feature from the main menu. Review the public issue text and any screenshots before submitting.</li>
			<li>If DMR audio is distorted, confirm the net bridge has its own vocoder and that its gains match the known-good DMR bridge.</li>
			<li>If TX Active never appears, confirm the dedicated bridge log is current and readable.</li>
		</ul>
	</section>
</main>
<?php asExit(); ?>
