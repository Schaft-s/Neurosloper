"""Optional: run server first; use CHROMIUM_PATH for a system Chromium."""
import os,tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from test_core import sample
url=os.getenv("EDITOR_TEST_URL","http://127.0.0.1:8473")
with sync_playwright() as pw,tempfile.TemporaryDirectory() as tmp:
    browser=pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH","/usr/bin/chromium"),args=["--no-sandbox"])
    page=browser.new_page()
    page.goto(url)
    page.get_by_text("Вставить пример").click()
    page.get_by_role("button",name="Найти шаблонные формулировки",exact=True).click()
    page.locator(".finding").check()
    page.get_by_text("Предложить замены для выбранных").click()
    page.locator(".patch").check()
    with page.expect_download() as dl:page.get_by_text("Скачать с выбранными правками").click()
    assert "покажут станок" in Path(dl.value.path()).read_text()
    file=Path(tmp)/"test.pptx";file.write_bytes(sample(".pptx"))
    page.locator("input[type=file]").set_input_files(file)
    page.get_by_role("button",name="Найти шаблонные формулировки",exact=True).click()
    page.locator(".finding").check()
    page.get_by_text("Предложить замены для выбранных").click()
    page.locator(".patch").check(timeout=25000)
    page.get_by_text("Посмотреть правки").click()
    image=page.locator("article img[src]")
    expect(image).to_be_visible(timeout=25000)
    expect(image).to_have_js_property("complete",True)
    assert image.evaluate("(node) => node.naturalWidth > 0")
    with page.expect_download() as dl:page.get_by_text("Скачать с выбранными правками").click()
    from formats import load
    assert "покажут" in load("deck.pptx",Path(dl.value.path()).read_bytes())[1][0]["text"]
    browser.close()
    print("OK: keyboard text, selected diff, TXT download, PPTX upload, changed preview and editable PPTX download")
