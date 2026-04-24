#!/usr/bin/env python3
"""
NX文档聚合器 - 终极完全体 (v11.28 修复版：兼容 JS 版生成的 JSON 结构)
核心优化:

1. 完美应用用户自定义的全局配置参数 (NX12/2506 通用)。
2. 彻底修复侧边栏双重斑马线，加入4px极限缩进、#888单竖线、西门子深蓝字体。
3. 全词库无死角底部垃圾清理 + OOM 防爆流式写入硬盘。
4. [新增] 页面池化 (Page Pooling)：不再频繁开闭标签页，极致压榨 CPU 性能。
5. [新增] SQLite 数据库缓存：消灭海量碎文件，单库秒级读写。
6. [新增] 默认展开首层目录，提升阅读体验。
7. [新增] 优化选中目录项的背景色，提高文字可读性。
8. [新增] 将正文主标题颜色设为主题蓝，与侧边栏标题呼应。
9. [修复] 无论是否有内容，强制生成 HTML 文件，避免静默失败。
10. [修复] 彻底移除所有针对表格的强制样式，完全依赖页面自带 CSS。
11. [修复] 恢复内容容器回退机制，解决 DOM_CONTAINER_NOT_FOUND 错误。
12. [修复] 增强网络拦截，屏蔽字体和 websocket。
13. [修复] 重构并优化续传逻辑，确保失败的页面能够被重新下载。
14. [优化] 固定侧边栏标题，使其不随目录滚动。
15. [修复] 回归 ultra_simple4.py 的抓取逻辑（优先查找 div.doc-content），并保留强力清洗功能。
16. [优化] 明确设置正文主标题的字体大小。
17. [新增] 自动检测并清洗数据库中的脏数据（包含侧边栏的页面），强制重抓。
18. [新增] 启动时自动压缩数据库 (VACUUM) 并打印数据统计信息。19. [重构] 数据库结构升级：CSS 独立去重存储，数据库体积缩减 90%！
20. [新增] 每次改动追加 REV 标记，便于回退定位。
21. [修复] 修正纯目录外壳判定逻辑，避免访问 javascript:void(0) 链接。
22. [优化] 代码结构清理，移除重复定义，统一 LF 换行符。
23. [增强] CSS URL 绝对化处理，与 JS 版保持一致，修复背景图路径问题。
24. [修复] 强制使用 utf-8 读写 progress.json，解决 Windows 下的编码错误。
25. [修复] 兼容 JS 版生成的 nav_structure.json (读取 url 字段作为 href)。
"""

# 变更标记（每次改动请追加一条，勿修改历史）

# [REV 2026-02-18 #01] 抓取：优先使用 frameDoc 的 div.doc-content 克隆，清理 header/navbar/breadcrumb；表格：保留 locator 的 col/colgroup 宽度；CSS：增加去重命中调试输出。
# [REV 2026-02-18 #02] 表格：避免离线页面表格被压缩换行，仅对 locator 表格启用横向滚动兜底（不改原站点列宽定义）。
# [REV 2026-02-18 #03] 调试：抓取时统计正文中 table 类型签名，输出 table_report.json/txt（用于定位哪些表格需要额外 CSS 兜底）。
# [REV 2026-02-19 #04] 修复：修正纯目录外壳判定逻辑，正确跳过 javascript:void(0) 链接。
# [REV 2026-02-19 #05] 清理：移除重复函数定义，统一行尾为 LF。
# [REV 2026-02-19 #06] 修复：修正 update_table_report 函数中的缩进错误和垃圾字符。
# [REV 2026-02-19 #07] 样式：根据 table_report 优化表格 CSS，覆盖 no-class, navigator, locator 三种类型。
# [REV 2026-02-19 #08] 增强：添加 cssTextToAbsoluteUrls 函数，修复 CSS 中的相对路径。
# [REV 2026-02-19 #09] 修复：load_progress 和 save_progress 强制使用 utf-8 编码。
# [REV 2026-02-19 #10] 修复：flatten 函数优先读取 'url' 字段，兼容 JS 版生成的 JSON。
# [REV 2026-04-24 #11] 🚀 重大升级：完整复刻 JS 版功能 - 视频提取引擎 + 表格分类打标 + 小图标保护
#   - 新增：5层视频链接提取策略（属性/source/父节点/script/网络拦截）
#   - 新增：MutationObserver 等待动态渲染
#   - 新增：6种表格精准分类（toxic-wide/nested-parent/nested-child/multi-image/code-comparison/media-code）
#   - 新增：小图标保护机制（≤80px 添加 inline-small-icon 类）
#   - 新增：代码块清理（移除 Copy 按钮和工具栏）
#   - 增强：CSS URL 绝对化处理（与 JS 版完全一致）
#   - 增强：播放器修复（剥离 Plyr，启用原生 controls）

import asyncio
from playwright.async_api import async_playwright
import json
import os
import time
import signal
import sys
import random
import hashlib
import sqlite3
import re

# ==========================================
# 🔧 Windows Console Encoding Fix
# ==========================================
# Set stdout encoding to UTF-8 for proper Chinese character display
if sys.platform == 'win32':
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except:
        pass

# ==========================================
# 📋 批量任务配置区 (从 download_list.txt 读取)
# ==========================================

