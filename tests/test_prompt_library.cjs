const test = require('node:test');
const assert = require('node:assert/strict');
const {createPromptLibrary} = require('../geo_search_lab/workbench/static/prompt-library.js');

// A small DOM double tests event/data behavior only; it is not browser visual QA.
function setup(fail = false) {
  const elements = new Map();
  function node() { return {value:'',textContent:'',disabled:true,children:[],
    replaceChildren(...children){this.children=children;},append(child){this.children.push(child);}}; }
  const document = {getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id);}, createElement:node};
  const request = {prompt:'原始问题 ±0.02mm',facets:['检测'],constraints:['德国'],experiment_id:'prospect-p01-unbranded-v1',input_mode:'empty_ablation'};
  const library = {companies:[{id:'P01',name:'制造商',cases:[{id:'P01-Q01',title:'无品牌',awareness:'unbranded',variant_of:null,request}]},
    {id:'P02',name:'品牌',cases:[{id:'P02-Q01',title:'另一个问题',awareness:'branded',request:{...request,prompt:'另一问题',experiment_id:'prospect-p02-branded-v1'}}]}]};
  const applied=[], viewed=[];
  const controller=createPromptLibrary({document,fetchLibrary:async()=>{if(fail)throw Error('offline');return library;},applyRequest:r=>applied.push(r),showRecords:g=>viewed.push(g)});
  const get=id=>document.getElementById(id);
  const select=(id,value)=>{get(id).value=value;get(id).onchange();};
  return {controller,get,select,applied,viewed,library};
}

test('preview is separate from loading; loading clones the preset and preserves Unicode', async()=>{
  const s=setup();await s.controller.init();
  s.select('library-company','P01');s.select('library-case','P01-Q01');
  assert.equal(s.applied.length,0);
  assert.match(s.get('library-preview').textContent,/±0.02mm/);
  assert.equal(s.get('load-library-case').disabled,false);
  s.get('load-library-case').onclick();
  s.applied[0].prompt='改写';s.applied[0].facets.push('新维度');
  s.get('load-library-case').onclick();
  assert.equal(s.applied[1].prompt,'原始问题 ±0.02mm');
  assert.deepEqual(s.applied[1].facets,['检测']);
  s.get('library-records').onclick();
  assert.deepEqual(s.viewed,['prospect-p01-unbranded-v1']);
});

test('changing company or clearing selection never loads the previous company case', async()=>{
  const s=setup();await s.controller.init();
  s.select('library-company','P01');s.select('library-case','P01-Q01');
  s.select('library-company','P02');
  assert.equal(s.get('library-case').value,'');
  assert.equal(s.get('load-library-case').disabled,true);
  s.get('load-library-case').onclick();
  assert.equal(s.applied.length,0);
  s.select('library-case','P02-Q01');s.get('load-library-case').onclick();
  assert.equal(s.applied[0].prompt,'另一问题');
  s.select('library-company','');assert.equal(s.get('library-case').disabled,true);
});

test('library failure is visible and does not touch custom input or submit a run', async()=>{
  const s=setup(true);await s.controller.init();
  assert.match(s.get('library-status').textContent,/仍可.*自由输入/);
  assert.equal(s.get('library-company').disabled,true);
  assert.equal(s.applied.length,0);
});
