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
NAV_FILE = "siemens_nav_structure.json"

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

def collect_nav_tree(frame, selector_hint=None):
    """从目录树中收集导航树结构。"""
    print("🔗 正在收集导航树结构...")
    try:
        tree_data = frame.evaluate("""
            (selector_hint) => {
                function getChildren(element) {
                    const nodes = [];
                    // 查找直接的 li 子元素
                    let lis = [];
                    
                    // 尝试找到 ul/ol 容器
                    let container = element.querySelector(':scope > ul, :scope > ol, :scope > div > ul');
                    if (!container && (element.tagName === 'UL' || element.tagName === 'OL')) {
                        container = element;
                    }
                    
                    if (container) {
                        lis = Array.from(container.children).filter(c => c.tagName === 'LI');
                    } else {
                        // 尝试 role="treeitem"
                        lis = Array.from(element.querySelectorAll(':scope > [role="treeitem"]'));
                    }

                    for (const li of lis) {
                        // 提取链接和文本
                        const a = li.querySelector(':scope > a') || li.querySelector('a'); // 优先找直接子元素
                        let text = "";
                        let href = "";
                        
                        if (a) {
                            text = a.innerText.trim();
                            href = a.href;
                        } else {
                            // 尝试获取直接文本
                            const clone = li.cloneNode(true);
                            clone.querySelectorAll('ul, ol').forEach(e => e.remove());
                            text = clone.innerText.trim();
                        }
                        
                        // 递归查找子节点
                        const children = getChildren(li);
                        
                        if (text || children.length > 0) {
                            nodes.push({
                                text: text,
                                href: href,
                                children: children
                            });
                        }
                    }
                    return nodes;
                }

                // 定位根容器
                let root = null;
                if (selector_hint) {
                    const el = document.querySelector(selector_hint);
                    if (el) {
                        root = el.closest('.doc-sidebar') || el.closest('.toc') || el.closest('[role="tree"]');
                    }
                }
                if (!root) {
                    root = document.querySelector('.doc-sidebar, .toc, .nav-tree, #toc, [role="tree"]');
                }
                
                // 如果还是没找到，尝试找页面上最大的 ul
                if (!root) {
                    const uls = document.querySelectorAll('ul');
                    let maxLi = 0;
                    uls.forEach(ul => {
                        const count = ul.querySelectorAll('li').length;
                        if (count > maxLi) {
                            maxLi = count;
                            root = ul;
                        }
                    });
                }

                if (!root) return [];
                
                return getChildren(root);
            }
        """, selector_hint)
        
        print(f"✅ 收集到树状结构，根节点数: {len(tree_data)}")
        return tree_data
    except Exception as e:
        print(f"❌ 收集导航树失败: {e}")
        return []

