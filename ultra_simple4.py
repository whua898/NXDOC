#!/usr/bin/env python3
"""
NX文档聚合器 - 终极完全体 (v9.0 极限性能与防爆版)
核心优化:
1. 完美应用用户自定义的全局配置参数 (NX12/2506 通用)。
2. 支持极速并发、断点续传、全词库无死角底部垃圾清理。
3. 彻底修复侧边栏双重斑马线，加入4px极限缩进、#888单竖线、西门子深蓝字体。
4. 增加极限网络请求拦截 (阻断多余字体与Websocket)。
5. 采用 OOM 防爆流式写入硬盘，支持无限页数生成而不占内存。
"""

import asyncio
from playwright.async_api import async_playwright
import json
import os
import time
import signal
import sys
import random
import hashlib

# ==========================================
# ⚙️ 全局配置区 (Global Configuration)
# ==========================================
START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/feat_based_mach_fbm_overview";
# START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20241101461013487.mfgholemaking/feat_based_mach_fbm_overview"

FINAL_OUTPUT_FILE = "NX12基于特征加工.html"  # 最终生成的单文件 HTML 名称
CACHE_DIR_NAME = "NX12_pages"  # 本地缓存文件夹名称
SIDEBAR_TITLE = "NX12&nbsp;&nbsp;基于特征加工"  # 侧边栏大标题 (&nbsp;代表空格)
MAX_CONCURRENCY = 5  # 🚀 并发线程数量 (推荐: 极速设5，防封锁设2)
NAV_JSON_FILE = "NX12_nav_structure.json"  # 目录结构 JSON 文件名


# ==========================================

class ProcessingStats:
    def __init__(self):
        self.start_time = time.time()
        self.total_pages = 0
        self.processed_pages = 0
        self.success_count = 0
        self.failed_count = 0
        self.redundant_count = 0
        self.interrupted = False
        self.cache_dir = CACHE_DIR_NAME
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_page_cache_path(self, title):
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        if len(safe_title) > 30: safe_title = safe_title[:30]
        hash_suffix = hashlib.md5(title.encode()).hexdigest()[:8]
        return os.path.join(self.cache_dir, f"{safe_title}_{hash_suffix}.json")

    def get_progress_info(self):
        pct = (self.processed_pages / self.total_pages * 100) if self.total_pages > 0 else 0
        elapsed = time.time() - self.start_time
        rate = self.processed_pages / elapsed if elapsed > 0 else 0
        remaining = self.total_pages - self.processed_pages
        eta = f"{int(remaining / rate // 60)}分{int(remaining / rate % 60)}秒" if rate > 0 else "计算中"
        return f"[{pct:.1f}%] 成功:{self.success_count} 映射复用:{self.redundant_count} 失败:{self.failed_count} | ETA: {eta}"


def load_progress():
    if os.path.exists('progress.json'):
        with open('progress.json', 'r') as f: return json.load(f)
    return {'completed': [], 'failed': []}


def save_progress(progress):
    with open('progress.json', 'w') as f: json.dump(progress, f)


