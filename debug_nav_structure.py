from playwright.sync_api import sync_playwright
import time

URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/hole_making_holem_intros"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print(f"Navigating to {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        
        # Wait for content frame
        print("Waiting for content frame...")
        content_frame = None
        for _ in range(20):
            for f in page.frames:
                if "/documentation/external/" in f.url:
                    content_frame = f
                    break
            if content_frame:
                break
            time.sleep(1)
            
        if not content_frame:
            print("Content frame not found.")
            return

        print(f"Content frame found: {content_frame.url}")
        
        # Dump some structure info
        print("--- Content Frame Body Snippet ---")
        body_html = content_frame.evaluate("document.body.innerHTML")
        print(body_html[:2000]) # Print first 2000 chars
        
        print("\n--- Potential Nav Elements ---")
        # Check for common nav selectors
        selectors = [
            '[role="treeitem"]',
            '.toc',
            '#toc',
            'nav',
            'ul',
            'li',
            'a'
        ]
        
        for sel in selectors:
            count = content_frame.locator(sel).count()
            print(f"Selector '{sel}': {count} found")
            if count > 0 and count < 10:
                # Print details if few
                print(content_frame.evaluate(f"""(sel) => {{
                    return Array.from(document.querySelectorAll(sel)).map(e => e.outerHTML.substring(0, 100));
                }}""", sel))

        browser.close()

if __name__ == "__main__":
    main()