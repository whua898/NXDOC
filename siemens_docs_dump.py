from playwright.sync_api import sync_playwright
import os
import time
from urllib.parse import urlparse, urljoin
import json
import re
import base64
import shutil

# --- 配置 ---
URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/hole_making_holem_intros"
OUT_HTML = "siemens_docs_all.html"
PAGES_DIR = "siemens_pages"
STATE_FILE = "siemens_docs_state.json"

def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).lower() not in ('0', 'false', 'no', 'off', '')

HEADLESS = _env_bool('HEADLESS', True)
COOKIE_ACTION = (os.environ.get('COOKIE_ACTION') or 'accept').strip().lower()


# --- 辅助函数 ---

def _selectors_for_action(action='accept'):
    action = (action or 'accept').lower()
    if action == 'reject':
        return [
            "button:has-text(\"拒绝\")", "button:has-text(\"拒绝所有\")",
            "button:has-text(\"Reject all\")", "button:has-text(\"Decline\")",
        ]
    elif action == 'none':
        return []
    else: # accept
        return [
            "button:has-text(\"接受\")", "button:has-text(\"同意\")",
            "button:has-text(\"Accept all\")", "button:has-text(\"Accept\")",
        ]

def accept_cookies(page_or_frame, action=COOKIE_ACTION):
    """在给定的页面或框架中尝试接受/拒绝 cookies。"""
    selectors = _selectors_for_action(action)
    for sel in selectors:
        try:
            loc = page_or_frame.locator(sel).first
            if loc.is_visible(timeout=1000):
                loc.click(force=True, timeout=3000)
                print(f"✅ 已点击 Cookie 按钮 (selector: {sel})")
                page_or_frame.wait_for_timeout(1000) # 等待动画
                return True
        except Exception:
            continue
    return False

def get_content_frame(page, timeout=30000):
    """定位并返回文档内容的 iframe。"""
    print("🔍 正在定位内容 iframe...")
    start_time = time.time()
    while time.time() - start_time < timeout / 1000:
        for frame in page.frames:
            try:
                if "/documentation/external/" in frame.url:
                    print(f"✅ 找到内容 iframe: {frame.url}")
                    return frame
            except Exception:
                continue
        page.wait_for_timeout(500)
    raise RuntimeError("❌ 未能找到文档内容 iframe。")

def find_nav_frame(page):
    """查找包含目录树的 frame。"""
    print("🔍 正在查找目录树 frame...")
    
    nav_selectors = [
        '[role="treeitem"]', 
        '.toc-tree', 
        '.nav-tree', 
        '#toc-tree',
        'div[class*="TreeView"]',
        'ul[class*="toc"]'
    ]

    for sel in nav_selectors:
        if page.locator(sel).count() > 0:
            print(f"✅ 目录树在主页面中 (selector: {sel})。")
            return page, sel
    
    for frame in page.frames:
        try:
            for sel in nav_selectors:
                if frame.locator(sel).count() > 0:
                    print(f"✅ 目录树在 frame 中: {frame.url} (selector: {sel})")
                    return frame, sel
        except Exception:
            continue
            
    print("⚠️ 未找到明显的目录树结构。")
    return None, None

def expand_all_tree_nodes(frame, selector_hint=None):
    """在给定的 frame 中展开所有可折叠的目录树节点。"""
    print("🌳 正在展开所有目录节点...")
    
    expanded_count = 0
    for _ in range(20): 
        try:
            nodes = frame.locator('[aria-expanded="false"]:visible')
            count = nodes.count()
            
            if count == 0:
                nodes = frame.locator('.collapsed:visible, .expandable:not(.expanded):visible')
                count = nodes.count()
            
            if count == 0:
                print("✅ 没有更多可展开的节点。")
                return

            print(f"  - 发现 {count} 个未展开的节点，正在点击...")
            
            for i in range(min(count, 20)):
                try:
                    node = nodes.nth(i)
                    icon = node.locator('svg, .icon, .toggle').first
                    if icon.is_visible():
                        icon.click(timeout=1000)
                    else:
                        node.click(timeout=1000)
                    expanded_count += 1
                    frame.wait_for_timeout(200)
                except Exception:
                    pass
            
            frame.wait_for_timeout(1000) 
        except Exception as e:
            print(f"  - 警告: 在展开目录时发生错误: {e}")
            break
    print(f"ℹ️ 总共展开了 {expanded_count} 个节点。")