def load_subjects_from_file(filename="download_list.txt"):
    """
    从 download_list.txt 读取主题配置
    格式：每两行为一组（标题 + URL）
    返回：[{name, url, title}, ...]
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        
        subjects = []
        # 每两行为一组：标题 + URL
        for i in range(0, len(lines), 2):
            if i + 1 < len(lines):
                title = lines[i]
                url = lines[i + 1]
                
                # 生成安全的名称（与 JS 版一致）
                name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', title)
                
                if url.startswith('http') and len(title) > 0:
                    subjects.append({
                        'name': name,
                        'url': url,
                        'title': title
                    })
        
        return subjects
    except FileNotFoundError:
        print(f"❌ 配置文件 {filename} 不存在，请创建 download_list.txt 文件")
        return []
    except Exception as e:
        print(f"❌ 读取配置文件出错: {e}")
        return []

SUBJECTS = load_subjects_from_file()

# ==========================================
# ⚙️ 全局配置区 (Global Configuration)
# ==========================================
# 动态变量（将在 start_job 中设置）
START_URL = ""
FINAL_OUTPUT_FILE = ""
CACHE_DB_FILE = ""
SIDEBAR_TITLE = ""
NAV_JSON_FILE = ""
OUTPUT_DIR = "output"  # 专门的输出目录

# 固定常量
MAX_CONCURRENCY = 9  # 🚀 并发线程数量 (推荐: 极速设5，防封锁设2)
TABLE_REPORT_JSON = "table_report.json"
TABLE_REPORT_TXT = "table_report.txt"


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

    def get_progress_info(self):
        pct = (self.processed_pages / self.total_pages * 100) if self.total_pages > 0 else 0
        elapsed = time.time() - self.start_time
        rate = self.processed_pages / elapsed if elapsed > 0 else 0
        remaining = self.total_pages - self.processed_pages
        eta = f"{int(remaining / rate // 60)}分{int(remaining / rate % 60)}秒" if rate > 0 else "计算中"
        return f"[{pct:.1f}%] 成功:{self.success_count} 复用:{self.redundant_count} 失败:{self.failed_count} | ETA: {eta}"


def _normalize_table_signature(sig: dict) -> str:
    """
    Build a stable signature key for table type aggregation.
    """
    classes = ",".join(sorted(sig.get("classes", []))) or "-"
    return (
        f"classes={classes}"
        f"|locator={int(bool(sig.get('isLocator')))}"
        f"|colgroup={int(bool(sig.get('hasColgroup')))}"
        f"|thead={int(bool(sig.get('hasThead')))}"
        f"|tbody={int(bool(sig.get('hasTbody')))}"
        f"|rows={sig.get('rows', '?')}"
        f"|cols={sig.get('cols', '?')}"
    )


def update_table_report(table_report: dict, title: str, url: str, table_sigs: list):
    """
    table_report structure:
      {
        "total_pages_with_tables": int,
        "total_tables": int,
        "by_signature": {
            "<signature>": {"count": int, "examples": [{"title":..., "url":...}]}
        },
        "by_class": {
            "<class>": {"count": int, "examples": [...]}
        }
      }
    """
    if not table_sigs:
        return

    table_report["total_pages_with_tables"] += 1
    table_report["total_tables"] += len(table_sigs)

    by_sig = table_report["by_signature"]
    by_class = table_report["by_class"]

    for sig in table_sigs:
        key = _normalize_table_signature(sig)
        if key not in by_sig:
            by_sig[key] = {"count": 0, "examples": []}
        by_sig[key]["count"] += 1
        if len(by_sig[key]["examples"]) < 5:
            by_sig[key]["examples"].append({"title": title, "url": url})

        classes = sig.get("classes", []) or []
        if not classes:
            classes = ["(no-class)"]
        for cls in classes:
            if cls not in by_class:
                by_class[cls] = {"count": 0, "examples": []}
            by_class[cls]["count"] += 1
            if len(by_class[cls]["examples"]) < 5:
                by_class[cls]["examples"].append({"title": title, "url": url})


def write_table_reports(table_report: dict):
    # JSON
    try:
        with open(TABLE_REPORT_JSON, "w", encoding="utf-8") as f:
            json.dump(table_report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 写入 {TABLE_REPORT_JSON} 失败: {e}")

    # TXT
    try:
        lines = []
        lines.append("NXDOC Table Report")
        lines.append("=" * 60)
        lines.append(f"pages_with_tables: {table_report.get('total_pages_with_tables', 0)}")
        lines.append(f"total_tables:      {table_report.get('total_tables', 0)}")
        lines.append("")

        # top signatures
        lines.append("[Top table signatures]")
        sig_items = sorted(
            table_report.get("by_signature", {}).items(),
            key=lambda kv: kv[1].get("count", 0),
            reverse=True,
        )
        for sig, info in sig_items[:50]:
            lines.append(f"- {info.get('count', 0):>5}  {sig}")
            for ex in info.get("examples", [])[:3]:
                lines.append(f"        · {ex.get('title', '')} | {ex.get('url', '')}")
        lines.append("")

        lines.append("[Top table classes]")
        cls_items = sorted(
            table_report.get("by_class", {}).items(),
            key=lambda kv: kv[1].get("count", 0),
            reverse=True,
        )
        for cls, info in cls_items[:100]:
            lines.append(f"- {info.get('count', 0):>5}  class={cls}")
            for ex in info.get("examples", [])[:3]:
                lines.append(f"        · {ex.get('title', '')} | {ex.get('url', '')}")

        with open(TABLE_REPORT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"⚠️ 写入 {TABLE_REPORT_TXT} 失败: {e}")


def load_progress():
    if os.path.exists('progress.json'):
        with open('progress.json', 'r', encoding='utf-8') as f: return json.load(f)
    return {'completed': [], 'failed': []}


def save_progress(progress):
    with open('progress.json', 'w', encoding='utf-8') as f: json.dump(progress, f)


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
        # 优化点：根UL默认展开
        html = '<ul class="root-list active">\n' if level == 0 else '<ul class="nested">\n'

        for node in nodes:
            text = node.get('text', 'Untitled')
            children = node.get('children', [])
            # 现在的版本：增加了一层兼容兜底！找不到 hasChildren，就去看看 children 数组里面有没有东西
            has_children = node.get('hasChildren', len(children) > 0)

            page_index = idx_counter[0]
            idx_counter[0] += 1

            html += f'    <li class="nav-level-{level}">\n'
            html += '        <div class="nav-item-row">\n'
            if has_children and children:
                # 优化点：首层目录的箭头默认向下
                caret_class = "caret caret-down" if level == 0 else "caret"
                html += f'            <span class="{caret_class}" onclick="toggleNode(this)"></span>\n'
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
                sub_html = build_tree_html(children, level + 1)
                # 优化点：首层目录的子菜单默认展开
                if level == 0:
                    sub_html = sub_html.replace('<ul class="nested">', '<ul class="nested active">', 1)
                html += sub_html
            html += '    </li>\n'
        return html + '</ul>\n'

    return build_tree_html(nav_structure)


async def process_page(i, title, url, has_href, page_pool, stats, progress, mode, lock, db_conn,
                       seen_hashes, seen_css_hashes, global_styles, contents_dict, valid_indices, duplicate_map,
                       table_report):
    if stats.interrupted: return

    # ==================================================================
    # 续传逻辑重构 (v11.8)
    # ==================================================================

    # 修复点：修正纯目录外壳判定逻辑
    # 修复点：修正纯目录外壳判定逻辑
    is_directory_shell = not url or not url.strip() or "javascript:void(0)" in url
    if is_directory_shell:
        print(f"   ℹ️ [{i + 1}] {title} (📁 纯目录外壳，自动跳过)")
        async with lock:
            if title not in progress['completed']:
                progress['completed'].append(title)
            stats.processed_pages += 1
            stats.success_count += 1
            if stats.processed_pages % 10 == 0:
                save_progress(progress)
        return

    # 2. 【核心修复】彻底抛弃 progress.json，直接以 SQLite 数据库为唯一真理进行续传
    cached_content = None
    cached_css = None
    try:
        async with lock:
            # 🚀 改为用 url 精准查询，防止同名不同页的误判
            row = db_conn.execute("SELECT html, css_hash FROM cache WHERE url=?", (url,)).fetchone()
            if row:
                cached_content, css_hash = row
                if css_hash:
                    style_row = db_conn.execute("SELECT content FROM styles WHERE hash=?", (css_hash,)).fetchone()
                    if style_row:
                        cached_css = style_row[0]
    except Exception:
        pass

    if cached_content:
        # 修复点：检查缓存是否脏了 (包含侧边栏)
        if 'doc-sidebar' in cached_content or 'id="doc-sidebar"' in cached_content:
            print(f"   ⚠️ [{i + 1}] {title} (缓存包含侧边栏，视为脏数据，强制重抓)")
            cached_content = None  # 标记为无效，触发重抓
        else:
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
                            else:
                                # 调试：CSS 去重命中（可能存在误杀/过度去重）
                                # print(f"   🧪 [CSS去重命中] {title} -> {css_hash[:10]} (已存在)")
                                pass

                        stats.success_count += 1
                        print(f"[{i + 1}] {title} (✓ 数据库极速恢复)")
                    else:
                        duplicate_map[i] = seen_hashes[content_hash]
                        stats.redundant_count += 1
                        print(f"[{i + 1}] {title} (🔗 缓存映射复用)")
                    stats.processed_pages += 1
            return

    # 如果代码执行到这里，意味着：
    # - 这是一个新页面
    # - 这是一个之前失败的页面 (不在 'completed' 列表里)
    # - 这是一个在 'completed' 列表里但缓存丢失的页面
    # - 这是一个缓存脏了的页面
    # 无论哪种情况，都需要重新抓取。
    print(f"[{i + 1}] 🚀 开始提取: {title}")

    # 🚀 从池中获取一个空闲的页面实例 (代替昂贵的 new_page)
    page = await page_pool.get()

    try:
        retry_count = 0
        success = False
        extracted_data = ""
        extracted_css = ""
        extracted_table_sigs = []

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

// ============================================================================
// ultra_simple8.py 增强版 evaluate 脚本
// 完整复刻 nxdoc_scraper_no_img完美版.js 的核心功能
// 使用方法：替换 ultra_simple8.py 第473-642行的 evaluate 内容
// ============================================================================

async () => {
    // ==========================================
    // 1. 获取文档对象（iframe 兼容）
    // ==========================================
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

    // ==========================================
    // 2. 🚀 滚动触发懒加载（视频/图片）
    // ==========================================
    const scrollTargets = [frameDoc.defaultView, window, container, frameDoc.scrollingElement || frameDoc.body].filter(el => el != null);
    let maxScroll = 5000;
    scrollTargets.forEach(t => { if (t.scrollHeight && t.scrollHeight > maxScroll) maxScroll = t.scrollHeight; });
    for(let pos = 0; pos <= maxScroll; pos += 1000) {
        scrollTargets.forEach(target => {
            try { if (target.scrollTo) target.scrollTo(0, pos); else target.scrollTop = pos; } catch(e) {}
        });
        await new Promise(r => setTimeout(r, 50));
    }

    // ==========================================
    // 3. 🚀 等待视频组件加载（MutationObserver）
    // ==========================================
    window.__interceptedMediaUrls = interceptedMediaUrls;
    const videos = frameDoc.querySelectorAll("disw-video");
    if (videos.length > 0) {
        await Promise.all(
            Array.from(videos).map((v) => {
                return new Promise((resolve) => {
                    if (v.querySelector("video source") || v.querySelector("video[src]")) {
                        return resolve();
                    }
                    const observer = new MutationObserver(() => {
                        if (v.querySelector("video source") || v.querySelector("video[src]")) {
                            observer.disconnect();
                            resolve();
                        }
                    });
                    observer.observe(v, { childList: true, subtree: true });
                    setTimeout(() => {
                        observer.disconnect();
                        resolve();
                    }, 8000);
                });
            })
        );
    }

    // ==========================================
    // 4. 🚀 5层视频链接提取策略
    // ==========================================
    frameDoc.querySelectorAll("video").forEach((v) => {
        let realSrc = null;

        // 层1：检查 video 自身的属性
        Array.from(v.attributes).forEach((attr) => {
            if (attr.value.includes(".mp4") || attr.value.includes(".webm") || attr.value.includes(".m3u8")) {
                realSrc = attr.value;
            }
        });

        // 层2：检查 source 子节点
        if (!realSrc) {
            v.querySelectorAll("source").forEach((s) => {
                let src = s.getAttribute("src") || s.getAttribute("data-src") || s.getAttribute("data-video-src");
                if (src && (src.includes(".mp4") || src.includes(".webm") || src.includes(".m3u8"))) {
                    realSrc = src;
                }
            });
        }

        // 层3：向上遍历父节点查找
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
            let parent = v.parentElement;
            while (parent && parent.tagName !== "BODY") {
                Array.from(parent.attributes).forEach((attr) => {
                    if (attr.value.includes(".mp4") || attr.value.includes(".webm") || attr.value.includes(".m3u8")) {
                        realSrc = attr.value;
                    }
                });
                if (realSrc) break;

                if (parent.tagName.toLowerCase() === "disw-video") {
                    const videoUrl = parent.getAttribute("video-url") || parent.getAttribute("src") || 
                                   parent.getAttribute("data-video-url") || parent.getAttribute("data-src");
                    if (videoUrl) {
                        realSrc = videoUrl;
                        break;
                    }
                    let match = parent.innerHTML.match(/(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i);
                    if (match) {
                        realSrc = match[1];
                        break;
                    }
                }
                parent = parent.parentElement;
            }
        }

        // 层4：全局搜索 script 标签中的视频链接
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
            let scripts = frameDoc.querySelectorAll("script");
            for (let script of scripts) {
                let match = script.textContent.match(/(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i);
                if (match) {
                    realSrc = match[1];
                    break;
                }
            }
        }

        // 层5：从 window.__interceptedMediaUrls 中获取
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
            if (window.__interceptedMediaUrls && window.__interceptedMediaUrls.length > 0) {
                realSrc = window.__interceptedMediaUrls[0];
            }
        }

        if (realSrc) {
            v.setAttribute("data-real-src", realSrc);
        }
    });

    // 额外检查 disw-video
    frameDoc.querySelectorAll("disw-video").forEach((v) => {
        let realSrc = v.getAttribute("video-url") || v.getAttribute("src") || 
                     v.getAttribute("data-video-url") || v.getAttribute("data-src");
        if (!realSrc) {
            Array.from(v.attributes).forEach((attr) => {
                if (attr.value.includes(".mp4") || attr.value.includes(".webm") || attr.value.includes(".m3u8")) {
                    realSrc = attr.value;
                }
            });
        }
        if (!realSrc) {
            let match = v.innerHTML.match(/(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i);
            if (match) realSrc = match[1];
        }
        if (!realSrc && window.__interceptedMediaUrls && window.__interceptedMediaUrls.length > 0) {
            realSrc = window.__interceptedMediaUrls[0];
        }
        if (realSrc) {
            let video = v.querySelector("video");
            if (!video) {
                video = frameDoc.createElement("video");
                video.setAttribute("data-real-src", realSrc);
                v.appendChild(video);
            } else if (!video.getAttribute("data-real-src")) {
                video.setAttribute("data-real-src", realSrc);
            }
        }
    });

    // ==========================================
    // 5. 克隆 DOM 并清理
    // ==========================================
    const clone = (frameDoc.querySelector("div.doc-content") || container).cloneNode(true);

    // 移除不需要的元素
    const unwantedSelectors = [
        'header', '.navbar', '.site-header', '.topbar', '.app-header',
        '.breadcrumb', '.breadcrumbs', '.global-nav', '.nav-header',
        '.doc-sidebar', '#doc-sidebar', '#topic-navigator', 
        '.hidden-md-up', '#feedback-btns', '.gutter',
        '.cookie-banner', '.cookie-consent', '.gdpr-banner',
        '.disw-video-loader-container'
    ];
    unwantedSelectors.forEach(sel => {
        const els = clone.querySelectorAll(sel);
        els.forEach(el => el.remove());
    });

    // 页脚关键词清理
    const footerKeywords = [
        'Learn more', 'How do I', 'Look up more details', 'See also', 'See Also',
        'Related Concepts', 'Related Reference', 'Related Topics', 'Related Tasks', 
        'Related Information', 'Related Links',
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

    // ==========================================
    // 6. 🌟 小图标保护（≤80px）
    // ==========================================
    clone.querySelectorAll("img").forEach((img) => {
        if ((img.clientWidth > 0 && img.clientWidth <= 80) || 
            (img.naturalWidth > 0 && img.naturalWidth <= 80)) {
            img.classList.add("inline-small-icon");
        }
    });

    // ==========================================
    // 7. 处理图片和视频链接（绝对化 + 恢复真实链接）
    // ==========================================
    clone.querySelectorAll("img").forEach((img) => {
        if (img.hasAttribute("src")) {
            const absoluteUrl = new URL(img.getAttribute("src"), baseUrl).href;
            img.src = absoluteUrl;
            img.removeAttribute("loading");
            img.setAttribute("decoding", "async");
        }
    });

    clone.querySelectorAll("a, video, source, track").forEach((el) => {
        // 尝试从 data-real-src 恢复真实的 src
        ["data-real-src", "data-src", "data-video-src"].forEach((attr) => {
            if (el.hasAttribute(attr)) {
                const val = el.getAttribute(attr);
                if (val && !val.startsWith("blob:") && !val.startsWith("data:")) {
                    el.setAttribute("src", val);
                }
            }
        });

        if (el.tagName === "A" && el.hasAttribute("href")) {
            try { el.href = new URL(el.getAttribute("href"), baseUrl).href; } catch (e) {}
        } else if ((el.tagName === "VIDEO" || el.tagName === "SOURCE" || el.tagName === "TRACK") && el.hasAttribute("src")) {
            const src = el.getAttribute("src");
            if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
                try { el.src = new URL(src, baseUrl).href; } catch (e) {}
            }
        }

        // 🚀 视频播放器修复
        if (el.tagName === "VIDEO") {
            ["poster", "data-poster"].forEach((attr) => {
                if (el.hasAttribute(attr)) {
                    const val = el.getAttribute(attr);
                    if (val && !val.startsWith("blob:") && !val.startsWith("data:")) {
                        try {
                            const absUrl = new URL(val, baseUrl).href;
                            el.setAttribute(attr, absUrl);
                            if (attr === "data-poster") el.setAttribute("poster", absUrl);
                        } catch (e) {}
                    }
                }
            });

            // 开启原生控制条，剥离 Plyr
            el.setAttribute("controls", "true");
            el.setAttribute("preload", "metadata");
            el.style.cssText = "width:100%;height:auto;aspect-ratio:16/9;display:block;background:#000;";

            const aspectRatioCtrl = el.closest(".aspectRatioHeightControl");
            const diswVideo = el.closest("disw-video");
            const targetToReplace = aspectRatioCtrl || diswVideo;

            if (targetToReplace && targetToReplace.parentNode) {
                targetToReplace.parentNode.replaceChild(el, targetToReplace);
            } else {
                el.classList.remove("plyr");
                const plyr = el.closest(".plyr");
                if (plyr) {
                    plyr.classList.remove("plyr");
                    plyr.querySelectorAll(".plyr__controls, .plyr__control--overlaid, .plyr__captions, .plyr__poster")
                        .forEach((c) => c.remove());
                }
            }

            // 强制显示被隐藏的播放器容器
            let current = el;
            while (current && current.tagName !== "BODY") {
                if (current.style && current.style.display === "none") {
                    current.style.display = "block";
                }
                current = current.parentNode;
            }
        }
    });

    // ==========================================
    // 8. 清理代码块 Copy 按钮
    // ==========================================
    clone.querySelectorAll(".codeblock-toolbar").forEach((toolbar) => toolbar.remove());
    clone.querySelectorAll("button, a, div, span").forEach((el) => {
        const txt = (el.textContent || "").trim().toLowerCase();
        if (txt === "copy" && (el.tagName === "BUTTON" || 
            (el.className && typeof el.className === "string" && el.className.toLowerCase().includes("copy")))) {
            el.remove();
        }
    });

    // ==========================================
    // 9. 📊 表格分类打标系统（6种类型）
    // ==========================================
    
    // 9.1 全局清理：剥离硬编码宽度
    clone.querySelectorAll("table").forEach((tbl) => {
        if (tbl.getAttribute("border") === "0" || tbl.classList.contains("borderless")) {
            tbl.classList.add("siemens-table-no-grid");
        } else if (tbl.getAttribute("border") === "1" || tbl.classList.contains("siemens-table-grid")) {
            tbl.classList.add("siemens-table-with-grid");
        }

        const tblW = tbl.getAttribute("width");
        if (!tblW || (!tblW.includes("%") && !tblW.includes("*"))) tbl.removeAttribute("width");
        tbl.removeAttribute("style");
        
        tbl.querySelectorAll("col, colgroup, td, th").forEach((el) => {
            const w = el.getAttribute("width");
            if (!w || (!w.includes("%") && !w.includes("*"))) el.removeAttribute("width");
            el.removeAttribute("style");
        });
    });

    // 9.2 嵌套表格处理
    clone.querySelectorAll("table td table").forEach((tbl) => {
        tbl.classList.add("nested-child-table");

        if (tbl.classList.contains("tree") || tbl.classList.contains("navigator") || 
            tbl.closest(".tree") || tbl.closest(".navigator")) {
            tbl.classList.add("nested-text-table");
            tbl.classList.add("siemens-table-no-grid");
        } else if (tbl.querySelector("img:not(.inline-small-icon)")) {
            tbl.classList.add("multi-image-layout-table");
            if (!tbl.classList.contains("siemens-table-with-grid")) {
                tbl.classList.add("siemens-table-no-grid");
            }
        } else {
            tbl.classList.add("nested-text-table");
        }
    });

    // 9.3 外层表格分类
    clone.querySelectorAll("table:not(.locator):not(.navigator):not(.siemens-table-no-grid):not(.nested-child-table)")
        .forEach((table) => {
            if (table.classList.contains("siemens-options-table")) {
                table.classList.add("nested-parent-table");
                return;
            }

            let hasNested = table.querySelector("table") !== null;
            let hasImg = table.querySelector("img, object, svg, picture") !== null;
            let hasPre = table.querySelector("pre") !== null;
            let hasPreInFirstCol = false;
            let isToxic = false;
            let isFirstColHuge = false;

            const rows = Array.from(table.rows || []);
            rows.forEach((row) => {
                if (row.cells.length > 0) {
                    if ((row.cells[0].textContent || "").trim().length > 150) isFirstColHuge = true;
                    if (row.cells[0].querySelector("pre")) hasPreInFirstCol = true;
                }
                Array.from(row.cells).forEach((cell) => {
                    const text = (cell.innerHTML || "").replace(/<[^>]+>/g, " ");
                    const words = text.trim().split(/[^a-zA-Z0-9\u4e00-\u9fa5_-]+/);
                    for (let w of words) if (w.length > 45) isToxic = true;
                });
            });

            // 套上滚动保护壳
            if (table.parentElement && !table.parentElement.classList.contains("table-scroll-wrapper")) {
                const wrapper = frameDoc.createElement("div");
                wrapper.className = hasImg ? "table-scroll-wrapper has-horizontal-scroll" : "table-scroll-wrapper";
                table.parentNode.insertBefore(wrapper, table);
                wrapper.appendChild(table);
            }

            if (hasNested) {
                table.classList.add("nested-parent-table");
            } else if (hasImg && isFirstColHuge) {
                table.classList.add("media-code-table");
            } else if (hasPreInFirstCol) {
                table.classList.add("code-comparison-table");
            } else if (isFirstColHuge) {
                table.classList.add("nested-parent-table");
            } else if (isToxic || hasPre) {
                table.classList.add("toxic-wide-table");
                if (table.parentElement && table.parentElement.classList.contains("table-scroll-wrapper")) {
                    table.parentElement.classList.add("toxic-scroll-wrapper");
                }
                rows.forEach((row) => {
                    if (row.cells.length > 1) row.cells[0].classList.add("toxic-col-left");
                });
            } else if (hasImg) {
                table.classList.add("image-table-layout");
            }
        });

    // 9.4 表头修复
    clone.querySelectorAll("table.image-table-layout, table.multi-image-layout-table").forEach((table) => {
        const firstRow = table.querySelector("tr");
        if (firstRow && !firstRow.querySelector("img:not(.inline-small-icon)")) {
            firstRow.classList.add("pseudo-header-row");
        }
    });

    // ==========================================
    // 10. CSS 收集与绝对化
    // ==========================================
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

    function cssTextToAbsoluteUrls(cssText, baseUrl) {
        return cssText.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (match, quote, url) => {
            const raw = url.trim();
            if (!raw || raw.startsWith("data:") || raw.startsWith("blob:") || raw.startsWith("#")) return match;
            if (/^https?:\/\//i.test(raw)) return `url(${quote}${raw}${quote})`;
            try {
                return `url(${quote}${new URL(raw, baseUrl).href}${quote})`;
            } catch (e) {
                return match;
            }
        });
    }

    const processedCss = cssTextToAbsoluteUrls(cssText, baseUrl);

    // ==========================================
    // 11. 文本清理
    // ==========================================
    const walker = frameDoc.createTreeWalker(clone, NodeFilter.SHOW_TEXT, null, false);
    let node;
    while ((node = walker.nextNode())) {
        if (/[\u00A0\u202F\u2007\u2060]/.test(node.nodeValue)) {
            node.nodeValue = node.nodeValue.replace(/[\u00A0\u202F\u2007\u2060]/g, " ");
        }
    }

    clone.querySelectorAll('script, link[rel="stylesheet"], style, meta, title').forEach((el) => el.remove());

    let finalHtml = clone.innerHTML.trim().replace(/&nbsp;/gi, " ").replace(/\u00A0/g, " ");

    // ==========================================
    // 12. 返回结果
    // ==========================================
    const tableSigs = Array.from(clone.querySelectorAll('table')).map(t => {
        const rows = t.querySelectorAll('tr').length;
        const firstRow = t.querySelector('tr');
        const cols = firstRow ? firstRow.children.length : 0;
        return {
            classes: Array.from(t.classList || []),
            isLocator: t.classList && t.classList.contains('locator'),
            hasColgroup: !!t.querySelector('colgroup'),
            hasThead: !!t.querySelector('thead'),
            hasTbody: !!t.querySelector('tbody'),
            rows,
            cols
        };
    });

    return { 
        html: finalHtml, 
        css: processedCss, 
        tableSigs,
        tableClasses: {
            toxicWide: clone.querySelectorAll('.toxic-wide-table').length,
            nestedParent: clone.querySelectorAll('.nested-parent-table').length,
            nestedChild: clone.querySelectorAll('.nested-child-table').length,
            multiImage: clone.querySelectorAll('.multi-image-layout-table').length,
            codeComparison: clone.querySelectorAll('.code-comparison-table').length,
            mediaCode: clone.querySelectorAll('.media-code-table').length,
            imageLayout: clone.querySelectorAll('.image-table-layout').length
        }
    };
}

                
                """)

                extracted_data = result['html']
                extracted_css = result.get('css', '')
                extracted_table_sigs = result.get('tableSigs', [])
                # 🚀 新增：获取表格分类统计
                table_classes = result.get('tableClasses', {})
                if table_classes:
                    print(f"   📊 表格分类: 毒性宽表={table_classes.get('toxicWide', 0)}, "
                          f"嵌套父表={table_classes.get('nestedParent', 0)}, "
                          f"图文表={table_classes.get('multiImage', 0)}, "
                          f"代码对照表={table_classes.get('codeComparison', 0)}")

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

                    # table inventory report (per page)
                    try:
                        update_table_report(table_report, title, url, extracted_table_sigs)
                    except Exception as e:
                        print(f"⚠️ 表格统计失败: {e}")

                    if extracted_css:
                        css_hash = hashlib.md5(extracted_css.encode('utf-8')).hexdigest()
                        if css_hash not in seen_css_hashes:
                            global_styles.append(extracted_css)
                            seen_css_hashes.add(css_hash)
                        else:
                            # 调试：CSS 去重命中（可能存在误杀/过度去重）
                            # print(f"   🧪 [CSS去重命中] {title} -> {css_hash[:10]} (已存在)")
                            pass

                        try:
                            db_conn.execute("INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)",
                                            (css_hash, extracted_css))
                        except:
                            pass

                    stats.success_count += 1
                    print(f"[{i + 1}] {title} (✓ 抓取成功) | {stats.get_progress_info()}")

                if title in progress['failed']: progress['failed'].remove(title)
                if title not in progress['completed']: progress['completed'].append(title)

                try:
                    css_hash_val = hashlib.md5(extracted_css.encode('utf-8')).hexdigest() if extracted_css else ""
                    # 写入时增加 url 字段
                    db_conn.execute("REPLACE INTO cache (url, title, html, css_hash) VALUES (?, ?, ?, ?)",
                                    (url, title, extracted_data, css_hash_val))
                    db_conn.commit()
                except Exception as e:
                    print(f"写入数据库失败: {e}")

        stats.processed_pages += 1
        if stats.processed_pages % 10 == 0: save_progress(progress)

    finally:
        await page_pool.put(page)


