<?php
// AllScan Reimagined EchoLink lookup page
require_once('../include/common.php');
$html = new Html();
$msg = [];
asInit($msg);
asrInitAuthenticatedUser($msg);

function asr_h(string $value): string {
	return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$lookup = preg_replace('/[^A-Za-z0-9\/-]/', '', (string) ($_GET['lookup'] ?? $_POST['lookup'] ?? ''));
$rows = [];
$error = '';

if ($lookup !== '') {
	$payload = http_build_query(['call' => $lookup]);
	$context = stream_context_create([
		'http' => [
			'method' => 'POST',
			'header' => "Content-Type: application/x-www-form-urlencoded\r\nUser-Agent: AllScanReimaginedEchoLinkLookup/1.0\r\n",
			'content' => $payload,
			'timeout' => 8,
			'ignore_errors' => true,
		],
	]);
	$body = @file_get_contents('https://www.echolink.org/validation/node_lookup.jsp', false, $context);
	if (!is_string($body) || $body === '') {
		$error = 'EchoLink lookup is unavailable right now.';
	} elseif (preg_match_all('/<tr>\s*<td>(.*?)<\/td>\s*<td[^>]*>(.*?)<\/td>\s*<\/tr>/is', $body, $matches, PREG_SET_ORDER)) {
		foreach ($matches as $match) {
			$callsign = trim(strip_tags($match[1]));
			$node = trim(strip_tags($match[2]));
			if ($callsign !== '' || $node !== '') {
				$rows[] = ['callsign' => $callsign, 'node' => $node];
			}
		}
	}
}

pageInit();
?>
<section class="asr-lookup-page asr-echolink-lookup-page">
	<h1 class="asr-lookup-title">EchoLink Lookup</h1>

	<div class="asr-lookup-grid asr-lookup-grid-single">
		<section class="asr-lookup-list-panel">
			<div class="asr-lookup-panel-head">
				<h2>EchoLink Result</h2>
			</div>
			<?php if ($lookup !== '') : ?>
				<p class="asr-lookup-help">EchoLink node number selected from ASR: <strong><?php echo asr_h($lookup); ?></strong></p>
			<?php else : ?>
				<p class="asr-lookup-help">No EchoLink number was supplied.</p>
			<?php endif; ?>

			<section class="asr-echolink-result-card">
				<h3><?php echo $lookup !== '' ? 'Result for ' . asr_h($lookup) : 'No EchoLink number'; ?></h3>
				<?php if ($rows) : ?>
					<div class="asr-echolink-result-table-wrap">
						<table class="asr-echolink-result-table">
							<thead>
								<tr>
									<th>Callsign</th>
									<th>EchoLink Number</th>
								</tr>
							</thead>
							<tbody>
								<?php foreach ($rows as $row) : ?>
									<tr>
										<td>
											<?php if ($row['callsign'] !== '') : ?>
												<a href="https://www.qrz.com/db/<?php echo rawurlencode(strtoupper($row['callsign'])); ?>" target="_blank" rel="noreferrer"><?php echo asr_h(strtoupper($row['callsign'])); ?></a>
											<?php else : ?>
												-
											<?php endif; ?>
										</td>
										<td><?php echo asr_h($row['node']); ?></td>
									</tr>
								<?php endforeach; ?>
							</tbody>
						</table>
					</div>
				<?php elseif ($error !== '') : ?>
					<p class="asr-lookup-error"><?php echo asr_h($error); ?></p>
				<?php elseif ($lookup !== '') : ?>
					<p class="asr-lookup-empty">No EchoLink match was returned for this lookup.</p>
				<?php endif; ?>

				<?php if ($lookup !== '') : ?>
					<form class="asr-echolink-actions" method="post" action="https://www.echolink.org/validation/node_lookup.jsp" target="_blank" rel="noreferrer">
						<input type="hidden" name="call" value="<?php echo asr_h($lookup); ?>">
						<button type="submit">Open on EchoLink</button>
							<a href="<?php echo asr_h(rtrim($urlbase, '/') . '/lookup/'); ?>">Back to Lookup</a>
							<a href="<?php echo asr_h(rtrim($urlbase, '/') . '/'); ?>">Return to Main Page</a>
					</form>
				<?php endif; ?>
			</section>
		</section>
	</div>
</section>
<?php
asExit();
