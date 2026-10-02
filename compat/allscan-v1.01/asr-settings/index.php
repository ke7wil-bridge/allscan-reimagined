<?php
// Settings view dispatcher. Keep the legacy renderer server-selectable during
// the Beta 8 redesign evaluation so recovery never depends on client script.
$requestedView = strtolower(trim((string)($_GET['view'] ?? 'modern')));
define('ASR_SETTINGS_MODERN_UI', $requestedView !== 'legacy');
require __DIR__ . '/settings-controller.php';
