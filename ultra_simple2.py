#!/usr/bin/env python3
"""
NX文档聚合器 - 终极完全体 (v3.7 可见滚动增强版)
核心特性:
1. 完美断点续传与缓存恢复 (JSON格式支持原生样式)
2. 哈希去重 (防止相同页面多次聚合)
3. 5万SVG彻底切除 (解决 DOM 膨胀)
4. 原生样式提取 + 差异化排版保留 (精准正则过滤Base64毒瘤)
5. 最强弹窗阻击逻辑 (Cookie/GDPR 弹窗完美击杀)
6. 全方位地毯式可见滚动 (100% 触发懒加载图片与长表格)
"""

import asyncio
from playwright.async_api import async_playwright
import json
import os
import time
import signal
import sys
from datetime import datetime
import hashlib


class ProcessingStats:
    def __init__(self):
        self.start_time = time.time()
        self.total_pages = 0
        self.processed_pages = 0
        self.success_count = 0
        self.failed_count = 0
        self.redundant_count = 0
        self.interrupted = False
        self.cache_dir = "siemens_pages"
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_page_cache_path(self, title):
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        if len(safe_title) > 30: safe_title = safe_title[:30]
        hash_suffix = hashlib.md5(title.encode()).hexdigest()[:8]
        return os.path.join(self.cache_dir, f"{safe_title}_{hash_suffix}.json")

    def update_stats(self, success=True, redundant=False):
        self.processed_pages += 1
        if redundant:
            self.redundant_count += 1
        elif success:
            self.success_count += 1
        else:
            self.failed_count += 1

    def get_progress_percentage(self):
        if self.total_pages == 0: return 0
        return (self.processed_pages / self.total_pages) * 100

    def get_eta(self):
        if self.processed_pages == 0: return "未知"
        elapsed = time.time() - self.start_time
        rate = self.processed_pages / elapsed
        remaining = self.total_pages - self.processed_pages
        eta_seconds = remaining / rate if rate > 0 else 0
        return f"{int(eta_seconds // 60)}分{int(eta_seconds % 60)}秒"


def load_progress():
    if os.path.exists('progress.json'):
        with open('progress.json', 'r') as f: return json.load(f)
    return {'completed': [], 'failed': []}


def save_progress(progress):
    with open('progress.json', 'w') as f: json.dump(progress, f)


def generate_tree_navigation(pages, valid_indices):
    try:
        with open('siemens_nav_structure.json', 'r', encoding='utf-8') as f:
            nav_structure = json.load(f)
    except:
        return "<p>未找到导航结构</p>"

    def build_tree_html(nodes, level=0):
        if not nodes: return ""
        html = '<ul class="nested' + (' active"' if level == 0 else '"') + '>\n'
        for node in nodes:
            text = node.get('text', 'Untitled')
            has_children = node.get('hasChildren', False)
            children = node.get('children', [])
            page_index = next((idx for idx, p in enumerate(pages) if p['text'] == text), None)

            html += '    <li>\n'
            if has_children and children:
                html += f'        <span class="caret" onclick="toggleNode(this)"></span>\n'
            else:
                html += '        <span class="no-caret"></span>\n'

            if page_index is not None and page_index in valid_indices:
                html += f'        <a href="#page_{page_index}" onclick="handleManualClick(this)">{text}</a>\n'
            else:
                html += f'        <span class="nav-text" style="color:#aaa;">{text}</span>\n'

            if has_children and children:
                html += build_tree_html(children, level + 1)
            html += '    </li>\n'
        return html + '</ul>\n'

    return build_tree_html(nav_structure)


def signal_handler(signum, frame, stats, progress, browser):
    print("\n\n🚨 收到中断信号，正在优雅退出...")
    stats.interrupted = True
    save_progress(progress)
    if browser: asyncio.create_task(browser.close())
    print(f"📊 进度已保存: 成功{stats.success_count} 重复{stats.redundant_count} 失败{stats.failed_count}")
    sys.exit(0)


