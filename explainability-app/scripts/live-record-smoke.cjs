/* Validate captured external records against a local API and independent worker.
 * No credentials, HA endpoints or device writes are used here.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8080';
  const input = process.env.LIVE_RECORDS || '../outputs/live-explainability/attempt-01/decisions.jsonl';
  const records = fs.readFileSync(input, 'utf8').split(/\r?\n/).filter(s => s.trim()).map(s => JSON.parse(s));
  assert.equal(records.length, 3, 'requires one complete three-site capture');
  assert.equal(new Set(records.map(d => d.site_id)).size, 3, 'requires three distinct sites');
  assert.equal(new Set(records.map(d => d.run_id)).size, 1, 'must use the same capture run');
  assert(records.every(d => d.source_kind === 'external' && ['mixed', 'observed'].includes(d.provenance?.input_kind)
    && d.snapshot.sources.pv === 'observed' && d.snapshot.sources.load === 'observed'));
  const out = path.resolve('artifacts/live-readonly');
  fs.mkdirSync(out, {recursive: true});
  const summaries = [], checks = [], decisions = [];
  async function request(url, data, expected = 200) {
    const response = await fetch(base + url, data === undefined ? {} : {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const body = await response.json();
    assert.equal(response.status, expected, JSON.stringify(body));
    return body;
  }
  async function compare(id, power) {
    const submitted = await request('/api/decisions/' + id + '/counterfactuals', {first_charge_kw: power}, 202);
    let job;
    for (let attempt = 0; attempt < 140; attempt++) {
      job = await request('/api/counterfactuals/' + submitted.id);
      if (!['queued', 'running'].includes(job.status)) return job;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('worker did not finish persisted task within polling budget');
  }
  for (const record of records) {
    const d = await request('/api/decisions', record);
    assert.equal(d.created, false, 'CLI must have imported the same records first');
    assert.equal(d.capabilities.counterfactual, true);
    assert.equal(d.execution_status, 'candidate_only');
    assert.deepEqual(d.execution_events, []);
    assert.equal(d.settlement, null);
    const explanation = await request('/api/decisions/' + d.id + '/explanation');
    assert.equal(explanation.template_version, 'facts-template-v2');
    assert(!explanation.scope.includes('人工输入'));
    assert(explanation.facts.every(f => f.evidence_refs.length > 0));
    decisions.push(d);
    const job = await compare(d.id, 0);
    assert.equal(job.status, 'ready', JSON.stringify(job));
    assert.equal(job.result.baseline.plan.length, 96);
    assert.equal(job.result.alternative.plan.length, 96);
    assert(Math.abs(job.result.alternative.plan[0].charge_kw) < 1e-6);
    for (const key of Object.keys(record.cost)) {
      assert(Math.abs(job.result.baseline.cost[key] - record.cost[key]) < 1e-6, key);
    }
    summaries.push({site: d.site_id, decision_id: d.id, job_id: job.id,
      device_time: d.snapshot.device_time, observed_at: d.snapshot.observed_at,
      source: d.provenance.input_kind, cost: record.cost,
      alternative_cost: job.result.alternative.cost, delta_eur: job.result.delta_eur,
      first_step: d.replay_context.first_step, control_semantics: d.action.control_semantics});
    console.log(JSON.stringify({site: d.site_id, comparison: job.status, delta_eur: job.result.delta_eur}));
  }
  checks.push('three real captures imported idempotently; evidence references; no artificial inputs or invented execution/settlement');
  checks.push('three independent-worker comparisons ready; full 96-step plans and every baseline cost component match saved solve');
  const full = decisions.find(d => d.snapshot.soc_percent >= d.snapshot.soc_max_percent - 1e-6);
  let boundary = null;
  if (full) {
    const job = await compare(full.id, 1);
    assert.equal(job.status, 'infeasible', JSON.stringify(job));
    boundary = {site: full.site_id, first_charge_kw: 1, status: job.status, job_id: job.id};
    checks.push('captured full battery cannot accept a 1 kW first-step charge under same model constraints');
  }
  const why = await request('/api/questions', {decision_id: decisions[0].id, question: '为什么这样做'});
  assert.equal(why.intent, 'why'); assert(why.evidence_refs.length > 0);
  const unsupported = await request('/api/questions', {decision_id: decisions[0].id, question: '明天一定能赚多少'});
  assert.equal(unsupported.intent, 'unsupported'); assert.equal(unsupported.job, null);
  checks.push('supported question cites evidence; unsupported earnings question does not invent a prediction');
  const result = {status: 'passed', run_id: records[0].run_id, checks, summaries, boundary};
  fs.writeFileSync(path.join(out, 'api-result.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
})().catch(e => {console.error(e); process.exitCode = 1;});