async def auto_generate_nav_structure(context):
    print(f"\n🔍 [探测阶段] 正在前往 {START_URL} 探测结构...")
    page = await context.new_page()
    try:
        await page.goto(START_URL, wait_until='domcontentloaded', timeout=90000)
        await page.wait_for_timeout(5000)

        for sel in ['button:has-text("接受")', 'button:has-text("Accept")', '.cookie-consent button']:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible(): await btn.click(timeout=1000)
            except:
                pass

        print("📋 正在自适应识别侧边栏并爆破展开所有节点...")

        nav_data = await page.evaluate(r"""
            async () => {
                function findBestNavRoot() {
                    let root = document.querySelector("ul.doc-topics") || document.querySelector('[role="tree"]');
                    if (root) return root;
                    let allUls = Array.from(document.querySelectorAll('ul'));
                    if (allUls.length === 0) return null;
                    allUls.sort((a,b) => b.querySelectorAll('a').length - a.querySelectorAll('a').length);
                    return allUls[0];
                }

                const treeRoot = findBestNavRoot();
                if (!treeRoot) throw new Error("无法自动识别侧边栏。");

                let lastCount = 0;
                let stuck = 0;
                while (stuck < 6) {
                    const expandables = Array.from(document.querySelectorAll("li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded), li[aria-expanded='false'] > button"));
                    if (expandables.length === 0) {
                        stuck++;
                        await new Promise(r => setTimeout(r, 1000));
                        continue;
                    }
                    for (let el of expandables) {
                        try {
                            el.scrollIntoView({block: 'center', inline: 'nearest'});
                            el.click();
                        } catch(e) {}
                    }
                    await new Promise(r => setTimeout(r, 2000));
                    let currentCount = document.querySelectorAll("li").length;
                    if (currentCount > lastCount) {
                        lastCount = currentCount;
                        stuck = 0;
                    } else {
                        stuck++;
                    }
                }

                function parseLevel(ul) {
                    const result = [];
                    if (!ul) return result;
                    const lis = ul.querySelectorAll(':scope > li');
                    for (let li of lis) {
                        const a = li.querySelector(":scope > a, :scope > div > a, .toc-node-content a");
                        const sub = li.querySelector(":scope > ul") || li.querySelector(":scope > div > ul");
                        if (a) {
                            result.push({ text: a.innerText.trim(), url: a.href, href: a.href, hasChildren: !!sub, children: parseLevel(sub) });
                        } else {
                            const titleSpan = li.querySelector(":scope > span, :scope > div > span");
                            if(titleSpan) {
                                result.push({ text: titleSpan.innerText.trim(), url: "", href: "", hasChildren: !!sub, children: parseLevel(sub) });
                            }
                        }
                    }
                    return result;
                }
                const startUl = treeRoot.tagName === 'UL' ? treeRoot : treeRoot.querySelector('ul');
                return parseLevel(startUl);
            }
        """)
        await page.close()
        return nav_data
    except Exception as e:
        print(f"❌ 自动探测失败: {str(e)}")
        await page.close()
        sys.exit(1)


def generate_tree_navigation(nav_structure, valid_indices, duplicate_map):
    idx_counter = [0]

    def build_tree_html(nodes, level=0):
        if not nodes: return ""
        if level == 0:
            html = '<ul class="root-list active">\n'
        else:
            html = '<ul class="nested">\n'

        for node in nodes:
            text = node.get('text', 'Untitled')
            has_children = node.get('hasChildren', False)
            children = node.get('children', [])

            page_index = idx_counter[0]
            idx_counter[0] += 1

            html += f'    <li class="nav-level-{level}">\n'
            html += '        <div class="nav-item-row">\n'
            if has_children and children:
                html += f'            <span class="caret caret-down" onclick="toggleNode(this)"></span>\n'
            else:
                html += '            <span class="no-caret"></span>\n'

            if page_index in valid_indices:
                html += f'            <a href="#page_{page_index}" onclick="handleManualClick(this)">{text}</a>\n'
            elif page_index in duplicate_map:
                target_id = duplicate_map[page_index]
                html += f'            <a href="#page_{target_id}" onclick="handleManualClick(this)">{text}</a>\n'
            else:
                if has_children and children:
                    html += f'            <span class="nav-text folder-text" onclick="toggleNode(this.previousElementSibling)" title="点击展开/折叠">{text}</span>\n'
                else:
                    html += f'            <span class="nav-text">{text}</span>\n'

            html += '        </div>\n'

            if has_children and children:
                html += build_tree_html(children, level + 1)
            html += '    </li>\n'
        return html + '</ul>\n'

    return build_tree_html(nav_structure)


