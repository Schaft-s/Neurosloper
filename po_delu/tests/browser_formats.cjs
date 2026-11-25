const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fs = require('fs'), assert = require('assert').strict;
const os = require('node:os'), path = require('node:path'), child = require('node:child_process');
const base=process.env.EDITOR_TEST_URL || 'http://127.0.0.1:8000', dir=fs.mkdtempSync(path.join(os.tmpdir(),'po-delu-browser-')), text='В рамках мероприятия будет осуществляться проведение демонстрации станка.';
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH || undefined,args:['--no-sandbox']});
  try {
  const context=await browser.newContext({viewport:{width:1360,height:940}});
  const page=await context.newPage(), errors=[], accepted=[];
  page.setDefaultTimeout(40000);
  page.on('pageerror',e=>errors.push(e.message));
  const click=text=>page.getByRole('button',{name:text,exact:true}).click();
  async function review(){
    const mode = page.locator('[name=analysis_mode]');
    const guarded = await mode.isVisible() && await mode.isEnabled();
    if (process.env.EDITOR_TEST_REQUIRE_LOCAL === '1') {
      assert.ok(guarded, 'Local-only smoke requires the visible document consent control');
      assert.equal(await mode.inputValue(), 'local');
      assert.equal(await page.locator('[name=share_document]').isChecked(), false);
    }
    const [response] = await Promise.all([
      page.waitForResponse(r => new URL(r.url()).pathname === '/review'),
      click('Найти шаблонные формулировки')
    ]);
    const job = await response.json();
    assert.ok(response.ok(), job.detail);
    if (guarded) {
      assert.equal(job.detector, 'offline');
      assert.equal(job.document_model_consent, false);
      assert.equal(job.stages.length, 0);
    }
    await page.getByRole('heading',{name:'Замечания',exact:true}).waitFor();
  }
  async function propose(){
    await page.locator('.finding:not(:disabled)').first().check();
    await click('Предложить замены для выбранных');
    await page.getByRole('heading',{name:'Предлагаемые замены'}).waitFor();
    const patches=page.locator('.patch');assert.ok(await patches.count(), await page.locator('#result').innerText());
    assert.equal(await patches.first().isChecked(),false);
    await patches.first().check();
  }
  async function download(label){
    const [file]=await Promise.all([page.waitForEvent('download'),click(label)]);
    return fs.readFileSync(await file.path());
  }
  child.execFileSync(process.env.TEST_PYTHON || 'python3', ['-c',
    "from pathlib import Path;import sys;sys.path.insert(0,'tests');from test_core import sample;files=['.txt','.md','.docx','.pptx','.pdf'];p=Path(sys.argv[1]);[(p/('source'+e)).write_bytes(sample(e)) for e in files]",dir],
    {cwd:path.resolve(__dirname,'..'),env:process.env});
  const html='<!doctype html>\r\n<!-- рукопись -->\r\n<script>window.RUN_MARKER = true</script><p>Документ &amp; '+text+' &#33;</p>\r\n';
  fs.writeFileSync(dir+'/source.html',html);
  for (const ext of ['txt','md','html','docx','pptx','pdf']){
    await page.goto(base);
    await page.locator('input[type=file]').setInputFiles(dir+'/source.'+ext);
    await review();
    assert.deepEqual(await download('Скачать исходный файл'),fs.readFileSync(dir+'/source.'+ext));
    assert.equal(await page.evaluate(()=>window.RUN_MARKER),undefined);
    await propose();
    const selected = page.locator('.patch:checked').first();
    const after = await selected.locator('xpath=ancestor::article').locator('ins').textContent();
    accepted.push({ext, block:await selected.inputValue(), after});
    if(['pptx','pdf'].includes(ext)){
      await click('Посмотреть правки');
      await page.waitForFunction(()=>document.querySelector('article img')?.naturalWidth>0);
    }
    const bytes=await download('Скачать с выбранными правками');
    fs.writeFileSync(dir+'/accepted.'+ext,bytes);
    if(ext==='html')assert.equal(bytes.toString('utf8'),html.replace(text,'На мероприятии покажут станок.'));
    console.log('browser accepted',ext,bytes.length);
  }
  const checkNativeCopies = [
    'from pathlib import Path',
    'import json, sys',
    'from formats import load',
    'folder = Path(sys.argv[1])',
    'for item in json.loads(sys.argv[2]):',
    '    ext = item["ext"]',
    '    original = folder / ("source." + ext)',
    '    native = folder / ("accepted." + ext)',
    '    old = load(original.name, original.read_bytes())[1]',
    '    new = load(native.name, native.read_bytes())[1]',
    '    expected = [item["after"] if b["id"] == item["block"] else b["text"] for b in old]',
    '    assert [b["text"] for b in new] == expected, (ext, "native blocks differ from accepted diff")',
  ].join('\n');
  child.execFileSync(process.env.TEST_PYTHON || 'python3', ['-c',checkNativeCopies,dir,JSON.stringify(accepted)],
    {cwd:path.resolve(__dirname,'..'),env:process.env});
  console.log('all native downloads contain the accepted text and retain neighboring blocks');
  await page.goto(base);
  await click('Личная память');
  await page.locator('#rule-form [name=before]').fill('первый экран');
  await page.locator('#rule-form [name=after]').fill('обзор');
  await page.locator('#rule-form [name=note]').fill('Название страницы справки');
  await click('Сохранить запись');await page.getByText('Запись сохранена.').waitFor();
  await click('Закрыть');
  await page.locator('#draft').fill('Откройте первый экран.');
  await review();await page.getByRole('heading',{name:'Совпадения с личными правилами'}).waitFor();
  await propose();assert.equal((await download('Скачать с выбранными правками')).toString('utf8'),'Откройте обзор.');
  const second=await browser.newContext(); const outsider=await second.newPage();
  await outsider.goto(base);await outsider.getByRole('button',{name:'Личная память'}).click();
  await outsider.getByText('Личных правил пока нет.').waitFor();
  await page.reload();await click('Личная память');await page.getByText('первый экран',{exact:true}).waitFor();
  await click('Удалить');await page.getByText('Личных правил пока нет.').waitFor();
  console.log('memory persisted across reload, isolated browser, opt-in replacement, deletion');
  assert.deepEqual(errors,[]);
  } finally{await browser.close(); fs.rmSync(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
