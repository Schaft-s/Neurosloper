/* Run against a local or consent-guarded instance. Every submitted review is local. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const base = process.env.EDITOR_TEST_URL || 'http://127.0.0.1:8000';
const names = ['etiketki.md', 'fonoteka.html', 'korobka.pptx'];
const examples = path.resolve(__dirname, '../examples/showcase');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({viewport: {width: 360, height: 800}});
    const errors = [], reviews = [], external = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => {
      const url = new URL(r.url());
      requests.push([r.method(), url.pathname]);
      if (url.protocol.startsWith('http') && url.origin !== new URL(base).origin) external.push(url.origin);
      if (url.pathname === '/review') reviews.push(r);
    });
    async function press(selector) {
      await page.locator(selector).focus();
      await page.keyboard.press('Enter');
    }
    async function fits() {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    }
    await page.goto(base);
    await page.locator('#draft').fill('Собственный черновик');
    await page.locator('[name=context]').fill('Собственный бриф');
    await press('#tab-showcase');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator(':focus').getAttribute('id'), 'showcase-choice');
    for (const [index, name] of names.entries()) {
      const loaded = page.waitForResponse(r => new URL(r.url()).pathname === '/examples/' + name);
      await page.keyboard.press('Home');
      for (let step = 0; step <= index; step++) await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      const example = await (await loaded).json();
      await page.waitForFunction(n => document.querySelector('#showcase-name').textContent === n, name);
      assert.equal(await page.locator('[name=context]').inputValue(), example.brief);
      assert.equal(await page.locator('#showcase-blocks p').count(), example.blocks.length);
      assert.equal(await page.locator('#showcase-blocks p').first().textContent(), example.blocks[0].text);
      assert.equal(await page.locator('#showcase-blocks img, #showcase-blocks script, #showcase-blocks iframe').count(), 0);
      assert.equal(reviews.length, index);
      if (name === 'korobka.pptx') {
        assert.equal(example.slides, 3);
        const images = [];
        for (const slide of [0, 1, 2]) {
          if (slide) await page.locator('#showcase-page').selectOption(String(slide));
          await page.waitForFunction(n => {
            const img = document.querySelector('#showcase-image');
            return img.alt === 'Слайд ' + n && img.naturalWidth > 0;
          }, slide + 1);
          images.push(await page.locator('#showcase-image').screenshot());
        }
        assert.notDeepEqual(images[0], images[1]);
        assert.notDeepEqual(images[1], images[2]);
        assert.equal(reviews.length, index);
      }
      const requestCount = requests.length;
      await press('#open-saved');
      const saved = page.getByRole('dialog', {name:'Сохранённый прогон'});
      await saved.waitFor();
      assert.equal(await saved.getByText('Подтверждённое замечание', {exact:true}).count(), 1);
      assert.equal(await saved.getByText('Спорный фрагмент', {exact:true}).count(), name === 'fonoteka.html' ? 1 : 0);
      assert.ok((await saved.innerText()).includes('25.11.2026'));
      assert.ok((await saved.innerText()).includes('Замена отклонена проверкой'));
      if (name === 'etiketki.md') {
        assert.equal(await saved.locator('ins').textContent(), example.saved_review.edits.find(e => e.status === 'verified_native').after);
        assert.ok((await saved.innerText()).includes('при редакторской сверке этот вариант отклонён'));
      }
      await saved.locator('summary').first().focus();
      await page.keyboard.press('Enter');
      assert.ok(await saved.locator('.ballot').first().isVisible());
      await fits();
      await press('#close-saved');
      assert.equal(await saved.isVisible(), false);
      assert.equal(await page.locator(':focus').getAttribute('id'), 'open-saved');
      assert.equal(requests.length, requestCount, 'Opening and closing the saved review made a network request');
      assert.equal(await page.locator('#result').innerText(), '');
      assert.equal(await page.locator('[name=share_document]').isChecked(), false);
      assert.equal(reviews.length, index);
      const file = await fs.readFile(path.join(examples, name));
      const downloaded = page.waitForEvent('download');
      await press('#showcase-download');
      assert.deepEqual(await fs.readFile(await (await downloaded).path()), file);

      const model = page.locator('[name=analysis_mode]');
      assert.equal(await model.inputValue(), 'local');
      await model.selectOption('models');
      assert.equal(await page.locator('[name=share_document]').isChecked(), false);
      await press('#review');
      assert.equal(await page.locator(':focus').getAttribute('name'), 'share_document');
      assert.equal(reviews.length, index);
      await model.selectOption('local');
      const reply = page.waitForResponse(r => new URL(r.url()).pathname === '/review');
      await press('#review');
      const job = await (await reply).json();
      await page.waitForFunction(() => document.activeElement.id === 'review-results');
      assert.equal(job.name, name);
      assert.equal(Object.hasOwn(job, 'saved_review'), false);
      assert.equal(job.revision_hash, example.revision);
      assert.equal(job.detector, 'offline');
      assert.equal(job.document_model_consent, false);
      assert.deepEqual(job.stages, []);
      assert.equal(job.context, example.brief);
      assert.equal(job.review_view.incomplete, false);
      assert.deepEqual(job.blocks.map(b => b.text), example.blocks.map(b => b.text));
      assert.equal(await page.locator('.overview strong').textContent(), job.review_view.state);
      await fits();

      if (name === 'korobka.pptx') {
        await page.getByRole('button', {name: 'Посмотреть оригинал'}).click();
        await page.waitForFunction(() => document.querySelector('article img')?.naturalWidth > 0);
        await fits();
      }
      await page.locator('#showcase-choice').focus();
      console.log('example opened and reviewed locally:', name, example.blocks.length, 'blocks');
    }
    await page.locator('[name=analysis_mode]').selectOption('models');
    await page.locator('[name=share_document]').check();
    await press('#tab-text');
    assert.equal(await page.locator('#draft').inputValue(), 'Собственный черновик');
    assert.equal(await page.locator('[name=context]').inputValue(), 'Собственный бриф');
    assert.equal(await page.locator('[name=analysis_mode]').inputValue(), 'local');
    assert.equal(await page.locator('[name=share_document]').isChecked(), false);
    await page.setViewportSize({width: 1280, height: 900});
    await press('#example');
    assert.equal(await page.locator(':focus').getAttribute('id'), 'draft');
    await fits();
    assert.equal(reviews.length, 3);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    console.log('Saved reports opened without network requests. Original bytes, keyboard, 360px, consent and custom draft restored.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