async def main():
    global START_URL, SIDEBAR_TITLE, FINAL_OUTPUT_FILE, CACHE_DB_FILE, NAV_JSON_FILE
    
    # 如果有多个主题，让用户选择
    if len(SUBJECTS) > 1:
        print("\n📋 可用主题列表:")
        for i, sub in enumerate(SUBJECTS, 1):
            print(f"   {i}. {sub['title']}")
        print()
        
        choice = input(f"请选择主题 [1-{len(SUBJECTS)}] (默认 1): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(SUBJECTS):
            selected = SUBJECTS[int(choice) - 1]
        else:
            selected = SUBJECTS[0]
    elif len(SUBJECTS) == 1:
        selected = SUBJECTS[0]
    else:
        print("❌ 没有可处理的主题，请检查 download_list.txt 文件")
        sys.exit(1)
    
    # 动态设置全局变量
    START_URL = selected['url']
    SIDEBAR_TITLE = selected['title'].replace(' ', '&nbsp;&nbsp;')
    FINAL_OUTPUT_FILE = f"{selected['name']}.html"
    CACHE_DB_FILE = os.path.join(OUTPUT_DIR, f"db_{selected['name']}.db")
    NAV_JSON_FILE = os.path.join(OUTPUT_DIR, f"nav_{selected['name']}.json")
    
    print(f"\n{'='*60}")
    print(f" 当前主题: {selected['title']}")
    print(f" 数据库: {CACHE_DB_FILE} |  输出: {FINAL_OUTPUT_FILE}")
    print(f"{'='*60}\n")
    
    start_time = time.time()
    stats = ProcessingStats()
    progress = None
    seen_hashes = {}
    valid_indices = set()
    duplicate_map = {}
    global_styles = []
    seen_css_hashes = set()
    contents_dict = {}

    table_report = {
        "total_pages_with_tables": 0,
        "total_tables": 0,
        "by_signature": {},
        "by_class": {}
    }

    lock = asyncio.Lock()

    print("=" * 50)
    print("🚀 NX文档聚合器 - 终极全自动版 (v11.28 修复版：兼容 JS 版生成的 JSON 结构)")
    print("=" * 50)
    print("[a] 全自动一键探测 (探测结构 + 并发抓取)")
    print("[c] 增量续传模式 (基于现有 SQLite 恢复)")
    print("[r] 彻底重抓模式 (清空旧数据从零开始)")
    mode = input("请选择 [a/c/r] (默认回车为 c 续传): ").strip().lower()
    if not mode or mode not in ['a', 'c', 'r']: mode = 'c'

    # 处理模式选择与旧数据清理
    if mode == 'r':
        print("🗑️ 正在清洗旧缓存与数据库...")
        if os.path.exists(CACHE_DB_FILE): os.remove(CACHE_DB_FILE)
        if os.path.exists('progress.json'): os.remove('progress.json')
        if os.path.exists(NAV_JSON_FILE): os.remove(NAV_JSON_FILE)

    # 🗄️ 初始化 SQLite 数据库 (升级表结构)
    db_conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)

    # 检查表结构是否需要迁移 (简单起见，如果表存在但结构不对，建议用户选 r 重抓)
    # 这里我们直接创建新表结构。如果旧表存在且结构不同，可能会报错。
    # 为了稳健，我们尝试创建 styles 表。
    db_conn.execute("CREATE TABLE IF NOT EXISTS styles (hash TEXT PRIMARY KEY, content TEXT)")

    # 检查 cache 表是否有 css_hash 列。如果没有，说明是旧版数据库。
    # 简单处理：如果用户选 c 但数据库是旧版，可能会出错。
    # 建议：如果数据库存在，检查表结构。
    try:
        db_conn.execute("SELECT css_hash FROM cache LIMIT 1")
    except sqlite3.OperationalError:
        # 列不存在，说明是旧版数据库。
        if mode == 'c' and os.path.exists(CACHE_DB_FILE):
            print("⚠️ 检测到旧版数据库结构，正在自动迁移数据...")
            # 简单的迁移策略：重命名旧表，创建新表，尝试迁移数据（CSS 哈希化）
            # 但这比较复杂。最简单的策略是：提示用户重抓。
            # 或者，我们直接 drop table cache 并重建，强制重抓。
            print("⚠️ 旧版数据库无法直接兼容 CSS 去重特性，将清除旧缓存并强制重抓。")
            db_conn.execute("DROP TABLE IF EXISTS cache")
            mode = 'r'  # 强制转为重抓模式

    # 修复：使用 url 作为主键，彻底解决同名目录相互覆盖的 Bug
    db_conn.execute("CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, title TEXT, html TEXT, css_hash TEXT)")

    # 🚀 数据库诊断与优化
    print("🧹 正在执行数据库 VACUUM 压缩...")
    db_conn.execute("VACUUM")
    db_conn.commit()

    try:
        cursor = db_conn.execute("SELECT count(*), avg(length(html)), max(length(html)) FROM cache")
        row = cursor.fetchone()
        if row and row[0] > 0:
            print(
                f"📊 数据库统计: {row[0]} 条记录 | 平均 HTML 大小: {int(row[1] or 0)} 字节 | 最大 HTML 大小: {int(row[2] or 0)} 字节")
    except Exception as e:
        print(f"⚠️ 无法获取数据库统计: {e}")

    progress = load_progress()
    if mode == "r":
        progress = {'completed': [], 'failed': []}
        if os.path.exists(FINAL_OUTPUT_FILE):
            try:
                os.remove(FINAL_OUTPUT_FILE)
            except:
                pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1440, 'height': 960},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # 获取目录结构
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
                # 修复点：优先读取 'url' 字段（JS 版生成），如果不存在则读取 'href'（Python 版生成）
                url = n.get('url') if 'url' in n else n.get('href', '')
                
                pages.append({
                    'text': n['text'],
                    'href': url,
                    'has_href': bool(url and url.strip() and 'javascript:void(0)' not in url)
                })
                if n.get('children'): flatten(n['children'])

        flatten(nav_structure)
        stats.total_pages = len(pages)

        # 🚀 极限网络断流拦截优化：精准屏蔽多余字体与无用长连接
        async def route_intercept(route):
            block_types = ["media", "beacon", "csp_report", "font", "websocket"]
            if route.request.resource_type in block_types:
                await route.abort()
            else:
                await route.continue_()

        # 🚀 建立 Page Pool (页面池)
        print(f"⚙️ 正在初始化浏览器核心引擎 (并发量: {MAX_CONCURRENCY})...")
        page_pool = asyncio.Queue()
        for _ in range(MAX_CONCURRENCY):
            p = await context.new_page()
            await p.route("**/*", route_intercept)
            await page_pool.put(p)

        def cleanup_handler(signum, frame):
            print("\n\n🚨 收到中断信号，保存并退出...")
            stats.interrupted = True
            save_progress(progress)
            db_conn.close()
            sys.exit(0)

        signal.signal(signal.SIGINT, cleanup_handler)
        signal.signal(signal.SIGTERM, cleanup_handler)

        tasks = []
        for i, page_info in enumerate(pages):
            task = asyncio.create_task(
                process_page(i, page_info['text'], page_info['href'], page_info['has_href'],

                             page_pool, stats, progress, mode, lock, db_conn,
                             seen_hashes, seen_css_hashes, global_styles, contents_dict, valid_indices, duplicate_map,
                             table_report)
            )
            tasks.append(task)

        # 开始并发执行
        await asyncio.gather(*tasks)

        # 清理浏览器环境
        await browser.close()
        save_progress(progress)
        db_conn.close()
        # 写出表格类型统计报告
        write_table_reports(table_report)
        print(f"📄 表格统计已输出: {TABLE_REPORT_TXT} / {TABLE_REPORT_JSON}")

        # 🛡️ OOM 内存防爆：流式写入合成最终 HTML
        # 修复：移除 if contents_dict: 判断，强制执行写入逻辑
        print(f"⏳ 正在合成并流式写入最终 HTML (包含 {len(contents_dict)} 个页面)...")
        tree_html = generate_tree_navigation(nav_structure, valid_indices, duplicate_map)
        combined_css = "\n".join(global_styles)

        with open(FINAL_OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Siemens NX 文档库</title>
    <style>{combined_css}
        
/* ============================================================================
   🚀 JS 版功能增强 CSS（完整复刻 nxdoc_scraper_no_img完美版.js）
   ============================================================================ */
/* ============================================================================
   ultra_simple8.py 增强版 CSS 样式
   完整复刻 nxdoc_scraper_no_img完美版.js 的500+行精准修复
   使用方法：替换 ultra_simple8.py 中 generate_html 函数的 CSS 部分
   ============================================================================ */

:root {{
    --siemens-blue: #009999;
    --siemens-blue-dark: #007a7a;
    --bg-color: #ffffff;
    --text-color: #333333;
    --sidebar-bg: #f8f9fa;
    --border-color: #e9ecef;
    --code-bg: #f6f8fa;
    --link-color: #0066cc;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    line-height: 1.6;
    color: var(--text-color);
    background: var(--bg-color);
    display: flex;
    min-height: 100vh;
}}

/* ==========================================
   侧边栏样式
   ========================================== */
.sidebar {{
    width: 280px;
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border-color);
    overflow-y: auto;
    position: fixed;
    height: 100vh;
    left: 0;
    top: 0;
    z-index: 100;
}}

