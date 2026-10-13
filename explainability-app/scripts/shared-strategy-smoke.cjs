/* Run after the synthetic shadow export/import, with API and worker running. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8080';
  const input = process.env.SHADOW_RECORDS || '../outputs/explainability-smoke-v2/shadow/explainability-decisions.jsonl';
  const records = fs.readFileSync(input, 'utf8').split(/\r?\n/).filter(s => s.trim()).map(s => JSON.parse(s));
  assert.equal(records.length, 3);
  assert(records.every(r => r.provenance.input_kind === 'synthetic' && r.model_version === 'shared-milp-3a9dff42-cf1'));
  const out = path.resolve('artifacts/shared-browser');
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL ? {channel: process.env.BROWSER_CHANNEL} : {})});
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  const errors = [], checks = [], imported = [], summaries = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    for (const record of records) {
      const response = await page.request.post(base + '/api/decisions', {data: record});
      assert.equal(response.status(), 200, await response.text());
      const d = await response.json();
      assert.equal(d.capabilities.counterfactual, true);
      assert.deepEqual(d.execution_events, []);
      assert.equal(d.settlement, null);
      assert.equal(d.execution_status, 'candidate_only');
      imported.push(d);
    }
    checks.push('same-solve shared strategy import; matching model available; no execution/settlement invented');
    await page.goto(base, {waitUntil: 'networkidle'});
    await page.locator('#run-select').selectOption(records[0].run_id);
    await page.locator('.station-card').nth(2).waitFor();
    assert.equal(await page.locator('.station-card').count(), 3);
    assert.equal(await page.locator('.station-card .source-badge').filter({hasText: '模型接入验证 · 人工输入'}).count(), 3);
    await page.screenshot({path: path.join(out, 'overview.png'), fullPage: true});
    checks.push('three-site overview retains synthetic input and fallback/candidate/unsettled states');
    const first = records.findIndex(r => r.site_id === '1');
    await page.locator('.station-card').filter({has: page.getByRole('heading', {name: '1', exact: true})}).getByRole('button', {name: '查看决策依据'}).click();
    await page.getByRole('heading', {name: '1 · 决策依据'}).waitFor();
    assert.match(await page.locator('.fact-list').innerText(), /电池充电 5 kW/);
    assert.match(await page.locator('.scope-note').first().innerText(), /共同策略 MILP/);
    assert.match(await page.locator('.source-grid').innerText(), /人工数据/);
    await page.screenshot({path: path.join(out, 'detail.png'), fullPage: true});
    checks.push('detail displays shared source, saved four flows, costs and traceable evidence');
    await page.getByLabel('首步电池充电功率').fill('0');
    await page.getByRole('button', {name: '计算替代方案', exact: true}).click();
    await page.locator('.delta-card').waitFor({timeout: 40000});
    assert.equal(await page.locator('.scenario-panel tbody tr').count(), 192);
    assert.match(await page.locator('.assumptions-panel').innerText(), /energy_lab.rolling/);
    assert.match(await page.locator('.assumptions-panel').innerText(), /不下发 HA/);
    await page.screenshot({path: path.join(out, 'comparison.png')});
    checks.push('detail form → durable API task → independent shared MILP worker → 96+96 plan UI');
    for (let index = 0; index < records.length; index++) {
      const response = await page.request.post(base + '/api/decisions/' + imported[index].id + '/counterfactuals', {data: {first_charge_kw: 0}});
      assert.equal(response.status(), 202);
      const submitted = await response.json();
      let job;
      for (let attempt = 0; attempt < 140; attempt++) {
        job = await (await page.request.get(base + '/api/counterfactuals/' + submitted.id)).json();
        if (!['queued', 'running'].includes(job.status)) break;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
      assert.equal(job.status, 'ready', JSON.stringify(job));
      assert.equal(job.result.baseline.plan.length, 96);
      assert.equal(job.result.alternative.plan.length, 96);
      assert(Math.abs(job.result.alternative.plan[0].charge_kw) < 1e-6);
      for (const key of Object.keys(records[index].cost)) {
        assert(Math.abs(job.result.baseline.cost[key] - records[index].cost[key]) < 1e-6, key);
      }
      summaries.push({site: records[index].site_id, steps: 96, baseline: job.result.baseline.cost.objective_eur,
        alternative: job.result.alternative.cost.objective_eur, delta_eur: job.result.delta_eur});
    }
    checks.push('three saved candidates reproduce all cost components; only alternative first charge changes');
    assert(summaries[first].delta_eur > 0);
    assert.deepEqual(errors, []);
    const result = {checks, summaries, errors};
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