def process_tree_and_flatten(nodes, parent_id=""):
    """处理树状结构，分配ID，并返回扁平化的下载列表。"""
    flat_list = []
    processed_nodes = []
    
    for i, node in enumerate(nodes):
        current_id = f"{parent_id}_{i}" if parent_id else f"node_{i}"
        node['id'] = current_id
        
        # 处理 href
        href = node.get('href')
        valid_link = False
        if href and href.startswith('http') and "javascript:" not in href:
             if "login.siemens.com" not in href and "siemens.com/global" not in href:
                 valid_link = True
        
        if valid_link:
            flat_list.append({
                'id': current_id,
                'text': node['text'],
                'href': href
            })
        else:
            node['href'] = "" # 清空无效链接
            
        # 递归
        if node.get('children'):
            children_flat, children_nodes = process_tree_and_flatten(node['children'], current_id)
            flat_list.extend(children_flat)
            node['children'] = children_nodes
            node['hasChildren'] = True
        else:
            node['hasChildren'] = False
            
        processed_nodes.append(node)
        
    return flat_list, processed_nodes

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
    
    # 2. 获取所有图片 URL、CSS URL 和内联样式
    resource_urls = frame.evaluate("""
        () => {
            const resources = [];
            
            // 收集图片
            document.querySelectorAll('img').forEach(img => {
                if (img.src && !img.src.startsWith('data:')) {
                    resources.push({type: 'img', url: img.src});
                }
            });
            
            // 收集外部CSS
            document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
                if (link.href) {
                    resources.push({type: 'external-css', url: link.href});
                }
            });
            
            // 收集内联样式
            document.querySelectorAll('style').forEach(style => {
                if (style.innerHTML.trim()) {
                    resources.push({type: 'inline-style', content: style.innerHTML.trim()});
                }
            });
            
            return resources;
        }
    """)
    
    # 3. 在 Python 端下载并编码 (利用 Playwright 的 request context 共享 cookie)
    resource_map = {}
    inline_styles = []
    
    base_url = frame.url if hasattr(frame, 'url') else page.url
    
    for res in resource_urls:
        if res['type'] == 'inline-style':
            inline_styles.append(res['content'])
        else:
            url = res['url']
            if not url or url.startswith('data:') or url in resource_map:
                continue
            
            if url.startswith('../') or url.startswith('./'):
                from urllib.parse import urljoin
                full_url = urljoin(base_url, url)
            else:
                full_url = url
                
            try:
                response = page.request.get(full_url, timeout=10000)
                if response.status == 200:
                    body = response.body()
                    if res['type'] == 'img':
                        b64 = base64.b64encode(body).decode('utf-8')
                        mime = "image/jpeg"
                        if full_url.lower().endswith('.png'): mime = "image/png"
                        elif full_url.lower().endswith('.gif'): mime = "image/gif"
                        elif full_url.lower().endswith('.svg'): mime = "image/svg+xml"
                        resource_map[url] = f"data:{mime};base64,{b64}"
                    elif res['type'] == 'external-css':
                        resource_map[url] = body.decode('utf-8', errors='ignore')
            except Exception as e:
                if res['type'] == 'img':
                    resource_map[url] = url 
    
    # 4. 将样式和资源注入回页面
    try:
        frame.evaluate("""(data) => {
            const resourceMap = data.resourceMap;
            const inlineStyles = data.inlineStyles;
            
            document.querySelectorAll('style, link[rel="stylesheet"]').forEach(el => el.remove());
            
            inlineStyles.forEach((content) => {
                const style = document.createElement('style');
                style.textContent = content;
                document.head.appendChild(style);
            });
            
            document.querySelectorAll('img').forEach(img => {
                if (resourceMap[img.src]) {
                    img.src = resourceMap[img.src];
                    const computedStyle = window.getComputedStyle(img);
                    img.style.width = computedStyle.width;
                    img.style.height = computedStyle.height;
                }
            });
            
            document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
                if (resourceMap[link.href]) {
                    const style = document.createElement('style');
                    style.textContent = resourceMap[link.href];
                    link.parentNode.replaceChild(style, link);
                }
            });
        }""", {"resourceMap": resource_map, "inlineStyles": inline_styles})
    except Exception as e:
        print(f"    ⚠️ 注入样式时出错: {e}")

    # 5. 提取主要内容区域
    try:
        content_html = frame.locator('.main.content-container').first.evaluate("el => el.outerHTML")
    except:
        try:
            content_html = frame.locator('article').first.evaluate("el => el.outerHTML")
        except:
            content_html = frame.evaluate("document.body.innerHTML")
    
    # 6. 保存样式元数据
    style_metadata = {
        'inline_styles': inline_styles,
        'external_css_urls': [res['url'] for res in resource_urls if res['type'] == 'external-css'],
        'resource_map_keys': list(resource_map.keys()),
        'base64_images': {k: v for k, v in resource_map.items() if k.startswith('data:image') or (v.startswith('data:image') if isinstance(v, str) else False)}
    }
    
    content_html = f'<!-- STYLE_METADATA: {json.dumps(style_metadata)} -->\n{content_html}'
    
    return content_html