.sidebar-header {{
    padding: 20px;
    background: var(--siemens-blue);
    color: white;
    font-size: 18px;
    font-weight: bold;
    text-align: center;
    position: sticky;
    top: 0;
    z-index: 101;
}}

.sidebar-nav ul {{
    list-style: none;
    padding: 0;
}}

.sidebar-nav li {{
    border-bottom: 1px solid #e0e0e0;
}}

.sidebar-nav a {{
    display: block;
    padding: 12px 20px;
    color: #333;
    text-decoration: none;
    transition: all 0.2s;
}}

.sidebar-nav a:hover {{
    background: rgba(0, 153, 153, 0.1);
    color: var(--siemens-blue);
}}

.sidebar-nav .active {{
    background: var(--siemens-blue);
    color: white !important;
    font-weight: bold;
}}

.sidebar-nav .level-1 > a {{
    font-weight: 600;
    padding-left: 20px;
}}

.sidebar-nav .level-2 > a {{
    padding-left: 40px;
    font-size: 14px;
}}

.sidebar-nav .level-3 > a {{
    padding-left: 60px;
    font-size: 13px;
    color: #666;
}}

/* ==========================================
   主内容区
   ========================================== */
.main-content {{
    margin-left: 280px;
    flex: 1;
    padding: 40px;
    max-width: 1200px;
}}

