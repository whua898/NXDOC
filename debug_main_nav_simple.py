from playwright.sync_api import sync_playwright

URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/hole_making_holem_intros"
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    page = b.new_page()
    page.goto(URL, wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(3000)
    try:
        html = page.evaluate("() => { const el = document.querySelector('#nav-hole_making_holem_intros'); return el ? el.outerHTML : null; }")
        print('MAIN outerHTML length:', len(html) if html else 'None')
        if html:
            print(html[:2000])
    except Exception as e:
        print('eval error:', e)
    b.close()
print('done')