def harvest_and_aggregate(context, download_list, resume=True):
    """遍历下载列表，抓取内容。"""
    pages_dir = os.path.join(os.path.dirname(__file__), PAGES_DIR)
    
    if not resume:
        if os.path.exists(pages_dir):
            shutil.rmtree(pages_dir)
        print("🗑️ 已清空旧数据，开始重新下载...")
        
    os.makedirs(pages_dir, exist_ok=True)
    
    print(f"📥 开始抓取 {len(download_list)} 个页面...")
    
    for i, item in enumerate(download_list):
        href = item['href']
        text = item['text']
        node_id = item['id']
        
        # 使用 ID 作为文件名，确保唯一且可映射
        filename = f"{node_id}.html"
        filepath = os.path.join(pages_dir, filename)

        if resume and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"  ({i+1}/{len(download_list)}) 跳过已存在: {text}")
            continue

        page = context.new_page()
        try:
            print(f"  ({i+1}/{len(download_list)}) 正在抓取: {text} ({href})")
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
                
                html_content = process_page_content(page, content_frame)
                
            except Exception as e:
                print(f"    ⚠️ 无法获取 content frame，尝试直接抓取主页面: {e}")
                html_content = process_page_content(page, page)

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

def render_sidebar_html(nodes):
    """递归生成侧边栏 HTML"""
    if not nodes:
        return ""
    
    html = '<ul class="nested">'
    for node in nodes:
        node_id = node.get('id', '')
        text = node.get('text', 'Untitled')
        has_children = node.get('hasChildren', False)
        href = node.get('href', '')
        
        html += '<li>'
        
        # 展开/折叠图标
        if has_children:
            html += '<span class="caret" onclick="toggleNode(this)"></span>'
        else:
            html += '<span class="no-caret"></span>'
            
        # 链接
        if href:
            # 指向对应的内容锚点
            html += f'<a href="#content_{node_id}" onclick="handleManualClick(this)">{text}</a>'
        else:
            html += f'<span class="nav-text">{text}</span>'
            
        # 递归子节点
        if has_children:
            # 默认展开第一层? 或者全部折叠。这里默认折叠，通过 JS 控制
            html += render_sidebar_html(node.get('children', []))
            
        html += '</li>'
        
    html += '</ul>'
    return html