.content-wrapper h1:first-child {{
    color: var(--siemens-blue);
    font-size: 32px;
    margin-bottom: 30px;
    padding-bottom: 15px;
    border-bottom: 2px solid var(--siemens-blue);
}}

.content-wrapper h2 {{
    color: var(--siemens-blue-dark);
    font-size: 24px;
    margin-top: 40px;
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-color);
}}

.content-wrapper h3 {{
    font-size: 20px;
    margin-top: 30px;
    margin-bottom: 15px;
    color: #444;
}}

.content-wrapper p {{
    margin-bottom: 15px;
    line-height: 1.8;
}}

.content-wrapper img {{
    max-width: 100%;
    height: auto;
    display: block;
    margin: 20px auto;
}}

.content-wrapper pre {{
    background: var(--code-bg);
    padding: 15px;
    border-radius: 5px;
    overflow-x: auto;
    margin: 15px 0;
    border: 1px solid #e1e4e8;
}}

.content-wrapper code {{
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 14px;
}}

.content-wrapper a {{
    color: var(--link-color);
    text-decoration: none;
}}

.content-wrapper a:hover {{
    text-decoration: underline;
}}

/* ==========================================
   🚀 表格精准修复系统（6种分类）
   ========================================== */

/* 基础表格重置 */
.main-content table {{
    border-collapse: collapse;
    margin: 20px 0;
    width: auto;
    max-width: 100%;
}}

