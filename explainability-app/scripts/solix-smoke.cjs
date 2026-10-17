// Run against a separate, explicitly seeded demo database and its worker.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.resolve(__dirname, '../.cache/browser-qa/node_modules/playwright'));
const base = process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8093';
const output = path.resolve(__dirname, '../artifacts/solix-v13');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(15_000);
    const errors = []; const writes = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname); });
    const overview = await (await fetch(`${base}/api/stations`)).json();
    assert.ok(overview.stations.length >= 2, 'A seeded demo with at least two sites is required');
    async function route(name) {
      console.log(`Checking ${name}`);
      await page.goto(`${base}/#/${name}`, { waitUntil: 'domcontentloaded' });
      if (name === 'home') await page.getByRole('button', { name: '展开能源信息节点' }).waitFor();
      else await page.locator('.solix-ui .heading h1').waitFor();
    }
    await route('home');
    await page.getByRole('button', { name: '展开能源信息节点' }).click();
    await page.locator('.home-universe.expanded').waitFor();
    await page.screenshot({ path: path.join(output, 'home-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: /CURRENT SITE/ }).click();
    await page.locator('#solix-site-options button').nth(1).click();
    await page.locator('.home-time').filter({ hasText: overview.stations[1].site_id }).waitFor();
    await route('overview');
    await page.getByText('快照未提供', { exact: true }).waitFor();
    await route('timeline');
    await page.getByRole('slider', { name: '查看预测时刻' }).waitFor();
    await page.screenshot({ path: path.join(output, 'timeline-desktop.png'), fullPage: true });
    await route('ai');
    await page.getByRole('button', { name: '为什么这样做', exact: true }).click();
    await page.locator('.answer').filter({ hasNotText: '选择追问' }).waitFor();
    await page.getByRole('button', { name: '重新求解并比较 →' }).click();
    await page.getByText(/比较任务：结果可用/).waitFor({ timeout: 45000 });
    await route('plan');
    await page.getByRole('button', { name: '检查模型可行性 →' }).click();
    // The artificial four-step provider intentionally has no E4 plan capability.
    await page.getByRole('heading', { name: '能力不可用', exact: true }).first().waitFor({ timeout: 45000 });
    await route('analytics');
    await page.getByText('当前站点和方案没有光伏日报，尚不能计算去向比例。').waitFor();
    await page.screenshot({ path: path.join(output, 'analytics-desktop.png'), fullPage: true });
    await route('preferences');
    await page.getByRole('button', { name: /费用优先/ }).click();
    await page.getByRole('button', { name: '保存家庭偏好' }).click();
    await page.getByText('已保存到当前浏览器 · 未写入设备').waitFor();
    await page.goto(`${base}/#/decisions/${encodeURIComponent(overview.stations[0].id)}`);
    await page.getByRole('button', { name: '← 返回 SOLIX 家庭能源' }).waitFor();
    await page.getByRole('heading', { name: /决策依据/ }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    for (const name of ['home', 'overview', 'timeline', 'ai', 'plan', 'analytics', 'preferences']) {
      await route(name);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name} overflows mobile viewport`);
      if (name === 'home') {
        await page.getByRole('button', { name: '展开能源信息节点' }).click();
        await page.screenshot({ path: path.join(output, 'home-mobile.png'), fullPage: true });
      }
    }
    assert.deepEqual(errors, [], 'Browser errors');
    assert.ok(writes.every(p => p === '/api/questions' || p === '/api/plans/feasibility' || /\/counterfactuals$/.test(p)), `Unexpected writes: ${writes}`);
    const report = { status: 'passed', sizes: ['1440x1050', '390x844'], checks: ['site selection', 'forecast', 'explanation', 'counterfactual worker', 'unsupported E4', 'empty PV', 'local preferences', 'evidence deep link', 'all mobile routes'], browserErrors: errors, postPaths: writes };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