async def process_page(i, title, url, has_href, context, sem, stats, progress, mode, lock,
                       seen_hashes, seen_css_hashes, global_styles, contents_dict, valid_indices, duplicate_map):
    if stats.interrupted: return

    if not has_href and (not url.strip() or 'javascript' in url.lower() or url.startswith('#')):
        print(f"   ℹ️ [{i + 1}] {title} (📁 纯目录外壳，自动跳过)")
        async with lock:
            if title not in progress['completed']: progress['completed'].append(title)
            stats.processed_pages += 1
            stats.success_count += 1
            if stats.processed_pages % 10 == 0: save_progress(progress)
        return

    skip_reason = ""
    async with lock:
        if title in progress['completed']:
            skip_reason = "已完成"

    if skip_reason == "已完成":
        cache_path = stats.get_page_cache_path(title)
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache_obj = json.load(f)
                    cached_content = cache_obj.get('html', '')
                    cached_css = cache_obj.get('css', '')

                content_hash = hashlib.md5(cached_content.encode()).hexdigest()

                async with lock:
                    if content_hash not in seen_hashes:
                        seen_hashes[content_hash] = i
                        contents_dict[i] = f"<div class=\"page-section\" id=\"page_{i}\">{cached_content}</div>"
                        valid_indices.add(i)

                        if cached_css:
                            css_hash = hashlib.md5(cached_css.encode()).hexdigest()
                            if css_hash not in seen_css_hashes:
                                global_styles.append(cached_css)
                                seen_css_hashes.add(css_hash)

                        stats.success_count += 1
                        print(f"[{i + 1}] {title} (✓ 缓存恢复)")
                    else:
                        duplicate_map[i] = seen_hashes[content_hash]
                        stats.redundant_count += 1
                        print(f"[{i + 1}] {title} (🔗 缓存映射复用)")
                    stats.processed_pages += 1
                return
            except Exception:
                skip_reason = ""
        else:
            skip_reason = ""

    if skip_reason:
        async with lock: stats.processed_pages += 1
        return

    print(f"[{i + 1}] 🚀 开始提取: {title}")

    async with sem:
        page = await context.new_page()

        async def route_intercept(route):
            # 🚀 极限网络断流拦截优化：精准屏蔽多余字体与无用长连接，保留文本与图片高速通行
            block_types = ["media", "beacon", "csp_report", "font", "websocket"]
            if route.request.resource_type in block_types:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_intercept)

        retry_count = 0
        success = False
        extracted_data = ""
        extracted_css = ""

        while retry_count < 3 and not success:
            try:
                if retry_count == 0:
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                else:
                    await page.wait_for_timeout(3000)

                if has_href and url.strip():
                    await page.goto(url, wait_until='domcontentloaded', timeout=35000)
                else:
                    try:
                        await page.click(f'#doc-sidebar li:has-text("{title}") > a', timeout=5000)
                        await page.wait_for_load_state('domcontentloaded', timeout=25000)
                    except Exception as click_error:
                        if url and url.strip():
                            await page.goto(url, wait_until='domcontentloaded', timeout=35000)
                        else:
                            raise Exception("DIRECTORY_NODE_SKIPPED")

                # 依赖evaluate内部的智能等待机制

                cookie_buttons = [
                    'button:has-text("接受所有Cookie")', 'button:has-text("Accept all cookies")',
                    'button:has-text("同意")', '.cookie-consent button', '#cookie-accept-button'
                ]
                for btn_sel in cookie_buttons:
                    try:
                        btn = await page.query_selector(btn_sel)
                        if btn and await btn.is_visible(): await btn.click(timeout=2000)
                    except:
                        pass

                result = await page.evaluate(r"""
                    async () => {
                        const getDoc = () => {
                            let doc = document;
                            const iframes = Array.from(document.querySelectorAll('iframe'));
                            for (const frame of iframes) {
                                if (frame.src && (frame.src.includes('/documentation/') || frame.src.includes('help'))) {
                                    try { if (frame.contentDocument) doc = frame.contentDocument; } catch(e) {}
                                    break;
                                }
                            }
                            if (document.querySelector('#xhtml')) {
                                try { doc = document.querySelector('#xhtml').contentDocument || document; } catch(e) {}
                            }
                            return doc;
                        };

                        let frameDoc = getDoc();
                        let container = null;
                        let retries = 30; 

                        while(retries > 0) {
                            frameDoc = getDoc();
                            container = frameDoc.querySelector('div.doc-content') || 
                                        frameDoc.querySelector('.main.content-container') || 
                                        frameDoc.querySelector('#content');

                            if (container) {
                                let visibleText = container.innerText || "";
                                let textLen = visibleText.replace(/\s+/g, '').length;
                                if (textLen >= 15 && !visibleText.includes('Loading...')) { break; }
                            }
                            await new Promise(r => setTimeout(r, 250));
                            retries--;
                        }

                        if (!container) container = frameDoc.body;
                        if (!container) throw new Error("DOM_CONTAINER_NOT_FOUND");

                        let finalLen = container.innerText.replace(/\s+/g, '').length;
                        if (finalLen < 15) throw new Error("REAL_TEXT_TOO_SHORT");

                        const scrollTargets = [frameDoc.defaultView, window, container, frameDoc.scrollingElement || frameDoc.body].filter(el => el != null);
                        let maxScroll = 5000;
                        scrollTargets.forEach(t => { if (t.scrollHeight && t.scrollHeight > maxScroll) maxScroll = t.scrollHeight; });
                        for(let pos = 0; pos <= maxScroll; pos += 1000) {
                            scrollTargets.forEach(target => {
                                try { if (target.scrollTo) target.scrollTo(0, pos); else target.scrollTop = pos; } catch(e) {}
                            });
                            await new Promise(r => setTimeout(r, 50));
                        }

                        let cssText = '';
                        const styleTags = Array.from(frameDoc.querySelectorAll('style'));
                        for (const style of styleTags) cssText += style.textContent + '\n';

                        const links = Array.from(frameDoc.querySelectorAll('link[rel="stylesheet"]'));
                        for (const link of links) {
                            try {
                                if (link.href && !link.href.includes('google') && !link.href.includes('typekit')) {
                                    const response = await fetch(link.href);
                                    if (response.ok) cssText += await response.text() + '\n';
                                }
                            } catch (e) {}
                        }

                        const clone = container.cloneNode(true);

                        const footerKeywords = [
                            'Learn more', 'How do I', 'Look up more details', 'See also', 'See Also',
                            'Related Concepts', 'Related Reference', 'Related Topics', 'Related Tasks', 'Related Information', 'Related Links',
                            '相关概念', '相关参考', '相关主题', '相关任务', '相关信息', '相关链接',
                            '了解更多', '如何操作', '如何...', '查找更多详细信息', '另请参见'
                        ];

                        Array.from(clone.querySelectorAll('*')).forEach(el => {
                            const txt = (el.textContent || '').trim();
                            if (footerKeywords.includes(txt) && /^(H[1-6]|STRONG|B|DIV|SPAN)$/i.test(el.tagName)) {
                                const wrapper = el.closest('.container-fluid, .related-links, .topic-links, .familylinks');
                                if (wrapper && wrapper !== clone && (wrapper.textContent || '').length < 2000) {
                                    wrapper.remove();
                                } else {
                                    let next = el.nextElementSibling;
                                    while (next) {
                                        let tmp = next;
                                        next = next.nextElementSibling;
                                        tmp.remove();
                                    }
                                    el.remove();
                                }
                            }
                        });

                        const baseUrl = frameDoc.baseURI || document.baseURI;
                        clone.querySelectorAll('*').forEach(el => {
                            if (el.tagName.toLowerCase() === 'table' || el.tagName.toLowerCase() === 'colgroup' || el.tagName.toLowerCase() === 'col') el.removeAttribute('width');
                            if (el.hasAttribute('src')) try { el.src = new URL(el.getAttribute('src'), baseUrl).href; } catch(e) {}
                            if (el.hasAttribute('href')) try { el.href = new URL(el.getAttribute('href'), baseUrl).href; } catch(e) {}
                            if (el.hasAttribute('style')) {
                                let cleanStyle = el.getAttribute('style').replace(/url\(['"]?data:image\/[^)]+['"]?\)/gi, 'none');
                                if (cleanStyle.trim() === '' || cleanStyle === 'none') el.removeAttribute('style');
                                else el.setAttribute('style', cleanStyle);
                            }
                        });

                        return { html: clone.innerHTML.trim(), css: cssText };
                    }
                """)

                extracted_data = result['html']
                extracted_css = result.get('css', '')
                success = True

            except Exception as page_error:
                error_msg = str(page_error)
                if "DIRECTORY_NODE_SKIPPED" in error_msg:
                    print(f"   ℹ️ [{title}] 识别为无内容的目录节点，已自动跳过")
                    async with lock:
                        if title not in progress['completed']: progress['completed'].append(title)
                    break
                elif "REAL_TEXT_TOO_SHORT" in error_msg:
                    error_print = "防封锁拦截：疑似触发IP限流白板，主动重试"
                else:
                    error_print = error_msg[:50].replace('\n', ' ')

                retry_count += 1
                print(f"   ❌ [{title}] 重试 {retry_count}/3: {error_print}")

                if retry_count >= 3:
                    async with lock:
                        if title not in progress['failed']: progress['failed'].append(title)
                        stats.failed_count += 1

        await page.close()

        async with lock:
            if success:
                content_hash = hashlib.md5(extracted_data.encode('utf-8')).hexdigest()

                if content_hash in seen_hashes:
                    duplicate_map[i] = seen_hashes[content_hash]
                    stats.redundant_count += 1
                    print(f"[{i + 1}] {title} (🔗 识别为重复，已建立目录复用映射)")
                else:
                    seen_hashes[content_hash] = i
                    contents_dict[i] = f"<div class=\"page-section\" id=\"page_{i}\">{extracted_data}</div>"
                    valid_indices.add(i)

                    if extracted_css:
                        css_hash = hashlib.md5(extracted_css.encode('utf-8')).hexdigest()
                        if css_hash not in seen_css_hashes:
                            global_styles.append(extracted_css)
                            seen_css_hashes.add(css_hash)

                    stats.success_count += 1
                    print(f"[{i + 1}] {title} (✓ 抓取成功) | {stats.get_progress_info()}")

                if title in progress['failed']: progress['failed'].remove(title)
                if title not in progress['completed']: progress['completed'].append(title)

                if stats.cache_dir:
                    try:
                        with open(stats.get_page_cache_path(title), 'w', encoding='utf-8') as f:
                            json.dump({'html': extracted_data, 'css': extracted_css}, f, ensure_ascii=False)
                    except:
                        pass

            stats.processed_pages += 1
            if stats.processed_pages % 10 == 0: save_progress(progress)