/* 1️⃣ 毒性宽表 - 超长文本强制换行 */
.toxic-wide-table {{
    table-layout: fixed;
    width: 100% !important;
    word-wrap: break-word;
    word-break: break-all;
    overflow-wrap: anywhere;
}}

.toxic-wide-table td,
.toxic-wide-table th {{
    word-break: break-all;
    overflow-wrap: anywhere;
    white-space: normal !important;
    max-width: 0;
}}

.toxic-col-left {{
    width: 30% !important;
    vertical-align: top;
}}

.toxic-scroll-wrapper {{
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}}

/* 2️⃣ 嵌套父表格 */
.nested-parent-table {{
    table-layout: auto;
    width: max-content;
    max-width: 100%;
}}

.nested-parent-table td {{
    vertical-align: top;
    padding: 8px 12px;
}}

/* 3️⃣ 嵌套子表格 */
.nested-child-table {{
    width: 100%;
    margin: 0;
    border: none;
}}

.nested-text-table {{
    table-layout: auto;
    width: auto;
}}

.nested-text-table td,
.nested-text-table th {{
    white-space: nowrap;
    padding: 4px 8px;
}}

/* 4️⃣ 图文对照表 */
.multi-image-layout-table {{
    table-layout: auto;
    width: max-content;
    max-width: 100%;
}}