def collect_nav_links(frame, selector_hint=None):
    """从目录树中收集所有导航链接。"""
    print("🔗 正在收集导航链接...")
    try:
        links = frame.evaluate("""
            (selector_hint) => {
                const links = [];
                let root = document;
                
                if (selector_hint) {
                    const el = document.querySelector(selector_hint);
                    if (el) {
                        root = el.closest('nav') || el.closest('.toc') || el.closest('[role="tree"]') || el.parentElement || document;
                    }
                } else {
                    const sidebar = document.querySelector('.doc-sidebar, .toc, .nav-tree, #toc, [role="tree"]');
                    if (sidebar) root = sidebar;
                }

                let items = root.querySelectorAll('[role="treeitem"] a');
                
                if (items.length === 0) {
                    items = root.querySelectorAll('.toc-tree a, .nav-tree a, ul.toc a');
                }
                
                if (items.length === 0 && root !== document) {
                    items = root.querySelectorAll('li a');
                }

                items.forEach(a => {
                    if (a.href && !a.href.startsWith('javascript:')) {
                        links.push({
                            text: a.innerText.trim(),
                            href: a.href
                        });
                    }
                });
                return links;
            }
        """, selector_hint)
        
        unique_links = []
        seen_hrefs = set()
        
        for link in links:
            href = link['href']
            text = link['text']
            
            if not href.startswith('http'): continue
            
            if "support.sw.siemens.com" in href:
                if "/doc/" not in href and "/documentation/" not in href:
                    continue
            
            if "login.siemens.com" in href: continue
            if "siemens.com/global" in href: continue
            
            if text in ["Support Center", "支持中心", "Documentation", "文档", "Home", "首页"]:
                continue

            if href not in seen_hrefs:
                unique_links.append(link)
                seen_hrefs.add(href)
        
        print(f"✅ 收集到 {len(unique_links)} 个目录节点链接。")
        return unique_links
    except Exception as e:
        print(f"❌ 收集导航链接失败: {e}")
        return []

def _sanitize_filename(s):
    s = re.sub(r'[\\/*?:"<>|]', "", s)
    s = s.strip().replace(" ", "_")
    return s[:100] 

def process_page_content(page, frame):
    """
    处理页面内容：
    1. 处理懒加载图片 (data-src -> src)
    2. 将图片和 CSS 转换为内联 Base64/文本
    3. 返回处理后的 HTML 片段（只包含主要内容区域）
    """
    
    # 1. 预处理：处理懒加载，移除 srcset
    frame.evaluate("""
        () => {
            document.querySelectorAll('img').forEach(img => {
                // 优先使用 data-src 或 data-original
                if (img.dataset.src) img.src = img.dataset.src;
                else if (img.dataset.original) img.src = img.dataset.original;
                
                img.removeAttribute('srcset'); // 移除 srcset 防止干扰
            });
        }
    """)
    
    # 2. 获取所有图片 URL 和 CSS URL
    resource_urls = frame.evaluate("""
        () => {
            const imgs = Array.from(document.querySelectorAll('img')).map(i => ({type: 'img', url: i.src}));
            const css = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(l => ({type: 'css', url: l.href}));
            return [...imgs, ...css];
        }
    """)
    
    # 3. 在 Python 端下载并编码 (利用 Playwright 的 request context 共享 cookie)
    # 这样可以避免浏览器内的 CORS 问题
    resource_map = {}
    for res in resource_urls:
        url = res['url']
        if not url or url.startswith('data:') or url in resource_map:
            continue
            
        try:
            # 使用 page.request (APIRequestContext) 下载
            response = page.request.get(url, timeout=10000)
            if response.status == 200:
                body = response.body()
                if res['type'] == 'img':
                    # 转 Base64
                    b64 = base64.b64encode(body).decode('utf-8')
                    # 简单的 MIME 类型猜测
                    mime = "image/jpeg"
                    if url.lower().endswith('.png'): mime = "image/png"
                    elif url.lower().endswith('.gif'): mime = "image/gif"
                    elif url.lower().endswith('.svg'): mime = "image/svg+xml"
                    
                    resource_map[url] = f"data:{mime};base64,{b64}"
                elif res['type'] == 'css':
                    # CSS 文本
                    resource_map[url] = body.decode('utf-8', errors='ignore')
        except Exception as e:
            # print(f"    ⚠️ 下载资源失败: {url} - {e}")
            pass

    # 4. 将编码后的资源注入回页面
    # 我们传递一个大对象给 evaluate，让它去替换
    # 同时，获取图片的计算后尺寸，并作为内联样式添加
    frame.evaluate("""
        (resourceMap) => {
            // 替换图片
            document.querySelectorAll('img').forEach(img => {
                if (resourceMap[img.src]) {
                    img.src = resourceMap[img.src];
                    // 获取计算后的尺寸并作为内联样式添加
                    const computedStyle = window.getComputedStyle(img);
                    img.style.width = computedStyle.width;
                    img.style.height = computedStyle.height;
                }
            });
            
            // 替换 CSS
            document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
                if (resourceMap[link.href]) {
                    const style = document.createElement('style');
                    style.textContent = resourceMap[link.href];
                    link.parentNode.replaceChild(style, link);
                }
            });
        }
    """, resource_map)
    
    # 5. 提取主要内容区域
    # 尝试定位 .main.content-container，如果找不到则取 body
    try:
        # 优先获取特定容器
        content_html = frame.locator('.main.content-container').first.evaluate("el => el.outerHTML")
    except:
        try:
            # 备选容器
            content_html = frame.locator('article').first.evaluate("el => el.outerHTML")
        except:
            # 回退到 body innerHTML
            content_html = frame.evaluate("document.body.innerHTML")
            
    return content_html


