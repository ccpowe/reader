import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const source = JSON.parse(readFileSync(resolve('domain/translationRuntimeSources.generated.ts'), 'utf8').match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [resolve('domain/webTranslationRules.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
async function start(page, html, selectedRules = rules) {
  await page.setContent(html);
  await page.evaluate(({ source, rules }) => {
    window.messages=[]; window.ReactNativeWebView={postMessage(raw){window.messages.push(JSON.parse(raw));}};
    (0,eval)(`(${source})`)({channelToken:'rich-parts',initialMode:'bilingual',rules});
    window.__readerTranslationBridge.confirmEpoch(window.messages[0].document_epoch);
  },{source,rules:selectedRules});
}
const segments = page => page.evaluate(() => [...new Map(window.messages.filter(m=>m.type==='reader_translation_batch_plan').flatMap(m=>m.batches.flatMap(b=>b.segments)).map(s=>[s.segment_id,s])).values()]);
async function reply(page, values) { await page.evaluate(values=>window.__readerTranslationBridge.applyTranslations(values.map(s=>({segment_id:s.segment_id,source_text:s.text,translated_text:s.text,translation_status:'succeeded'}))),values); }
test('long linked paragraph uses paired bounded parts, progresses beyond ten slots and renders once',async({page})=>{
  const text='A meaningful Unicode paragraph 😀 carries complete words and punctuation. '.repeat(1200);
  await start(page,`<p id="source">Beginning prose <a href="https://example.org/reference">${text}</a> Ending prose.</p>`);
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(1);
  const accepted=new Map();
  for(let attempt=0;attempt<20;attempt++){
    const fresh=(await segments(page)).filter(s=>!accepted.has(s.segment_id));
    fresh.forEach(s=>accepted.set(s.segment_id,s)); await reply(page,fresh); await page.waitForTimeout(100);
    if(await page.locator('.reader-translation-node').count()) break;
  }
  expect(accepted.size).toBeGreaterThan(10);
  for(const s of accepted.values()) { expect(s.text.length).toBeLessThanOrEqual(7500); expect((s.text.match(/READER_OPEN/g)||[]).length).toBe((s.text.match(/READER_CLOSE/g)||[]).length); }
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  expect((await page.locator('.reader-translation-node').innerText()).replace(/\s+/g,' ').trim()).toBe(`Beginning prose ${text} Ending prose.`.replace(/\s+/g,' ').trim());
  await expect(page.locator('#source')).toContainText(text);
});
test('strong and emphasis semantics survive controlled translation',async({page})=>{
  await start(page,'<p id="source">Meaningful opening <strong>important prose <em>emphasized words</em></strong> ending sentence.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBe(1); await reply(page,await segments(page));
  await expect(page.locator('.reader-translation-node strong em')).toHaveText('emphasized words');
});
test('changed href and base update an existing translated link without another text request',async({page})=>{
  await start(page,'<base href="https://example.org/old/"><p id="source">Meaningful opening <a href="reference" target="_blank">linked prose</a> ending sentence.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBe(1); await reply(page,await segments(page));
  await expect(page.locator('.reader-translation-node a')).toHaveCount(1);
  await page.evaluate(()=>{window.saved=document.querySelector('.reader-translation-node');document.querySelector('#source a').href='updated';document.querySelector('base').href='https://example.org/new/';});
  await expect(page.locator('.reader-translation-node a')).toHaveAttribute('href','updated');
  expect(await page.locator('.reader-translation-node a').evaluate(a=>a.href)).toBe('https://example.org/new/updated');
  expect(await page.evaluate(()=>window.saved===document.querySelector('.reader-translation-node'))).toBe(true);
  expect((await segments(page)).length).toBe(1);
});
test('one failed part retries alone and never hides an incomplete paragraph',async({page})=>{
  await start(page,`<p id="source">${'A complete meaningful sentence remains visible. '.repeat(500)}</p>`);
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(1);
  const all=await segments(page), failed=all[1];
  await reply(page,all.filter(s=>s!==failed));
  await page.evaluate(s=>window.__readerTranslationBridge.applyTranslations([{segment_id:s.segment_id,source_text:s.text,translation_status:'failed',error_code:'provider_error'}]),failed);
  await page.waitForTimeout(100); await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await page.evaluate(()=>{window.messages=[];window.__readerTranslationBridge.retryFailed();});
  await expect.poll(async()=> (await segments(page)).length).toBe(1);
  expect((await segments(page))[0].segment_id).toBe(failed.segment_id);
  await reply(page,[failed]); await expect(page.locator('.reader-translation-node')).toHaveCount(1);
});
test('BR and pre-wrap preserve explicit line semantics without duplicate projected prose',async({page})=>{
  await start(page,'<div id="source" style="white-space:pre-wrap">Opening meaningful text <span>first meaningful line<br>second meaningful line</span> ending prose.\nAnother explicit meaningful line.</div>');
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(0);
  const all=await segments(page); await reply(page,all);
  expect(all.map(s=>s.text).join(' ')).toContain('\nAnother');
  await expect(page.locator('.reader-translation-node br')).toHaveCount(1);
  expect((all.map(s=>s.text).join(' ').match(/first meaningful line/g)||[]).length).toBe(1);
  expect((all.map(s=>s.text).join(' ').match(/second meaningful line/g)||[]).length).toBe(1);
});
test('inline flex with mixed block descendants has unique ownership of every source phrase',async({page})=>{
  await start(page,'<p id="source">Opening meaningful phrase <span style="display:inline-flex"><span>Inline meaningful phrase <span style="display:block">Independent block phrase</span> Tail meaningful phrase</span></span> Closing meaningful phrase.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(0);
  const all=await segments(page), text=all.map(s=>s.text).join(' ');
  for(const phrase of ['Opening meaningful phrase','Inline meaningful phrase','Independent block phrase','Tail meaningful phrase','Closing meaningful phrase']) expect(text.split(phrase).length-1).toBe(1);
  await reply(page,all); await expect(page.locator('.reader-translation-node')).toHaveCount(all.length);
});
test('queued commits reject same-text source replacement and read the latest mode',async({page})=>{
  await start(page,'<p id="source">Meaningful opening <a href="https://example.org/old">linked prose</a> ending sentence.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBe(1);
  const [part]=await segments(page);
  await page.evaluate(part=>{
    window.__readerTranslationBridge.applyTranslations([{segment_id:part.segment_id,source_text:part.text,translated_text:part.text,translation_status:'succeeded'}]);
    const link=document.querySelector('#source a'); link.replaceWith(link.cloneNode(true));
    window.__readerTranslationBridge.setMode('translated');
  },part);
  await page.waitForTimeout(100); await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(1);
  const latest=(await segments(page)).at(-1); await reply(page,[latest]);
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await expect(page.locator('#source')).toHaveClass(/reader-source-hidden/);
});
test('template rule activation and removal preserve the document epoch and use source DOM',async({page})=>{
  await page.route('https://example.test/**',route=>route.fulfill({contentType:'text/html',body:'<body></body>'})); await page.goto('https://example.test/read');
  const { compileWebTranslationRule }=await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
  const conditional=compileWebTranslationRule({id:'template',priority:100,matches:[{host:'example.test',requiredSelectors:['[data-template="story"]']}],segmentation:{exclude:{add:['.support']}}});
  await start(page,'<main><p>Stable main paragraph remains translated.</p><p class="support">Supporting paragraph uses conditional exclusion.</p></main>',[conditional,...rules]);
  await expect.poll(async()=> (await segments(page)).length).toBe(2); await reply(page,await segments(page));
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  await page.evaluate(()=>{window.mainTranslation=document.querySelector('.reader-translation-node');document.querySelector('main').setAttribute('data-template','story');});
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  expect(await page.evaluate(()=>window.mainTranslation===document.querySelector('.reader-translation-node'))).toBe(true);
  await expect(page.locator('html')).toHaveAttribute('data-reader-translation-profile','template');
  await page.evaluate(()=>document.querySelector('main').removeAttribute('data-template'));
  await expect(page.locator('html')).toHaveAttribute('data-reader-translation-profile','generic');
  expect(await page.evaluate(()=>new Set(window.messages.map(m=>m.document_epoch)).size)).toBe(1);
});
test('unsafe href changes remove old destinations and safe targets preserve blank rel',async({page})=>{
  await start(page,'<p>Meaningful opening <a href="https://example.org/safe" target="_blank">linked prose</a> ending sentence.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBe(1); await reply(page,await segments(page));
  await expect(page.locator('.reader-translation-node a')).toHaveAttribute('rel','noopener noreferrer');
  await page.locator('p a').evaluate(a=>a.setAttribute('href','java\nscript:alert(1)'));
  await expect(page.locator('.reader-translation-node a')).not.toHaveAttribute('href');
  await page.locator('p a').evaluate(a=>{a.href='https://example.org/new';a.target='_self';});
  await expect(page.locator('.reader-translation-node a')).toHaveAttribute('href','https://example.org/new');
  await expect(page.locator('.reader-translation-node a')).not.toHaveAttribute('rel');
});
test('transport plans bound JSON-escaped payloads across current and adjacent windows',async({page})=>{
  const text=('Meaningful quoted "phrase" \\ repeated with words. ').repeat(1800);
  await start(page,Array.from({length:3},(_,i)=>`<p style="position:absolute;top:${i*300}px;height:100px;overflow:hidden">${text}</p>`).join(''));
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(1);
  const sizes=await page.evaluate(()=>window.messages.filter(m=>m.type==='reader_translation_batch_plan').map(m=>JSON.stringify(m).length));
  expect(Math.max(...sizes)).toBeLessThan(100000);
});
test('token splitting preserves exact Unicode and newline boundaries after independent trimming',async()=>{
  const partBundle=buildSync({entryPoints:[resolve('domain/webTranslationParts.ts')],bundle:true,format:'esm',platform:'node',write:false}).outputFiles[0].text;
  const { splitWebTranslationPayload:split }=await import(`data:text/javascript;base64,${Buffer.from(partBundle).toString('base64')}`);
  const removeTokens=text=>text.replace(/⟪READER_(?:(?:OPEN|CLOSE)_)?\d+⟫/g,'');
  for(const payload of ['😀'.repeat(1000),('Meaningful prose\n\nAnother line 😀\n').repeat(200).trim(),`Opening ⟪READER_OPEN_0⟫${'linked words\n'.repeat(500)}⟪READER_CLOSE_0⟫ ending.`]){
    const parts=split(payload,256); expect(parts).not.toBeNull();
    expect(parts.every(part=>part.text.length<=256)).toBe(true);
    expect(parts.map(part=>part.joiner+removeTokens(part.text.trim())).join('')).toBe(removeTokens(payload));
    expect(parts.every(part=>!/[\uD800-\uDBFF]$/.test(part.text)&&! /^[\uDC00-\uDFFF]/.test(part.text))).toBe(true);
  }
  expect(split('x'.repeat(10000),64,2)).toBeNull();
});
test('commit validation catches display segmentation changes before old translation insertion',async({page})=>{
  await start(page,'<p>Opening meaningful text <span id="middle">middle meaningful text</span> ending meaningful text.</p>');
  await expect.poll(async()=> (await segments(page)).length).toBe(1);
  const [part]=await segments(page);
  await page.evaluate(part=>{
    window.__readerTranslationBridge.applyTranslations([{segment_id:part.segment_id,source_text:part.text,translated_text:'OBSOLETE MERGED RESULT',translation_status:'succeeded'}]);
    document.querySelector('#middle').style.display='block';
  },part);
  await page.waitForTimeout(100); await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await expect.poll(async()=> (await segments(page)).length).toBeGreaterThan(1);
});
test('paragraph cap reports a bounded terminal budget state while preserving source content',async({page})=>{
  await start(page,`<p id="source">${'Meaningful enormous paragraph content. '.repeat(27000)}</p>`);
  await expect.poll(()=>page.evaluate(()=>window.messages.some(m=>m.type==='reader_translation_status'&&m.state==='budget_exhausted'))).toBe(true);
  expect((await segments(page)).length).toBe(0);
  await expect(page.locator('#source')).toBeVisible();
  await page.evaluate(()=>window.__readerTranslationBridge.retryFailed()); await page.waitForTimeout(200);
  expect((await segments(page)).length).toBe(0);
});
test('render-only template activation updates a successful layout without provider replay',async({page})=>{
  await page.route('https://example.test/**',route=>route.fulfill({contentType:'text/html',body:'<body></body>'})); await page.goto('https://example.test/read');
  const { compileWebTranslationRule }=await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
  const conditional=compileWebTranslationRule({id:'layout',priority:100,matches:[{host:'example.test',requiredSelectors:['[data-template="expanded"]']}],rendering:{unclamp:{add:['#card']}}});
  await start(page,'<div id="card" style="width:280px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden"><p>Meaningful source paragraph with translation and sufficient additional meaningful prose to exceed two visible lines in this natural flow container.</p></div>',[conditional,...rules]);
  await expect.poll(async()=> (await segments(page)).length).toBe(1); await reply(page,await segments(page));
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await page.evaluate(()=>{window.saved=document.querySelector('.reader-translation-node');document.body.setAttribute('data-template','expanded');});
  await expect(page.locator('html')).toHaveAttribute('data-reader-translation-profile','layout');
  await expect.poll(()=>page.locator('#card').evaluate(node=>getComputedStyle(node).webkitLineClamp)).toBe('none');
  expect(await page.evaluate(()=>window.saved===document.querySelector('.reader-translation-node'))).toBe(true);
  expect((await segments(page)).length).toBe(1);
});