.multi-image-layout-table td {{
    vertical-align: middle;
    padding: 8px;
}}

.multi-image-layout-table img {{
    max-width: 200px;
    height: auto;
    display: inline-block;
    margin: 0;
}}

/* 5️⃣ 代码对照表 */
.code-comparison-table {{
    table-layout: fixed;
    width: 100%;
}}

.code-comparison-table td {{
    vertical-align: top;
    padding: 8px;
}}

.code-comparison-table pre {{
    margin: 0;
    white-space: pre-wrap;
    word-break: break-all;
}}

/* 6️⃣ 媒体代码混合表 */
.media-code-table {{
    table-layout: auto;
    width: max-content;
    max-width: 100%;
}}

.media-code-table td {{
    vertical-align: top;
    padding: 8px;
}}

/* 图片布局表 */
.image-table-layout {{
    table-layout: auto;
    width: max-content;
    max-width: 100%;
}}

.image-table-layout td {{
    vertical-align: middle;
    padding: 8px;
}}

.pseudo-header-row {{
    font-weight: bold;
    background: #f8f9fa;
}}

.pseudo-header-row td {{
    border-bottom: 2px solid var(--siemens-blue);
}}

/* 滚动包装器 */
.table-scroll-wrapper {{
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    margin: 15px 0;
}}