def aggregate_saved_pages(pages_dir, out_file):
    """将保存的 HTML 页面聚合成一个文件，带侧边栏。"""
    
    # 读取导航结构
    nav_structure = []
    if os.path.exists(NAV_FILE):
        with open(NAV_FILE, 'r', encoding='utf-8') as f:
            nav_structure = json.load(f)
    else:
        print("⚠️ 未找到导航结构文件，将无法生成侧边栏。")

    # 收集内容和样式
    bodies = []
    all_styles = set()
    
    # 遍历下载目录中的文件
    # 注意：这里我们不直接遍历目录，而是应该根据 nav_structure 的顺序来或者直接读取所有文件
    # 为了简单，我们读取所有文件，并存入字典，然后在生成 HTML 时按需取用？
    # 不，更好的方式是：内容区域只包含已下载的页面。
    
    files_content = {}
    if os.path.exists(pages_dir):
        for f in os.listdir(pages_dir):
            if f.endswith('.html'):
                path = os.path.join(pages_dir, f)
                try:
                    with open(path, 'r', encoding='utf-8') as file:
                        files_content[f] = file.read()
                except:
                    pass

    # 提取样式并准备内容块
    # 我们需要遍历 nav_structure 来确定顺序吗？其实内容区域的顺序不重要，重要的是 ID 对应
    # 但为了线性阅读，最好还是按顺序
    
    def process_nodes_for_content(nodes):
        content_blocks = []
        for node in nodes:
            node_id = node.get('id')
            filename = f"{node_id}.html"
            
            if filename in files_content:
                content = files_content[filename]
                
                # 提取样式
                style_match = re.search(r'<!-- STYLE_METADATA: (.*?) -->', content)
                if style_match:
                    try:
                        metadata = json.loads(style_match.group(1))
                        for style in metadata.get('inline_styles', []):
                            all_styles.add(style.strip())
                    except: pass
                
                # 清理内容
                fragment = re.sub(r'<!-- STYLE_METADATA: .*? -->', '', content, flags=re.DOTALL)
                fragment = re.sub(r'<style\b[^>]*>[\s\S]*?<\/style>', '', fragment, flags=re.IGNORECASE)
                fragment = re.sub(r'<script\b[^>]*>[\s\S]*?<\/script>', '', fragment, flags=re.IGNORECASE)
                fragment = re.sub(r'<link\b[^>]*>', '', fragment, flags=re.IGNORECASE)
                fragment = re.sub(r'<meta\b[^>]*>', '', fragment, flags=re.IGNORECASE)
                fragment = re.sub(r'<title\b[^>]*>[\s\S]*?<\/title>', '', fragment, flags=re.IGNORECASE)
                
                # 包裹在带 ID 的 div 中
                block = f'<div class="page-section" id="content_{node_id}">\n{fragment}\n</div>'
                content_blocks.append(block)
            
            if node.get('children'):
                content_blocks.extend(process_nodes_for_content(node['children']))
        return content_blocks

    bodies = process_nodes_for_content(nav_structure)
    
    # 如果 nav_structure 为空（例如旧模式），则回退到简单的文件遍历
    if not bodies and files_content:
        print("⚠️ 使用回退模式聚合页面...")
        for filename in sorted(files_content.keys()):
            content = files_content[filename]
            # ... (同样的清理逻辑) ...
            fragment = re.sub(r'<!-- STYLE_METADATA: .*? -->', '', content, flags=re.DOTALL)
            fragment = re.sub(r'<style\b[^>]*>[\s\S]*?<\/style>', '', fragment, flags=re.IGNORECASE)
            # ...
            bodies.append(f'<div class="page-section" id="{filename}">\n{fragment}\n</div>')

    style_block = '\n'.join([f'<style>{s}</style>' for s in all_styles])
    
    # 生成侧边栏 HTML
    sidebar_html = render_sidebar_html(nav_structure)
    # 修正：最外层 ul 应该是 active 的
    sidebar_html = sidebar_html.replace('<ul class="nested">', '<ul class="nested active">', 1)

    # CSS 和 JS (借鉴 nx_fbm_scraper9.js)
    ui_css = """
    <style>
        body { display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: "Segoe UI", Arial, sans-serif; background-color: #f9f9f9; }
        
        /* 侧边栏 */
        aside { 
            width: 300px; 
            min-width: 200px; 
            max-width: 600px; 
            overflow-y: auto; 
            padding: 15px 10px; 
            background: #f0f0f0; 
            flex-shrink: 0; 
            border-right: 1px solid #ccc; 
            display: flex;
            flex-direction: column;
        }
        
        /* 调整器 */
        #resizer { width: 5px; cursor: col-resize; background: #ddd; height: 100%; flex-shrink: 0; transition: background 0.2s; }
        #resizer:hover { background: #aaa; }
        
        /* 主内容区 */
        main { flex: 1; overflow-y: auto; background: #fff; scroll-behavior: smooth; padding: 0; position: relative; }
        .content-wrapper { max-width: 1000px; margin: 0 auto; padding: 40px; background: #fff; min-height: 100%; }
        
        /* 目录树样式 */
        aside ul { list-style: none; margin: 0; padding: 0; }
        aside li { margin: 2px 0; padding: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        ul.nested { display: none; padding-left: 16px; }
        ul.active { display: block; }
        
        .caret { cursor: pointer; display: inline-block; width: 18px; text-align: center; user-select: none; color: #666; }
        .caret::before { content: "▶"; font-size: 10px; display: inline-block; transition: transform 0.2s; }
        .caret-down::before { transform: rotate(90deg); }
        .no-caret { display: inline-block; width: 18px; }
        
        aside a { text-decoration: none; color: #333; font-size: 14px; padding: 4px 6px; display: inline-block; border-radius: 3px; transition: background 0.1s; }
        aside a:hover { background: #e0e0e0; color: #000; }
        .selected-link { background: #005f87 !important; color: #fff !important; }
        .nav-text { color: #666; font-size: 14px; padding: 4px 6px; }

        /* 页面章节 */
        .page-section { margin-bottom: 50px; padding-bottom: 30px; border-bottom: 1px solid #eee; }
        
        /* 表格和图片样式修复 */
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
        th { background-color: #f5f5f5; font-weight: bold; }
        img { max-width: 100%; height: auto; }
    </style>
    """

    ui_js = """
    <script>
        function toggleNode(span) { 
            var ul = span.parentElement.querySelector(".nested"); 
            if (ul) { 
                ul.classList.toggle("active"); 
                span.classList.toggle("caret-down"); 
            } 
        }
        
        function handleManualClick(a) {
            document.querySelectorAll(".selected-link").forEach(l => l.classList.remove("selected-link"));
            a.classList.add("selected-link");
        }
        
        // 拖拽调整宽度
        window.onload = function() {
            const resizer = document.getElementById('resizer'); 
            const sidebar = document.querySelector('aside'); 
            let isResizing = false;
            
            resizer.addEventListener('mousedown', (e) => { 
                isResizing = true; 
                document.body.style.cursor = 'col-resize';
                e.preventDefault();
            });
            
            document.addEventListener('mousemove', (e) => { 
                if (isResizing) { 
                    let newWidth = e.clientX;
                    if (newWidth > 150 && newWidth < 800) {
                        sidebar.style.width = newWidth + 'px'; 
                    }
                } 
            });
            
            document.addEventListener('mouseup', () => { 
                isResizing = false; 
                document.body.style.cursor = 'default';
            });
        }
    </script>
    """

    final_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Aggregated Siemens Docs</title>
    {ui_css}
    {style_block}
