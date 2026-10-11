// Real browser test in a temporary store. No production reviews or external requests.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const {stopTestServer}=require('./browser_cleanup.cjs');

test('offline research browser: blind review, validation, revisions, analysis and export', {timeout:120000}, async()=>{
  const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'geo-research-browser-'));
  const port=18766,base=`http://127.0.0.1:${port}`;
  const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-X','utf8','scripts/lab.py','--store',tmp,'serve','--offline','--port',String(port)],{cwd:root,windowsHide:true,stdio:'pipe'});
  let logs='';child.stderr.on('data',b=>logs+=b);child.stdout.on('data',b=>logs+=b);
  let browser;
  try{
    let ready=false;
    for(let n=0;n<100;n++){
      if(child.exitCode!==null) throw new Error(logs);
      try{const r=await fetch(base+'/api/v1/health');if(r.ok){ready=true;break;}}catch{}
      await new Promise(r=>setTimeout(r,100));
    }
    assert.ok(ready,logs);
    browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
    const context=await browser.newContext({viewport:{width:1365,height:1000}});
    await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);
    await page.getByRole('button',{name:'创建模拟研究',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.research-item').length===36);
    assert.match(await page.locator('#research-progress').innerText(),/模拟数据/);
    assert.equal(await page.locator('#analyze').isDisabled(),true);
    await page.locator('.research-item').first().click();
    await page.locator('#reviewer').waitFor();
    const detail=await page.locator('#research-detail').innerText();
    assert.ok(!detail.includes('synthetic-demo-v1')&&!detail.includes('not-a-model'));
    // Invalid evidence must show a focusable error and must not save a review.
    const boxes=page.locator('.rubric-row');
    for(let n=0;n<await boxes.count();n++){
      await boxes.nth(n).locator('select').nth(0).selectOption('missing');
      await boxes.nth(n).locator('select').nth(1).selectOption('absent');
    }
    await boxes.first().locator('select').nth(0).selectOption('complete');
    await page.getByRole('button',{name:'保存初评',exact:true}).click();
    await page.locator('#research-notice').waitFor({state:'visible'});
    assert.match(await page.locator('#research-notice').innerText(),/检查证据/);
    await boxes.first().locator('select').nth(0).selectOption('missing');
    await page.getByRole('button',{name:'保存初评',exact:true}).click();
    await page.getByRole('button',{name:'保存新的评分版本',exact:true}).waitFor();
    assert.match(await page.locator('#research-progress').innerText(),/已评 1\/36/);
    await page.getByRole('button',{name:'保存新的评分版本',exact:true}).click();
    await page.getByText('查看 2 个人工评分版本',{exact:true}).waitFor();
    const artifacts=path.join(root,'tests','artifacts');fs.mkdirSync(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'research-review-desktop.png'),fullPage:true});
    const studyId=await page.locator('#studies').inputValue();
    const request=async(p,body)=>{
      const r=await fetch(base+'/api/v1/research'+p,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Lab-Request':'1'},body:body?JSON.stringify(body):undefined});
      assert.equal(r.status,200,await r.clone().text());return r.json();
    };
    const study=await request(`/studies/${studyId}`);
    for(const slot of study.slots){
      if(slot.revision)continue;
      const data=await request(`/studies/${studyId}/slots/${slot.slot_id}`);
      await request(`/studies/${studyId}/slots/${slot.slot_id}/reviews`,{expected_revision:0,reviewer_id:'reviewer-1',labels:data.rubric.map(i=>({item_id:i.item_id,coverage:'uncertain',conflict:'uncertain',evidence:[],note:'synthetic browser test'}))});
    }
    await page.getByRole('button',{name:'刷新',exact:true}).click();
    await page.waitForFunction(()=>!document.getElementById('analyze').disabled);
    await page.getByRole('button',{name:'查看评价',exact:true}).click();
    await page.locator('.research-table').waitFor();
    assert.equal(await page.locator('#export-links a').count(),2);
    const exported=await request(`/studies/${studyId}/export/json`);
    assert.equal(exported.records.length,36);assert.equal(exported.summary.formal_pass,null);
    assert.ok(exported.records.every(r=>r.partition==='development'));
    await page.screenshot({path:path.join(artifacts,'research-analysis-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await page.screenshot({path:path.join(artifacts,'research-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
  }finally{
    if(browser)await browser.close();
    await stopTestServer(child);
    // tmp is created by this test under OS temp; never operate on a user-supplied path.
    const resolved=path.resolve(tmp),tempRoot=path.resolve(os.tmpdir());
    assert.ok(resolved.startsWith(tempRoot+path.sep)&&path.basename(resolved).startsWith('geo-research-browser-'));
    fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:150});
  }
});