.has-horizontal-scroll {{
    overflow-x: scroll !important;
}}

/* 无边框表格 */
.siemens-table-no-grid {{
    border: none !important;
}}

.siemens-table-no-grid td,
.siemens-table-no-grid th {{
    border: none !important;
}}

/* 带边框表格 */
.siemens-table-with-grid {{
    border: 1px solid #ddd;
}}

.siemens-table-with-grid td,
.siemens-table-with-grid th {{
    border: 1px solid #ddd;
    padding: 8px 12px;
}}

/* ==========================================
   🌟 小图标保护
   ========================================== */
.inline-small-icon {{
    display: inline !important;
    vertical-align: middle;
    max-width: 80px !important;
    height: auto;
    margin: 0 4px;
}}

/* ==========================================
   🎥 视频播放器修复
   ========================================== */
video {{
    width: 100%;
    height: auto;
    aspect-ratio: 16/9;
    display: block;
    background: #000;
    margin: 20px 0;
}}

disw-video {{
    display: block;
    margin: 20px 0;
}}

.aspectRatioHeightControl {{
    width: 100%;
}}

/* 移除 Plyr 残留 */
.plyr__controls,
.plyr__control--overlaid,
.plyr__captions,
.plyr__poster {{
    display: none !important;
}}

/* ==========================================
   响应式设计
   ========================================== */
@media (max-width: 768px) {{
    .sidebar {{
        width: 100%;
        height: auto;
        position: relative;
    }}

    .main-content {{
        margin-left: 0;
        padding: 20px;
    }}

    .content-wrapper h1:first-child {{
        font-size: 24px;
    }}

    .content-wrapper h2 {{
        font-size: 20px;
    }}
}}

/* ==========================================
   打印优化
   ========================================== */
@media print {{
    .sidebar {{
        display: none;
    }}

    .main-content {{
        margin-left: 0;
        padding: 20px;
    }}

    .content-wrapper a {{
        color: #000;
        text-decoration: underline;
    }}

    .table-scroll-wrapper {{
        overflow: visible;
    }}
}}

</style>
    <style>
        body {{ display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; color: #333; }}

        .nx-sidebar {{ width: 340px; min-width: 250px; display: flex; flex-direction: column; background: #f8f9fa; border-right: 1px solid #dee2e6; }}

        .nx-sidebar-header {{ padding: 15px 10px; background: #f8f9fa; border-bottom: 2px solid #007cba; flex-shrink: 0; }}
        .nx-sidebar-content {{ flex: 1; overflow-y: auto; padding: 5px; }}

        .resizer {{ width: 5px; cursor: col-resize; background: #dee2e6; transition: background 0.2s; }}
        .resizer:hover {{ background: #007cba; }}
        .main-content {{ flex: 1; overflow-y: auto; scroll-behavior: smooth; padding: 0; background: #fff; line-height: 1.6; }}
        .content-wrapper {{ max-width: 100%; margin: 0 auto; padding: 40px; }}

        /* 优化点：统一正文主标题颜色 */
        .content-wrapper h1 {{ font-size: 32px !important; color: #007cba !important; font-weight: bold; margin-bottom: 15px; }}

        /* 修复点：彻底移除所有针对表格的强制样式，完全依赖页面自带 CSS */

        .nx-sidebar ul, .nx-sidebar ul.root-list {{ list-style: none; margin: 0; padding: 0; }}
        .nx-sidebar li {{ margin: 2px 0; padding: 0; }}

        .nav-item-row {{ display: flex; align-items: flex-start; margin: 2px 0; }}

        ul.nested {{ 
            display: none; 
            padding-left: 0px !important;
            border-left: 1px solid #888 !important;
            margin-left: 6px !important; 
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
        /* 优化点：调整选中目录项的背景色 */
        .selected-link {{ background: #ADD8E6 !important; color: #000 !important; font-weight: 600 !important; }}

        .page-section {{ margin-bottom: 80px; padding-top: 30px; border-top: 2px solid #eaeaea; }}
        .page-section:first-child {{ border-top: none; padding-top: 0; }}

        /* --- REV 2026-02-19 #06: table readability (offline) --- */
        /*
          来自 table_report：页面里主要表格类型为：
          - (no-class) 占多数：离线页容易被挤压导致换行/变形
          - locator：用于“位于何处”等小表
          - navigator：少量表格
        */

        /* 1) no-class：尽量保持单元格不乱换行；同时允许横向滚动兜底。 */
        .main-content table:not([class]) td,
        .main-content table:not([class]) th {{
            white-space: nowrap;
        }}
        .main-content .content-wrapper:has(table:not([class])) {{
            overflow-x: auto;
        }}

        /* 2) navigator：同样避免换行导致的“表格竖排”。 */
        .main-content table.navigator td,
        .main-content table.navigator th {{
            white-space: nowrap;
        }}
        .main-content .content-wrapper:has(table.navigator) {{
            overflow-x: auto;
        }}

        /* 3) locator：常见于“位于何处?”这类两列小表。 */
        .main-content table.locator {{
            table-layout: auto;
            width: max-content;           /* allow table to be as wide as needed */
            max-width: 100%;
        }}
        .main-content table.locator td,
        .main-content table.locator th {{
            white-space: nowrap;          /* prevent tight tables from wrapping into multiple lines */
        }}
        /* 仅当页面存在 locator 表格时，横向滚动兜底（让表格自己滚，不挤压内容）。 */
        .main-content .content-wrapper:has(table.locator) {{
            overflow-x: auto;
         }}
    </style>

</head>
<body>

    <div class="nx-sidebar">
        <div class="nx-sidebar-header">
            <h3 style="color: #007cba; margin: 0; text-align: center; font-size: 24px;">{SIDEBAR_TITLE}</h3>
        </div>
        <div class="nx-sidebar-content">{tree_html}</div>
    </div>
    <div class="resizer" id="resizer"></div>
    <div class="main-content">
        <div class="content-wrapper">
""")
            for k in sorted(contents_dict.keys()):
                f.write(contents_dict[k] + "\n")

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
            f"\n📊 最终统计: 独立内容 {len(contents_dict)} 页 | 复用 {stats.redundant_count} 页 | 失败 {stats.failed_count} 页")
        print(f"🎉 任务完成！输出文件: {FINAL_OUTPUT_FILE}")
        print(f"⏱️ 总耗时: {mins}分 {secs}秒")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())