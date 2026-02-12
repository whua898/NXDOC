from playwright.sync_api import sync_playwright

URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/hole_making_holem_intros"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    print('Opening', URL)
    page.goto(URL, wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2000)
    print('Frames total:', len(page.frames))
    for i, f in enumerate(page.frames):
        try:
            u = f.url or '<no-url>'
        except Exception:
            u = '<err>'
        print('\n--- frame #{} url={}'.format(i, u))
        # check for nav container
        try:
            has_nav = f.evaluate("() => !!document.querySelector('#nav-hole_making_holem_intros')")
        except Exception as e:
            has_nav = f'<eval-error: {e}>'
        print('has #nav-hole_making_holem_intros:', has_nav)
        try:
            has_link = f.evaluate("() => !!document.querySelector('#nav-hole_making_holem_intros a')")
        except Exception as e:
            has_link = f'<eval-error: {e}>'
        print('has #nav... a:', has_link)
        try:
            tree_count = f.evaluate("() => document.querySelectorAll('[role=\\\"treeitem\\\"]').length")
        except Exception as e:
            tree_count = f'<eval-error: {e}>'
        print('treeitem count:', tree_count)
    b.close()
print('done')