async def main():
    start_time = time.time()
    stats = ProcessingStats()
    progress = None
    browser = None
    seen_hashes = {}
    valid_indices = set()
    duplicate_map = {}
    global_styles = []
    seen_css_hashes = set()
    contents_dict = {}

    lock = asyncio.Lock()

    print("=" * 50)
    print("🚀 NX文档聚合器 - 终极全自动版 (v9.0 极限性能与防爆版)")
    print("=" * 50)
    print("[a] 全自动一键探测 (探测结构 + 并发抓取)")
    print("[c] 增量续传模式 (基于现有 JSON 恢复)")
    print("[r] 彻底重抓模式 (清空旧数据从零开始)")
    mode = input("请选择 [a/c/r] (默认回车为 c 续传): ").strip().lower()
    if not mode or mode not in ['a', 'c', 'r']: mode = 'c'

    if mode == 'r':
        print("🗑️ 正在清洗缓存...")
        if os.path.exists(stats.cache_dir):
            import shutil
            shutil.rmtree(stats.cache_dir)
            os.makedirs(stats.cache_dir, exist_ok=True)
        if os.path.exists('progress.json'): os.remove('progress.json')
        if os.path.exists(NAV_JSON_FILE): os.remove(NAV_JSON_FILE)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 960},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        if mode in ['a', 'r'] or not os.path.exists(NAV_JSON_FILE):
            nav_structure = await auto_generate_nav_structure(context)
            with open(NAV_JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump(nav_structure, f, ensure_ascii=False, indent=2)
            print(f"✅ 目录树识别完毕并已保存至: {NAV_JSON_FILE}")
        else:
            try:
                with open(NAV_JSON_FILE, 'r', encoding='utf-8') as f:
                    nav_structure = json.load(f)
            except:
                print(f"❌ 错误：无法读取 {NAV_JSON_FILE}，请选 [a] 重新探测。")
                sys.exit(1)

        pages = []

    def flatten(nodes):
        for n in nodes:
            pages.append({
                'text': n['text'],
                'href': n.get('href', ''),
                'has_href': bool(n.get('href') and n['href'].strip() and 'javascript:void(0)' not in n.get('href', ''))
            })
            if n.get('children'): flatten(n['children'])

    flatten(nav_structure)

    stats.total_pages = len(pages)
    progress = load_progress()

    if mode == "r":
        print("🗑️ 正在清洗缓存...")
        progress = {'completed': [], 'failed': []}

        if os.path.exists(FINAL_OUTPUT_FILE):
            try:
                os.remove(FINAL_OUTPUT_FILE)
            except:
                pass

        if os.path.exists(stats.cache_dir):
            import shutil
            shutil.rmtree(stats.cache_dir)
            os.makedirs(stats.cache_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 960},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        def cleanup_handler(signum, frame):
            print("\n\n🚨 收到中断信号，保存并退出...")
            stats.interrupted = True
            save_progress(progress)
            sys.exit(0)

        signal.signal(signal.SIGINT, cleanup_handler)
        signal.signal(signal.SIGTERM, cleanup_handler)

        tasks = []
        for i, page_info in enumerate(pages):
            task = asyncio.create_task(
                process_page(i, page_info['text'], page_info['href'], page_info['has_href'],
                             context, sem, stats, progress, mode, lock,
                             seen_hashes, seen_css_hashes, global_styles, contents_dict, valid_indices, duplicate_map)
            )
            tasks.append(task)

        await asyncio.gather(*tasks)

        await browser.close()
        save_progress(progress)

        if contents_dict:
            tree_html = generate_tree_navigation(nav_structure, valid_indices, duplicate_map)
            combined_css = "\n".join(global_styles)

            # 🛡️ OOM 内存防爆：放弃全量拼接，采用流式分步写入硬盘
            with open(FINAL_OUTPUT_FILE, 'w', encoding='utf-8') as f:
                # 1. 优先写入页面头部和侧边栏
                f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Siemens NX 文档库</title>
    <style>{combined_css}</style>
    <style>
        body {{ display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; color: #333; }}

        .nx-sidebar {{ width: 340px; min-width: 250px; overflow-y: auto; padding: 15px 5px; background: #f8f9fa; border-right: 1px solid #dee2e6; }}
        .resizer {{ width: 5px; cursor: col-resize; background: #dee2e6; transition: background 0.2s; }}
        .resizer:hover {{ background: #007cba; }}
        .main-content {{ flex: 1; overflow-y: auto; scroll-behavior: smooth; padding: 0; background: #fff; line-height: 1.6; }}
        .content-wrapper {{ max-width: 1200px; margin: 0 auto; padding: 40px; }}

        .nx-sidebar ul, .nx-sidebar ul.root-list {{ list-style: none; margin: 0; padding: 0; }}
        .nx-sidebar li {{ margin: 2px 0; padding: 0; }}

        .nav-item-row {{ display: flex; align-items: flex-start; margin: 2px 0; }}

        ul.nested {{ 
            display: none; 
            padding-left: 4px; 
            border-left: 1px solid #888; 
            margin-left: 7px; 
            margin-top: 2px;
            margin-bottom: 2px; 
        }}
        ul.active {{ display: block; }}

        .caret {{ cursor: pointer; display: inline-block; width: 14px; min-width: 14px; color: #666; font-size: 10px; margin-top: 6px; text-align: center; }}
        .caret::before {{ content: "▶"; display: inline-block; transition: transform 0.2s; }}
        .caret-down::before {{ transform: rotate(90deg); }}
        .no-caret {{ display: inline-block; width: 14px; min-width: 14px; }}

        .nx-sidebar a, .nav-text {{ 
            text-decoration: none; 
            color: #005f87;
            padding: 3px 6px; 
            border-radius: 4px; 
            transition: background 0.1s, color 0.1s; 
            word-wrap: break-word;
            word-break: normal;
            line-height: 1.4;
            flex: 1; 
            cursor: pointer;
        }}

        .nav-level-0 > .nav-item-row > a, 
        .nav-level-0 > .nav-item-row > span.nav-text {{ font-size: 14px; font-weight: 700; padding-top: 5px; padding-bottom: 5px; }}

        .nav-level-1 > .nav-item-row > a, 
        .nav-level-1 > .nav-item-row > span.nav-text {{ font-size: 13px; font-weight: 600; }}

        .nav-level-2 > .nav-item-row > a, 
        .nav-level-2 > .nav-item-row > span.nav-text {{ font-size: 13px; }}

        .nav-level-3 > .nav-item-row > a, .nav-level-3 > .nav-item-row > span.nav-text,
        .nav-level-4 > .nav-item-row > a, .nav-level-4 > .nav-item-row > span.nav-text,
        .nav-level-5 > .nav-item-row > a, .nav-level-5 > .nav-item-row > span.nav-text {{ font-size: 12px; }}

        .nx-sidebar a:hover, .folder-text:hover {{ background: #e2e8f0; color: #005a84; }}
        .selected-link {{ background: #005a84 !important; color: #fff !important; font-weight: 600 !important; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }}

        .page-section {{ margin-bottom: 80px; padding-top: 30px; border-top: 2px solid #eaeaea; }}
        .page-section:first-child {{ border-top: none; padding-top: 0; }}
        .page-section h1.title {{ font-size: 28px; color: #005a84; margin-top: 0; padding-bottom: 10px; border-bottom: 1px solid #eee; }}
    </style>
</head>
<body>
    <div class="nx-sidebar">
        <h3 style="color: #007cba; border-bottom: 2px solid #007cba; padding-bottom: 10px; margin: 0 10px 10px 10px; text-align: center; font-size: 24px;">{SIDEBAR_TITLE}</h3>
        <div>{tree_html}</div>
    </div>
    <div class="resizer" id="resizer"></div>
    <div class="main-content">
        <div class="content-wrapper">
""")
                # 2. 依次按索引顺序将每页内容直接追加写入硬盘
                for k in sorted(contents_dict.keys()):
                    f.write(contents_dict[k] + "\n")

                # 3. 写入闭合标签和侧边栏控制脚本
                f.write("""        </div>
    </div>
    <script>
        function toggleNode(span) {
            const li = span.closest('li');
            if (!li) return;
            const ul = li.querySelector(':scope > ul.nested');
            if (ul) { 
                ul.classList.toggle("active"); 
                span.classList.toggle("caret-down"); 
            }
        }

        function handleManualClick(a) {
            document.querySelectorAll(".selected-link").forEach(l => l.classList.remove("selected-link"));
            a.classList.add("selected-link");

            const caret = a.previousElementSibling;
            const li = a.closest('li');
            const nestedUl = li ? li.querySelector(':scope > ul.nested') : null;
            if (caret && caret.classList.contains('caret') && nestedUl) {
                nestedUl.classList.toggle('active');
                caret.classList.toggle('caret-down');
            }

            let parent = a.parentElement;
            while (parent && parent.tagName !== 'BODY') {
                if (parent.tagName === 'UL' && parent.classList.contains('nested')) {
                    parent.classList.add('active');
                    const pLi = parent.closest('li');
                    if (pLi) {
                        const pCaret = pLi.querySelector(':scope > .nav-item-row > .caret');
                        if (pCaret) pCaret.classList.add('caret-down');
                    }
                }
                parent = parent.parentElement;
            }
        }

        const resizer = document.getElementById('resizer');
        const sidebar = document.querySelector('.nx-sidebar');
        resizer.onmousedown = () => {
            document.onmousemove = e => {
                if (e.clientX > 200 && e.clientX < 600) sidebar.style.width = e.clientX + 'px';
            };
            document.onmouseup = () => document.onmousemove = null;
        };
    </script>
</body>
</html>""")

        elapsed_sec = int(time.time() - start_time)
        mins, secs = divmod(elapsed_sec, 60)

        print(
            f"\n📊 最终统计: 独立内容写入 {len(contents_dict)} 页 | 目录映射复用 {stats.redundant_count} 页 | 失败 {stats.failed_count} 页")
        print(f"🎉 任务完成！输出文件: {FINAL_OUTPUT_FILE}")
        print(f"⏱️ 总耗时: {mins}分 {secs}秒")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())