async def main():
    stats = ProcessingStats()
    progress = None
    browser = None
    seen_hashes = set()
    valid_indices = set()

    # 存储西门子原生 CSS
    global_styles = []
    seen_css_hashes = set()

    def cleanup_handler(signum, frame):
        signal_handler(signum, frame, stats, progress or {'completed': [], 'failed': []}, browser)

    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)

    print("=" * 50)
    print("🚀 NX文档聚合器 - 终极完全体 (v3.7 可见滚动版)")
    print("=" * 50)
    mode = input("[c] 续传 (跳过已完成，重试失败页面)\n[r] 重抓 (重新处理所有页面)\n请选择模式 [c/r]: ").strip().lower()
    if mode not in ['c', 'r']: mode = 'c'

    with open('siemens_nav_structure.json') as f:
        pages = []

        def flatten(nodes):
            for n in nodes:
                pages.append({
                    'text': n['text'],
                    'href': n.get('href', ''),
                    'has_href': bool(
                        n.get('href') and n['href'].strip() and 'javascript:void(0)' not in n.get('href', ''))
                })
                if n.get('children'): flatten(n['children'])

        flatten(json.load(f))

    stats.total_pages = len(pages)
    progress = load_progress()

    if mode == "r":
        print("🗑️  清理旧进度和输出文件...")
        progress = {'completed': [], 'failed': []}
        for file in os.listdir('.'):
            if file.startswith('nxdump_') and file.endswith('.html'):
                try:
                    os.remove(file)
                except:
                    pass
        if os.path.exists(stats.cache_dir):
            import shutil
            shutil.rmtree(stats.cache_dir)
            os.makedirs(stats.cache_dir, exist_ok=True)
    else:
        print(f"📊 进度分析: 待处理 {len(pages) - len(progress['completed']) - len(progress['failed'])} 个")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.set_viewport_size({'width': 1280, 'height': 960})

        contents = []
        for i, page_info in enumerate(pages):
            title = page_info['text']
            url = page_info['href']
            has_href = page_info['has_href']
            if stats.interrupted: break

            skip_reason = ""
            if title in progress['completed']:
                skip_reason = "已完成"
            elif mode == 'c' and title in progress['failed']:
                skip_reason = "标记为失败"

            # --- 缓存读取逻辑 ---
            if skip_reason == "已完成":
                cache_path = stats.get_page_cache_path(title)
                old_html_path = cache_path.replace('.json', '.html')
                actual_path = cache_path if os.path.exists(cache_path) else (
                    old_html_path if os.path.exists(old_html_path) else None)

                if actual_path and os.path.getsize(actual_path) > 0:
                    try:
                        with open(actual_path, 'r', encoding='utf-8') as f:
                            if actual_path.endswith('.json'):
                                cache_obj = json.load(f)
                                cached_content = cache_obj.get('html', '')
                                cached_css = cache_obj.get('css', '')
                            else:
                                cached_content = f.read()
                                cached_css = ''

                        content_hash = hashlib.md5(cached_content.encode()).hexdigest()
                        if content_hash not in seen_hashes:
                            contents.append(
                                f"<div class=\"page-section\" id=\"page_{i}\"><h2 class=\"page-title\">{title}</h2><div>{cached_content}</div></div>")
                            seen_hashes.add(content_hash)
                            valid_indices.add(i)

                            if cached_css:
                                css_hash = hashlib.md5(cached_css.encode()).hexdigest()
                                if css_hash not in seen_css_hashes:
                                    global_styles.append(cached_css)
                                    seen_css_hashes.add(css_hash)

                            stats.update_stats(success=True)
                            print(f"[{i + 1}/{len(pages)}] {title} (✓ 缓存恢复)")
                        else:
                            stats.update_stats(success=False, redundant=True)
                            print(f"[{i + 1}/{len(pages)}] {title} (⚠️ 缓存去重)")
                    except Exception as e:
                        skip_reason = ""
                else:
                    skip_reason = ""

            if skip_reason: continue

            print(f"[{i + 1}/{len(pages)}] {title}")
            retry_count = 0
            success = False
            extracted_data = ""
            extracted_css = ""

            while retry_count < 3 and not success:
                try:
                    if retry_count > 0: await page.wait_for_timeout(2000)

                    if has_href and url.strip():
                        await page.goto(url, wait_until='domcontentloaded', timeout=40000)
                    else:
                        try:
                            await page.click(f'#doc-sidebar li:has-text("{title}") > a', timeout=10000)
                            await page.wait_for_load_state('domcontentloaded', timeout=30000)
                        except Exception as click_error:
                            if url and url.strip():
                                await page.goto(url, wait_until='domcontentloaded', timeout=40000)
                            else:
                                raise click_error

                    # 恢复原版最强弹窗阻击机制
                    await page.wait_for_timeout(2000)
                    cookie_buttons = [
                        'button:has-text("接受所有Cookie")',
                        'button:has-text("Accept all cookies")',
                        'button:has-text("同意")',
                        '.cookie-consent button',
                        '#cookie-accept-button',
                        '.gdpr-banner button',
                        '[data-testid="cookie-accept"]',
                        '.modal button:first-child'
                    ]

                    popups_handled = 0
                    for btn_sel in cookie_buttons:
                        try:
                            btn = await page.query_selector(btn_sel)
                            if btn and await btn.is_visible():
                                await btn.click(timeout=5000)
                                await page.wait_for_timeout(1000)
                                popups_handled += 1
                                await page.wait_for_timeout(2000)
                        except:
                            pass

                    if popups_handled > 0:
                        print(f"   🎯 成功击杀 {popups_handled} 个遮挡弹窗")

                    # 提取核心 DOM、滚动并清理
                    result = await page.evaluate(r"""
                                                 async () => {
                                                     let frameDoc = document;
                                                     try {
                                                         const iframes = Array.from(document.querySelectorAll('iframe'));
                                                         for (const frame of iframes) {
                                                             if (frame.src && frame.src.includes('/documentation/external/')) {
                                                                 frameDoc = frame.contentDocument || document;
                                                                 break;
                                                             }
                                                         }
                                                         if (document.querySelector('#xhtml')) {
                                                             frameDoc = document.querySelector('#xhtml').contentDocument || document;
                                                         }
                                                     } catch (e) {
                                                     }

                                                     // --- 🌟 增强版：全方位地毯式滚动 (彻底触发所有懒加载) ---
                                                     const scrollTargets = [
                                                         frameDoc.defaultView,                  // iframe 的 window
                                                         window,                                // 主 window
                                                         frameDoc.querySelector('.main.content-container'), // 内容 div
                                                         frameDoc.querySelector('.doc-content'),            // 备用 div
                                                         frameDoc.scrollingElement || frameDoc.body
                                                     ].filter(el => el != null);

                                                     // 动态获取真实的最大高度 (默认至少滚动 5000px)
                                                     let maxScroll = 5000;
                                                     scrollTargets.forEach(t => {
                                                         if (t.scrollHeight && t.scrollHeight > maxScroll) {
                                                             maxScroll = t.scrollHeight;
                                                         }
                                                     });

                                                     // 以较缓的速度(150ms) 和较小的步长(600px) 往下滚，确保肉眼可见且加载充分
                                                     for (let pos = 0; pos <= maxScroll; pos += 600) {
                                                         scrollTargets.forEach(target => {
                                                             try {
                                                                 if (target.scrollTo) target.scrollTo(0, pos);
                                                                 else target.scrollTop = pos;
                                                             } catch (e) {
                                                             }
                                                         });
                                                         await new Promise(r => setTimeout(r, 150));
                                                     }

                                                     // 滚回顶部，恢复页面初始状态
                                                     await new Promise(r => setTimeout(r, 300));
                                                     scrollTargets.forEach(target => {
                                                         try {
                                                             if (target.scrollTo) target.scrollTo(0, 0);
                                                             else target.scrollTop = 0;
                                                         } catch (e) {
                                                         }
                                                     });
                                                     await new Promise(r => setTimeout(r, 300));
                                                     // -----------------------------------------------------------

                                                     const styles = Array.from(frameDoc.querySelectorAll('style'))
                                                         .map(s => s.innerText)
                                                         .join('\n');

                                                     const mainContainer =
                                                         frameDoc.querySelector('.main.content-container') ||
                                                         frameDoc.querySelector('.doc-content') || frameDoc.body;
                                                     if (!mainContainer) throw new Error('未找到内容容器');

                                                     const clone = mainContainer.cloneNode(true);

                                                     const trash = ['svg', 'symbol', 'script', 'style', 'iframe', 'noscript', '.cookie-banner', '#topic-navigator', '#feedback-btns'];
                                                     trash.forEach(sel => clone.querySelectorAll(sel).forEach(el => el.remove()));

                                                     const baseUrl = frameDoc.baseURI || document.baseURI;
                                                     clone.querySelectorAll('*').forEach(el => {
                                                         const keep = ['src', 'href', 'style', 'colspan', 'rowspan', 'class', 'id', 'width', 'height', 'align', 'valign'];
                                                         for (let attr of [...el.attributes]) {
                                                             if (!keep.includes(attr.name)) el.removeAttribute(attr.name);
                                                         }

                                                         if (el.hasAttribute('style')) {
                                                             let originalStyle = el.getAttribute('style');
                                                             let cleanStyle = originalStyle.replace(/url\(['"]?data:image\/[^)]+['"]?\)/gi, 'none');
                                                             if (cleanStyle.trim() === '' || cleanStyle === 'none') {
                                                                 el.removeAttribute('style');
                                                             } else {
                                                                 el.setAttribute('style', cleanStyle);
                                                             }
                                                         }

                                                         if (el.tagName === 'IMG') {
                                                             const src = el.getAttribute('data-src') || el.getAttribute('src');
                                                             if (src && !src.startsWith('data:image')) {
                                                                 try {
                                                                     el.src = new URL(src, baseUrl).href;
                                                                 } catch (e) {
                                                                     el.src = src;
                                                                 }
                                                             }
                                                         }
                                                     });

                                                     return {html: clone.innerHTML.trim(), css: styles};
                                                 }
                                                 """)

                    if not result or not result.get('html') or len(result['html']) < 50:
                        raise Exception("提取内容为空或太短")

                    extracted_data = result['html']
                    extracted_css = result.get('css', '')
                    success = True

                except Exception as page_error:
                    retry_count += 1
                    print(f"   ❌ 重试 {retry_count}/3: {str(page_error)[:80]}")
                    if retry_count >= 3:
                        if title not in progress['failed']: progress['failed'].append(title)
                        stats.update_stats(success=False)

            if success:
                content_hash = hashlib.md5(extracted_data.encode('utf-8')).hexdigest()

                if content_hash in seen_hashes:
                    print(f"   ⚠️ 重复内容，跳过合集")
                    stats.update_stats(success=False, redundant=True)
                else:
                    contents.append(
                        f"<div class=\"page-section\" id=\"page_{i}\"><h2 class=\"page-title\">{title}</h2><div>{extracted_data}</div></div>")
                    seen_hashes.add(content_hash)
                    valid_indices.add(i)

                    if extracted_css:
                        css_hash = hashlib.md5(extracted_css.encode('utf-8')).hexdigest()
                        if css_hash not in seen_css_hashes:
                            global_styles.append(extracted_css)
                            seen_css_hashes.add(css_hash)

                    stats.update_stats(success=True)
                    print(f"   ✓ 成功 | {stats.get_progress_percentage():.1f}% | ETA: {stats.get_eta()}")

                if title in progress['failed']: progress['failed'].remove(title)
                if title not in progress['completed']: progress['completed'].append(title)

                if stats.cache_dir:
                    try:
                        with open(stats.get_page_cache_path(title), 'w', encoding='utf-8') as f:
                            json.dump({'html': extracted_data, 'css': extracted_css}, f, ensure_ascii=False)
                    except:
                        pass

            if (i + 1) % 10 == 0: save_progress(progress)

        await browser.close()
        save_progress(progress)

        if contents:
            tree_html = generate_tree_navigation(pages, valid_indices)
            combined_css = "\n".join(global_styles)

            html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Siemens NX 文档库</title>
    <style>
        {combined_css}
    </style>
    <style>
        body {{ display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: -apple-system, sans-serif; }}
        .nx-sidebar {{ width: 320px; min-width: 250px; overflow-y: auto; padding: 15px 10px; background: #f8f9fa; border-right: 1px solid #dee2e6; }}
        .resizer {{ width: 5px; cursor: col-resize; background: #dee2e6; transition: background 0.2s; }}
        .resizer:hover {{ background: #007cba; }}
        .main-content {{ flex: 1; overflow-y: auto; scroll-behavior: smooth; padding: 0; background: #fff; }}
        .content-wrapper {{ max-width: 1200px; margin: 0 auto; padding: 30px; }}

        .nx-sidebar ul {{ list-style: none; margin: 0; padding: 0; }}
        .nx-sidebar li {{ margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        ul.nested {{ display: none; padding-left: 18px; }}
        ul.active {{ display: block; }}

        .caret {{ cursor: pointer; display: inline-block; width: 20px; color: #666; font-size: 12px; }}
        .caret::before {{ content: "▶"; display: inline-block; transition: transform 0.2s; }}
        .caret-down::before {{ transform: rotate(90deg); }}
        .no-caret {{ display: inline-block; width: 20px; }}

        .nx-sidebar a {{ text-decoration: none; color: #333; font-size: 14px; padding: 4px; border-radius: 4px; }}
        .nx-sidebar a:hover {{ background: #e9ecef; }}
        .selected-link {{ background: #007cba !important; color: #fff !important; font-weight: bold; }}

        .page-section {{ margin-bottom: 60px; padding: 20px 0; border-bottom: 1px dashed #ccc; }}
        .page-title {{ color: #007cba; border-left: 5px solid #007cba; padding-left: 15px; margin-bottom: 20px; background: #f0f7fa; padding: 10px; }}
    </style>
</head>
<body>
    <div class="nx-sidebar">
        <h3 style="color: #007cba; border-bottom: 2px solid #007cba; padding-bottom: 10px;">NX FBM 知识库</h3>
        <div>{tree_html}</div>
    </div>
    <div class="resizer" id="resizer"></div>
    <div class="main-content">
        <div class="content-wrapper">
            {''.join(contents)}
        </div>
    </div>

    <script>
        function toggleNode(span) {{
            const ul = span.parentElement.querySelector(".nested");
            if (ul) {{ ul.classList.toggle("active"); span.classList.toggle("caret-down"); }}
        }}
        function handleManualClick(a) {{
            document.querySelectorAll(".selected-link").forEach(l => l.classList.remove("selected-link"));
            a.classList.add("selected-link");
            let parent = a.parentElement;
            while (parent && parent.tagName !== 'DIV') {{
                if (parent.tagName === 'UL' && parent.classList.contains('nested')) {{
                    parent.classList.add('active');
                    const caret = parent.parentElement.querySelector('.caret');
                    if (caret) caret.classList.add('caret-down');
                }}
                parent = parent.parentElement;
            }}
        }}

        const resizer = document.getElementById('resizer');
        const sidebar = document.querySelector('.nx-sidebar');
        resizer.onmousedown = () => {{
            document.onmousemove = e => {{
                if (e.clientX > 200 && e.clientX < 600) sidebar.style.width = e.clientX + 'px';
            }};
            document.onmouseup = () => document.onmousemove = null;
        }};
    </script>
</body>
</html>"""

            with open('nxdump_final.html', 'w', encoding='utf-8') as f: f.write(html)

        print(
            f"\n📊 最终统计: 独立写入 {len(contents)} 页 | 去重过滤 {stats.redundant_count} 页 | 失败 {stats.failed_count} 页")
        print(f"🎉 任务完成！输出文件: nxdump_final.html")


if __name__ == "__main__":
    asyncio.run(main())