def harvest_and_aggregate(context, nav_links, base_url, resume=True):
    """遍历导航链接，抓取每个页面的内容，并最后聚合成一个文件。"""
    pages_dir = os.path.join(os.path.dirname(__file__), PAGES_DIR)
    
    # 如果是重新下载，清空目录
    if not resume:
        if os.path.exists(pages_dir):
            shutil.rmtree(pages_dir)
        print("🗑️ 已清空旧数据，开始重新下载...")
        
    os.makedirs(pages_dir, exist_ok=True)
    
    print(f"📥 开始抓取 {len(nav_links)} 个页面...")
    
    for i, link in enumerate(nav_links):
        href = link.get('href')
        text = link.get('text')
        if not href:
            continue

        filename = f"{i:03d}_{_sanitize_filename(text)}.html"
        filepath = os.path.join(pages_dir, filename)

        if resume and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"  ({i+1}/{len(nav_links)}) 跳过已存在: {text}")
            continue

        page = context.new_page()
        try:
            print(f"  ({i+1}/{len(nav_links)}) 正在抓取: {text} ({href})")
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            
            accept_cookies(page)
            
            try:
                content_frame = get_content_frame(page, timeout=15000)
                accept_cookies(content_frame)
                
                content_frame.wait_for_selector('body', timeout=30000)
                try:
                    content_frame.wait_for_selector('.content-container, .main, article', timeout=5000)
                except:
                    pass
                
                # --- 处理并提取内容 ---
                html_content = process_page_content(page, content_frame)
                
            except Exception as e:
                print(f"    ⚠️ 无法获取 content frame，尝试直接抓取主页面: {e}")
                html_content = process_page_content(page, page)

            # 包装一下，确保是合法的 HTML 片段
            if not html_content.strip().startswith('<div') and not html_content.strip().startswith('<article'):
                 html_content = f'<div class="fallback-content">{html_content}</div>'

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"  -> 已保存到 {filepath}")

        except Exception as e:
            print(f"  -> ❌ 抓取失败: {e}")
        finally:
            page.close()

    print("✅ 所有页面抓取完成。")
    
    print("🏗️ 开始聚合成单个 HTML 文件...")
    aggregate_saved_pages(pages_dir, OUT_HTML)


