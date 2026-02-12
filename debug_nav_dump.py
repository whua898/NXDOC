from playwright.sync_api import sync_playwright

URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/hole_making_holem_intros"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto(URL, wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2000)

    for f in page.frames:
        try:
            u = f.url or '<no-url>'
        except Exception:
            u = '<err>'
        try:
            has_nav = f.evaluate("() => !!document.querySelector('#nav-hole_making_holem_intros')")
        except Exception:
            has_nav = False
        if not has_nav:
            continue
        print('Found nav in frame:', u)
        try:
            html = f.evaluate("() => { const el = document.querySelector('#nav-hole_making_holem_intros'); return el ? el.outerHTML.slice(0,2000) : ''; }")
            print('\nouterHTML (first 2000 chars):\n')
            print(html)
        except Exception as e:
            print('outerHTML eval error:', e)
        try:
            links = f.evaluate(
                "() => { const a = Array.from(document.querySelectorAll('#nav-hole_making_holem_intros a')); return a.map(x=>({text: (x.innerText||x.textContent||'').trim().slice(0,200), href: x.href||x.getAttribute('href')})); }"
            )
            print('\nlinks count=', len(links))
            for i,l in enumerate(links[:200]):
                print(i, l)
        except Exception as e:
            print('links eval error:', e)
    b.close()
    print('\ndone')

