/* Browser proof for W1 single-site read-only replay. API must be started with a pinned trace. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8080';
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const summaryResponse = await page.request.get(base + '/api/offline-replay');
    assert.equal(summaryResponse.status(), 200);
    const summary = await summaryResponse.json();
    assert.equal(summary.time_basis, 'dataset_local_hour');
    assert.equal(summary.hours_per_arm, 168);
    assert.equal(summary.source_sha256, 'c0a7ff9479f2f8faffc6791cd4c0670f38107beba22599fcfd2c4a5479e99fbf');
    assert.equal(summary.official_score_claim, false);
    const detail = await (await page.request.get(base + '/api/offline-replay/steps/0?arm=irradiance')).json();
    assert.equal(detail.record.local_hour, '2026-09-14 14:00:00');
    assert.equal(detail.execution_status, 'not_observed');
    assert.equal(detail.settlement, null);
    assert.equal(detail.capabilities.counterfactual, false);
    assert(!('device_time' in detail.record));
    await page.goto(base + '/#/replay', {waitUntil: 'networkidle'});
    await page.getByRole('heading', {name: '真实策略回测记录'}).waitFor();
    await page.getByText(/没有设备时区，不能用于在线 deadline/).waitFor();
    await page.getByText(/不是现场设备或官方结算/).waitFor();
    assert.ok(await page.locator('.fact').count() >= 4);
    await page.getByRole('button', {name: '下一小时'}).click();
    await page.getByRole('heading', {name: /2026-09-14 15:00:00/}).waitFor();
    await page.getByLabel('回测臂').selectOption('persistence');
    await page.getByRole('heading', {name: /persistence/}).waitFor();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true, site: summary.site_id, arms: summary.arms,
      time_basis: summary.time_basis, browser_errors: errors}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
