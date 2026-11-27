// Actual isolated Docker web tier; the test broker refuses all host mutations.
const { chromium } = await import(process.env.ASR_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.ASR_QA_WEB
const password = process.env.ASR_QA_PASSWORD
if (!/^http:\/\/172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+$/.test(base || '') || !password) {
  throw new Error('An isolated Docker QA endpoint and synthetic password are required')
}
const assert = (value, reason) => { if (!value) throw new Error(reason) }
const api = base + '/asr/asr-api.php?action='
const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext()
  await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const headers = { 'X-ASR-Requested-With': 'asr-update-control', Origin: base }
  let response = await context.request.post(api + 'update-queue', { headers })
  assert(response.status() === 403, 'anonymous queue was not rejected')
  response = await context.request.get(api + 'update-check')
  assert(response.status() === 403, 'anonymous check was not rejected')
  const page = await context.newPage()
  await page.goto(base + '/asr/user/')
  await page.locator('input[name=name]').fill('asrqa')
  await page.locator('input[name=pass]').fill(password)
  await page.locator('input[type=submit]').first().click()
  await page.waitForLoadState('domcontentloaded')
  response = await context.request.get(api + 'auth-status')
  const auth = await response.json()
  assert(auth.isAdmin === true, 'real AllScan login did not establish admin permission')
  response = await context.request.post(api + 'update-queue')
  assert(response.status() === 403, 'missing update header was not rejected')
  response = await context.request.post(api + 'update-queue', { headers: {...headers, Origin: 'https://example.invalid'} })
  assert(response.status() === 403, 'cross-origin queue was not rejected')
  await page.goto(base + '/asr/?updateAsr=1')
  await page.getByRole('button', { name: 'Update Now', exact: true }).waitFor({timeout: 20000})
  await page.getByRole('button', { name: 'Update Now', exact: true }).click()
  await page.getByText('Isolated QA fixture refuses host mutations', { exact: false }).waitFor({timeout: 12000})
  console.log('Actual browser login, authorization, update check and rejected queue: PASS')
} finally {
  await browser.close()
}