</head>
<body>
    <aside>
        <h3 style="padding-left: 10px; margin-top: 0;">Contents</h3>
        {sidebar_html}
    </aside>
    <div id="resizer"></div>
    <main>
        <div class="content-wrapper">
            <h1 style="text-align: center; margin-bottom: 40px; border-bottom: 2px solid #005f87; padding-bottom: 20px;">Siemens Documentation</h1>
            {''.join(bodies)}
        </div>
    </main>
    {ui_js}
</body>
</html>
    """
    
    try:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f"✅ 聚合完成！已生成带侧边栏的文档: {out_file}")
    except Exception as e:
        print(f"❌ 写入聚合文件失败: {e}")


def main():
    # --- 交互提示 ---
    print("="*50)
    print("西门子文档抓取工具 (Enhanced)")
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
            
            # 获取树状结构
            tree_data = collect_nav_tree(nav_frame, selector_hint)
            
            if not tree_data:
                print("❌ 未能收集到导航树，尝试回退到旧方法...")
                # 这里可以加回退逻辑，但为了简洁先报错
                return

            # 处理树结构，生成下载列表
            download_list, processed_tree = process_tree_and_flatten(tree_data)
            
            # 保存导航结构供聚合使用
            with open(NAV_FILE, "w", encoding="utf-8") as f:
                json.dump(processed_tree, f, indent=2)
            print(f"✅ 导航结构已保存到 {NAV_FILE}")

            if not download_list:
                print("❌ 没有发现有效的下载链接。")
                return

            harvest_and_aggregate(context, download_list, resume=resume)

            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"status": "success", "url": URL, "pages_found": len(download_list)}, f, indent=2)

        except Exception as e:
            print(f"❌ 主流程发生严重错误: {e}")
            import traceback
            traceback.print_exc()
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"status": "error", "message": str(e)}, f, indent=2)
        finally:
            print("✅ 脚本执行完毕。")
            browser.close()

if __name__ == "__main__":
    main()