def aggregate_saved_pages(pages_dir, out_file):
    """将保存的 HTML 页面聚合成一个文件。"""
    files = sorted([f for f in os.listdir(pages_dir) if f.lower().endswith('.html')])
    if not files:
        print("⚠️ 聚合失败: 未找到任何已保存的 HTML 文件。")
        return

    bodies = []
    
    # 收集样式：这次我们从所有文件中提取内联的 <style>，因为每个文件可能内联了不同的 CSS
    # 但为了避免重复，我们做一个简单的去重
    all_styles = set()

    for filename in files:
        filepath = os.path.join(pages_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 提取 style
            styles = re.findall(r'<style[^>]*>([\s\S]*?)<\/style>', content, re.IGNORECASE)
            for s in styles:
                all_styles.add(s.strip())
            
            # 移除 style, script, link, meta, title
            # 因为我们已经提取了 style，剩下的就是纯内容
            fragment = re.sub(r'<style\b[^>]*>[\s\S]*?<\/style>', '', content, flags=re.IGNORECASE)
            fragment = re.sub(r'<script\b[^>]*>[\s\S]*?<\/script>', '', fragment, flags=re.IGNORECASE)
            fragment = re.sub(r'<link\b[^>]*>', '', fragment, flags=re.IGNORECASE)
            fragment = re.sub(r'<meta\b[^>]*>', '', fragment, flags=re.IGNORECASE)
            fragment = re.sub(r'<title\b[^>]*>[\s\S]*?<\/title>', '', fragment, flags=re.IGNORECASE)
            fragment = re.sub(r'<div id="MathJax_Message"[^>]*>.*?<\/div>', '', fragment, flags=re.IGNORECASE)
            
            bodies.append(f'<!-- START: {filename} -->\n<div class="page-section" id="{filename}">\n{fragment}\n</div>\n<!-- END: {filename} -->')
        except Exception as e:
            print(f"⚠️ 处理文件 {filename} 失败: {e}")

    style_block = '\n'.join([f'<style>{s}</style>' for s in all_styles])
    
    final_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Aggregated Siemens Docs</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        /* 基础重置，但尽量不干扰原网页样式 */
        body {{ 
            font-family: "Segoe UI", Arial, sans-serif; 
            margin: 0; 
            padding: 20px;
            background-color: #f9f9f9;
        }}
        
        /* 页面章节分隔，不限制宽度，让内容自然流式排布 */
        .page-section {{ 
            margin-bottom: 40px; 
            border-bottom: 1px solid #ccc; 
            padding-bottom: 40px; 
            background-color: #fff;
            /* 移除 max-width 和 box-shadow，回归朴素 */
        }}
        
        /* 仅防止图片溢出视口，不强制居中或改变尺寸 */
        img {{ 
            max-width: 100%; 
            height: auto; 
        }}
        
        /* 简单的链接样式 */
        a {{ color: #005f87; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
    {style_block}
</head>
<body>
    <h1 style="text-align: center; margin-bottom: 40px;">Aggregated Siemens Documentation</h1>
    {''.join(bodies)}
</body>
</html>
    """
    
    try:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f"✅ 聚合完成！已将 {len(bodies)} 个页面合并到 {out_file}")
    except Exception as e:
        print(f"❌ 写入聚合文件失败: {e}")


def main():
    # --- 交互提示 ---
    print("="*50)
    print("西门子文档抓取工具")
    print("="*50)
    
    choice = input("请选择操作模式: [c]继续下载(默认) / [r]重新下载: ").strip().lower()

    resume = True
    if choice == 'r':
        resume = False
        print("⚠️  将清空旧数据并重新开始...")
    else:
        print("🔄 将跳过已存在的文件...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        try:
            print(f"🌐 正在导航到: {URL}")
            page.goto(URL, wait_until="networkidle", timeout=90000)

            accept_cookies(page)

            nav_frame, selector_hint = find_nav_frame(page)
            
            if not nav_frame:
                print("❌ 无法找到包含目录树的 frame。")
                nav_frame = page

            expand_all_tree_nodes(nav_frame, selector_hint)
            
            nav_links = collect_nav_links(nav_frame, selector_hint)

            if not nav_links:
                print("❌ 未能收集到任何导航链接，无法继续。脚本将退出。")
                return

            harvest_and_aggregate(context, nav_links, URL, resume=resume)

            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"status": "success", "url": URL, "pages_found": len(nav_links)}, f, indent=2)

        except Exception as e:
            print(f"❌ 主流程发生严重错误: {e}")
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"status": "error", "message": str(e)}, f, indent=2)
        finally:
            print("✅ 脚本执行完毕。")
            browser.close()

if __name__ == "__main__":
    main()
