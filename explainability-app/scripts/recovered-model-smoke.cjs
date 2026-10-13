/* Run against a verify-model database, with its independent worker running. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
  const base=process.env.EXPLAINABILITY_URL || 'http://127.0.0.1:8080';
  const out=path.resolve('artifacts/recovered-browser'); fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  const checks=[];
  try{
    const records=JSON.parse(fs.readFileSync('docs/examples/recovered-model-validation.json','utf8'));
    for(const record of records){
      const response=await page.request.post(base+'/api/decisions',{data:record});
      assert.equal(response.status(),200,await response.text());
      const d=await response.json(); assert.equal(d.capabilities.counterfactual,true);
      assert.deepEqual(d.execution_events,[]); assert.equal(d.settlement,null);
    }
    checks.push('full external decision import using recovered model; no execution or settlement invented');
    await page.goto(base,{waitUntil:'networkidle'});
    await page.locator('#run-select').selectOption('recovered-model-validation-v1');
    await page.getByRole('heading',{name:'model-4-step',exact:true}).waitFor();
    assert.equal(await page.locator('.station-card').count(),3);
    assert.equal(await page.locator('.station-card .source-badge').filter({hasText:'模型接入验证 · 人工输入'}).count(),3);
    await page.screenshot({path:path.join(out,'overview.png'),fullPage:true});
    checks.push('4/24/96-step overview explicitly identifies synthetic validation inputs');
    await page.locator('.station-card').filter({hasText:'model-4-step'}).getByRole('button',{name:'查看决策依据'}).click();
    await page.getByRole('heading',{name:'model-4-step · 决策依据'}).waitFor();
    await page.getByText('€0.564947',{exact:true}).waitFor();
    assert.match(await page.locator('.fact-list').innerText(),/电池充电/);
    assert.match(await page.locator('.scope-note').first().innerText(),/不是实际遥测/);
    await page.screenshot({path:path.join(out,'detail.png'),fullPage:true});
    checks.push('original free optimum baseline 0.564947 EUR; full first-step evidence shown');
    await page.getByLabel('首步电池充电功率').fill('0');
    await page.getByRole('button',{name:'计算替代方案',exact:true}).click();
    await page.getByRole('heading',{name:'+€0.815158',exact:true}).waitFor({timeout:40000});
    await page.getByText('€1.380105',{exact:true}).waitFor();
    await page.getByText('模型接入验证 · 人工输入',{exact:true}).waitFor();
    assert.equal(await page.locator('.scenario-panel tbody tr').count(),8);
    await page.screenshot({path:path.join(out,'comparison.png'),fullPage:true});
    checks.push('API queue → independent worker → recovered MILP → comparison UI');
    const overview=await(await page.request.get(base+'/api/stations')).json();
    const tasks=[];
    for(const station of overview.stations){
      const response=await page.request.post(base+'/api/decisions/'+station.id+'/counterfactuals',{data:{first_charge_kw:0}});
      assert.equal(response.status(),202); tasks.push([station.site_id,(await response.json()).id]);
    }
    const summaries=[];
    for(const [site,id] of tasks){
      let job;
      for(let attempt=0;attempt<140;attempt++){
        job=await(await page.request.get(base+'/api/counterfactuals/'+id)).json();
        if(!['queued','running'].includes(job.status))break;
        await new Promise(resolve=>setTimeout(resolve,250));
      }
      assert.equal(job.status,'ready',JSON.stringify(job));
      const n=Number(site.match(/\d+/)[0]); assert.equal(job.result.alternative.plan.length,n);
      summaries.push({site,steps:n,baseline:job.result.baseline.cost.objective_eur,
        alternative:job.result.alternative.cost.objective_eur,delta_eur:job.result.delta_eur});
    }
    checks.push('all three recovered-model comparisons complete, including negative prices and nonzero PV');
    assert.deepEqual(errors,[]);
    const result={checks,summaries,errors}; fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
