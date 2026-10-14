/* Optional browser QA: PLAYWRIGHT_MODULE=/path/to/playwright node scripts/browser-smoke.cjs */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8080';
  const out = path.resolve('artifacts/browser');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL} : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const checks = [];
  try {
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.locator('#run-select').selectOption('demo-four-step-v1');
    await page.getByRole('heading', { name: 'site-01', exact: true }).waitFor();
    assert.equal(await page.locator('.station-card').count(), 3);
    assert.equal(await page.locator('.station-card .source-badge').filter({ hasText: '人工演示' }).count(), 3);
    await page.screenshot({ path: path.join(out,'overview.png'), fullPage: true });
    checks.push('three-site overview with explicit demo provenance');
    await page.locator('.station-card').filter({ hasText: 'site-01' }).getByRole('button',{name:'查看决策依据'}).click();
    await page.getByRole('heading',{name:'site-01 · 决策依据'}).waitFor();
    await page.getByText('€0.569000', { exact: true }).waitFor();
    assert.ok(await page.locator('.evidence-refs code').count() > 0);
    await page.getByRole('button',{name:'为什么这样做？',exact:true}).click();
    await page.getByText('基于当前决策的回答',{exact:true}).waitFor();
    await page.getByRole('textbox',{name:'你的问题'}).fill('明年一定能赚多少钱');
    await page.getByRole('button',{name:'提问',exact:true}).click();
    await page.getByText('当前支持范围',{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,'detail.png'),fullPage:true});
    checks.push('decision evidence, template question and unsupported question');
    await page.getByLabel('首步电池充电功率').fill('0');
    await page.getByRole('button',{name:'计算替代方案',exact:true}).click();
    await page.getByRole('heading',{name:'如果换一种做法'}).waitFor();
    await page.getByRole('heading',{name:'+€0.811105',exact:true}).waitFor({timeout:40000});
    await page.getByText('€1.380105',{exact:true}).waitFor();
    assert.equal(await page.locator('.scenario-panel tbody tr').count(),8);
    await page.getByText('人工演示',{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,'comparison.png'),fullPage:true});
    checks.push('independent-worker comparison: 0.569000 / 1.380105 / +0.811105 EUR');
    await page.getByRole('link',{name:'站点总览',exact:true}).click();
    await page.locator('.station-card').filter({hasText:'site-02'}).getByRole('button',{name:'查看决策依据'}).click();
    await page.getByRole('heading',{name:'site-02 · 决策依据'}).waitFor();
    assert.match(await page.locator('.fact-list').innerText(),/已达上界/);
    checks.push('full SOC boundary');
    await page.getByRole('link',{name:'站点总览',exact:true}).click();
    await page.locator('.station-card').filter({hasText:'site-03'}).getByRole('button',{name:'查看决策依据'}).click();
    await page.getByRole('heading',{name:'site-03 · 决策依据'}).waitFor();
    await page.getByText('当前使用降级方案',{exact:true}).waitFor();
    assert.match(await page.locator('.fact-list').innerText(),/48 小时/);
    checks.push('stale history and explicit fallback assumptions');
    await page.getByRole('link',{name:'光伏分析',exact:true}).click();
    await page.getByLabel('回放口径').selectOption('irradiance');
    for (const site of ['1','2','3']) {
      await page.getByLabel('站点编号').fill(site);
      await page.getByText('2026-09-14',{exact:true}).waitFor();
      const boundary = page.getByLabel('三站历史回放证据边界');
      await boundary.getByText('不是比赛现场三台设备').waitFor();
      await boundary.getByText('未来实测短波辐照度代理').waitFor();
      await boundary.getByText('not_measured').waitFor();
      await boundary.getByText('没有负价样本').waitFor();
      await boundary.getByText('不得用于在线 deadline 或跨时区比较').waitFor();
      assert.ok(await page.locator('.pv-day').count() > 0);
    }
    checks.push('three-site irradiance replay with visible evidence boundaries');
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('link',{name:'站点总览',exact:true}).click();
    await page.getByRole('heading',{name:'site-01',exact:true}).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    checks.push('390px mobile overview without horizontal overflow');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2)+'\n');
    console.log(JSON.stringify({passed:checks.length,checks,errors}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
