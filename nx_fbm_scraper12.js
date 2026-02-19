// ==========================================================
// NX2506 文档抓取器 (V121 - 样式完全恢复版)
// 核心革命：
// 1. [纯数据库] 彻底抛弃本地 HTML 碎片文件，所有内容直接存取 SQLite。
// 2. [真·续传] 任务开始前先查库，存在即跳过，0 IO 消耗，秒级恢复进度。
// 3. [极速合并] 最终生成 HTML 时直接从数据库流式读取，不再依赖磁盘碎片。
// 4. [进度修正] 修复进度条乱报问题，准确反映剩余任务。
// 5. [稳定性] 增强 Worker 健壮性，遇到任何错误直接销毁并重建页面实例。
// 6. [Frame修复] 重构 Frame 获取逻辑，不再依赖 contentFrame()，改用遍历查找。
// 7. [日志优化] 找回丢失的线程标识，优化 ETA 算法。
// 8. [静默运行] 改用 Headless 模式启动，后台运行无干扰，退出自动清理。
// 9. [上下文修复] 修复 Headless 模式下缺少默认 Context 导致的启动错误。
// 10. [样式恢复] 将 CSS 样式完全恢复为 ultra_simple7.py 的版本。
// ==========================================================

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");
const { chromium } = require("playwright");
const sqlite3 = require("sqlite3").verbose();

// ==========================================
// ⚙️ 全局配置区
// ==========================================
// const START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/feat_based_mach_fbm_overview";
const START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20241101461013487.mfgholemaking/feat_based_mach_fbm_overview";
const FINAL_OUTPUT_FILE = "NX2506基于特征加工-js.html";
const CACHE_DIR_NAME = "NX2506_pages"; // 仅用于存放 CSS 文件
const SIDEBAR_TITLE = "NX2506&nbsp;&nbsp;基于特征加工";
const MAX_CONCURRENCY = 5; // 保持较低并发以稳定运行
const NAV_JSON_FILE = "NX2506_nav_structure.json";
const CACHE_DB_FILE = "NX2506_pages.db";

const CACHE_DIR = path.join(__dirname, CACHE_DIR_NAME);
const TARGET_IFRAME_SELECTOR = "#xhtml";
const md5 = (s) => crypto.createHash("md5").update(s).digest("hex");

// ==========================================
// 🚀 数据库核心函数
// ==========================================

function initDatabase() {
    const db = new sqlite3.Database(CACHE_DB_FILE);
    db.serialize(() => {
        db.run(`CREATE TABLE IF NOT EXISTS styles (hash TEXT PRIMARY KEY, content TEXT)`);
        // 【防撞补丁】：将主键 (PRIMARY KEY) 从 title 改为 url，并保留 title 字段
        db.run(`CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, title TEXT, html TEXT, css_hash TEXT)`);
    });
    return db;
}

// 【防撞补丁】：改为通过全局唯一的 url 进行查重判定
function checkPageExists(db, url) {
    return new Promise((resolve) => {
        db.get("SELECT 1 FROM cache WHERE url = ?", [url], (err, row) => {
            resolve(!!row);
        });
    });
}

// 【防撞补丁】：写入数据库时，要求同时传入 url 和 title
function saveToDatabase(db, url, title, html, css) {
    return new Promise((resolve, reject) => {
        const cssHash = css ? md5(css) : "";
        db.serialize(() => {
            if (css && cssHash) {
                const stmt = db.prepare("INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)");
                stmt.run(cssHash, css);
                stmt.finalize();
            }
            // 写入时增加 url 字段匹配新的表结构
            const stmt = db.prepare("INSERT OR REPLACE INTO cache (url, title, html, css_hash) VALUES (?, ?, ?, ?)");
            stmt.run(url, title, html, cssHash, (err) => {
                if (err) reject(err);
                else resolve();
            });
            stmt.finalize();
        });
    });
}

function getDatabaseStats(db) {
    return new Promise((resolve) => {
        db.get("SELECT COUNT(*) as count FROM cache", (err, row) => {
            resolve(row ? row.count : 0);
        });
    });
}

// ==========================================
// 🌐 浏览器相关函数
// ==========================================

function cssTextToAbsoluteUrls(cssText, baseUrl) {
    return String(cssText).replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (m, quote, url) => {
        const raw = String(url).trim();
        if (!raw || raw.startsWith("data:") || raw.startsWith("blob:") || raw.startsWith("#")) return m;
        if (/^https?:\/\//i.test(raw)) return `url(${quote}${raw}${quote})`;
        try { return `url(${quote}${new URL(raw, baseUrl).href}${quote})`; } catch { return m; }
    });
}

// 添加与Python脚本相同的表格处理逻辑
function processTableSignatures(htmlContent) {
    // 这里可以添加表格签名统计逻辑，与Python版本保持一致
    // 暂时返回空数组以保持接口一致性
    return [];
}

async function buildInlineCss(frame) {
    if (!frame) return ""; // 防御性检查

    const meta = await frame.evaluate(() => {
        const baseUrl = document.baseURI;
        const inline = Array.from(document.querySelectorAll("style")).map((s) => s.textContent || "").join("\n\n");
        const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map((l) => l.href).filter(Boolean);
        return { baseUrl, inline, links };
    });

    let external = "";
    for (const href of meta.links) {
        try {
            const css = await frame.evaluate(async (url) => {
                try {
                    const resp = await fetch(url);
                    return resp.ok ? await resp.text() : "";
                } catch { return ""; }
            }, href);
            if (css) external += `\n/* ${href} */\n${css}`;
        } catch {}
    }

    const combined = `${meta.inline}\n${external}`;
    return cssTextToAbsoluteUrls(combined, meta.baseUrl);
}

async function capturePageContent(page) {
    // 等待 iframe 元素出现
    try {
        await page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 20000 });
    } catch (e) {
        throw new Error(`等待 iframe 超时: ${e.message}`);
    }

    // 🚀 重构：通过遍历 frames 查找目标 frame，而不是依赖 elementHandle.contentFrame()
    // 这种方式在某些情况下更稳定
    let frame = page.frames().find(f => f.name() === 'xhtml');

    // 如果没找到，尝试通过 url 匹配（备用方案）
    if (!frame) {
        frame = page.frames().find(f => f.url().includes('/documentation/') || f.url().includes('help'));
    }

    if (!frame) {
        // 最后尝试一次 contentFrame
        const element = await page.$(TARGET_IFRAME_SELECTOR);
        if (element) frame = await element.contentFrame();
    }

    if (!frame) throw new Error("无法获取目标 Frame (frame is null/undefined)");

    return await frame.evaluate(async () => {
        // 移除不需要的元素
        const unwanted = [
            '.navbar', '.header', '.footer', '#doc-sidebar', '.breadcrumb',
            '.site-header', '.topbar', '.app-header', '.global-nav', '.nav-header',
            '.doc-sidebar', '#topic-navigator', '.hidden-md-up', '#feedback-btns',
            '.gutter', '.cookie-banner', '.cookie-consent', '.gdpr-banner'
        ];
        unwanted.forEach(s => document.querySelectorAll(s).forEach(e => e.remove()));

        // 页脚关键词清理 - 移除相关链接块
        const footerKeywords = [
            'Learn more', 'How do I', 'Look up more details', 'See also', 'See Also',
            'Related Concepts', 'Related Reference', 'Related Topics', 'Related Tasks', 
            'Related Information', 'Related Links',
            '相关概念', '相关参考', '相关主题', '相关任务', '相关信息', '相关链接',
            '了解更多', '如何操作', '如何...', '查找更多详细信息', '另请参见'
        ];

        Array.from(document.querySelectorAll('*')).forEach(el => {
            const txt = (el.textContent || '').trim();
            if (footerKeywords.includes(txt) && /^(H[1-6]|STRONG|B|DIV|SPAN)$/i.test(el.tagName)) {
                const wrapper = el.closest('.container-fluid, .related-links, .topic-links, .familylinks');
                if (wrapper && wrapper !== document.body && (wrapper.textContent || '').length < 2000) {
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

        // 获取正文
        const container = document.querySelector('div.doc-content') || document.body;

        // 处理图片链接
        const baseUrl = document.baseURI;
        container.querySelectorAll('img, a').forEach(el => {
            if (el.hasAttribute('src')) el.src = new URL(el.getAttribute('src'), baseUrl).href;
            if (el.hasAttribute('href')) el.href = new URL(el.getAttribute('href'), baseUrl).href;
        });

        return {
            html: container.innerHTML.trim(),
            docClass: container.className,
            mainClass: container.id
        };
    }).then(data => ({ frame, data })); // 将 frame 一并返回
}

// ==========================================
// 🌲 目录树处理
// ==========================================

// 全局索引计数器，用于生成与Python脚本兼容的页面ID
let globalPageIndex = 0;

function renderSidebarHtml(nodes, level = 0) {
    if (!nodes || nodes.length === 0) return "";
    let html = level === 0 ? '<ul class="root-list active">\n' : '';

    function buildTree(nodes, level) {
        nodes.forEach((node) => {
            const currentPageIndex = globalPageIndex++;
            html += `    <li class="nav-level-${level}">\n        <div class="nav-item-row">\n`;

            const hasChildren = node.children && node.children.length > 0;
            const caretClass = level === 0 ? "caret caret-down" : "caret";

            if (hasChildren) {
                html += `            <span class="${caretClass}" onclick="toggleNode(this)"></span>\n`;
            } else {
                html += `            <span class="no-caret"></span>\n`;
            }

            if (!node.url || node.url.includes("javascript:void(0)") || node.url.trim() === "#") {
                html += `            <span class="folder-text" onclick="toggleNode(this.previousElementSibling)">${node.text}</span>\n`;
            } else {
                html += `            <a href="#page_${currentPageIndex}" onclick="handleManualClick(this)">${node.text}</a>\n`;
            }

            html += `        </div>\n`;
            if (hasChildren) {
                // 【修复核心1】必须在遍历子节点之前，先开启带有嵌套样式的 ul 标签
                const activeClass = level === 0 ? ' active' : '';
                html += `        <ul class="nested${activeClass}">\n`;

                // 【修复核心2】删除了原来的 globalPageIndex = 0 的重置逻辑
                // 因为必须保持全局索引连续递增，才能与页面输出的 id="page_X" 精确匹配
                buildTree(node.children, level + 1);

                // 遍历完子节点后闭合 ul
                html += `        </ul>\n`;
            }
            html += `    </li>\n`;
        });
    }

    buildTree(nodes, level);
    if (level === 0) html += "</ul>\n";
    return html;
}

function askUser(query) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise((r) => rl.question(query, (ans) => { rl.close(); r(ans); }));
}

// ==========================================
// 🚀 主程序
// ==========================================

(async () => {
    const scriptStartTime = Date.now(); // 🚀 记录整个脚本的开始时间
    let realFetchStartTime = 0;   // 第一次真实抓取的时间
    let realFetchCount = 0;       // 真实抓取计数

    console.clear();
    console.log("============================================================");
    console.log("🚀 NX文档抓取器 (V121 - 样式完全恢复版)");
    console.log("============================================================");

    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });

    const db = initDatabase();
    const dbCount = await getDatabaseStats(db);
    console.log(`📊 数据库当前记录数: ${dbCount}`);

    // 极简一行流，绝不撑爆屏幕
    const modeInput = await askUser("\n🚀 请选择运行模式: [a]全自动  [c]续传(跳过已存)  [r]重抓清空库 👉 (默认 c): ");
    const mode = modeInput.trim().toLowerCase() || "c";

    if (mode === "r") {
        console.log("🗑️ 清空数据库...");
        await new Promise(r => db.run("DELETE FROM cache", r));
        await new Promise(r => db.run("DELETE FROM styles", r));
        if (fs.existsSync(NAV_JSON_FILE)) fs.unlinkSync(NAV_JSON_FILE);
    }

    let browser;
    try {
        // 🚀 改为 Headless 模式启动，不再连接现有 Edge
        console.log("🚀 正在启动后台浏览器引擎...");
        browser = await chromium.launch({
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });
    } catch (e) {
        console.error("❌ 浏览器启动失败:", e.message);
        process.exit(1);
    }

    // 🛑 优雅退出处理
    const activeWorkers = []; // 追踪活跃的 worker 页面
    let isShuttingDown = false;

    async function gracefulShutdown() {
        if (isShuttingDown) return;
        isShuttingDown = true;
        console.log("\n\n🛑 收到中断信号，正在优雅退出...");

        // 1. 关闭所有 worker 页面
        console.log(`🔒 正在关闭 ${activeWorkers.length} 个并发标签页...`);
        await Promise.all(activeWorkers.map(page => page.close().catch(() => {})));

        // 2. 关闭浏览器连接 (重要！)
        if (browser) {
            console.log("🔒 正在关闭浏览器引擎...");
            await browser.close().catch(() => {});
        }

        // 3. 关闭数据库
        if (db) {
            db.close((err) => {
                if (err) console.error(err.message);
                console.log("🔒 数据库连接已关闭");
                process.exit(0);
            });
        } else {
            process.exit(0);
        }
    }

    process.on('SIGINT', gracefulShutdown);
    process.on('SIGTERM', gracefulShutdown);

    // 1. 获取目录结构
    let treeData = [];
    let navItems = [];

    if (fs.existsSync(NAV_JSON_FILE) && mode !== "a" && mode !== "r") {
        console.log("✅ 读取本地目录缓存...");
        treeData = JSON.parse(fs.readFileSync(NAV_JSON_FILE, "utf-8"));
    } else {
        console.log("📋 正在探测目录结构...");
        const page = await browser.newPage(); // 🚀 修复：直接使用 newPage()，它会自动创建上下文
        await page.goto(START_URL, { waitUntil: "domcontentloaded" });

        console.log("📋 正在自适应识别侧边栏并爆破展开所有节点...");
        treeData = await page.evaluate(async () => {
            // 1. 寻找最佳的根节点（防官方改版）
            function findBestNavRoot() {
                let root = document.querySelector("ul.doc-topics") || document.querySelector('[role="tree"]');
                if (root) return root;
                let allUls = Array.from(document.querySelectorAll('ul'));
                if (allUls.length === 0) return null;
                allUls.sort((a,b) => b.querySelectorAll('a').length - a.querySelectorAll('a').length);
                return allUls[0];
            }

            const treeRoot = findBestNavRoot();
            if (!treeRoot) return [];

            // 2. 暴力展开所有层级（带防卡死和懒加载触发）
            let lastCount = 0;
            let stuck = 0;
            while (stuck < 6) {
                // 覆盖西门子所有可能用到的展开按钮类名
                const expandables = Array.from(document.querySelectorAll("li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded), li[aria-expanded='false'] > button"));

                if (expandables.length === 0) {
                    stuck++;
                    await new Promise(r => setTimeout(r, 1000));
                    continue;
                }

                for (let el of expandables) {
                    try {
                        // 滚动到可见区域触发懒加载，然后再点击
                        el.scrollIntoView({block: 'center', inline: 'nearest'});
                        el.click();
                    } catch(e) {}
                }
                await new Promise(r => setTimeout(r, 2000));

                let currentCount = document.querySelectorAll("li").length;
                if (currentCount > lastCount) {
                    lastCount = currentCount;
                    stuck = 0; // 只要有新节点出现，重置防卡死计数
                } else {
                    stuck++;
                }
            }

            // 3. 递归解析目录树（兼容 Python 版的 hasChildren 和双链接）
            function parseLevel(ul) {
                const result = [];
                if (!ul) return result;
                const lis = ul.querySelectorAll(':scope > li');
                for (let li of lis) {
                    const a = li.querySelector(":scope > a, :scope > div > a, .toc-node-content a");
                    const sub = li.querySelector(":scope > ul") || li.querySelector(":scope > div > ul");

                    if (a) {
                        result.push({
                            text: a.innerText.trim(),
                            url: a.href,
                            href: a.href,
                            hasChildren: !!sub,
                            children: parseLevel(sub)
                        });
                    } else {
                        const titleSpan = li.querySelector(":scope > span, :scope > div > span");
                        if(titleSpan) {
                            result.push({
                                text: titleSpan.innerText.trim(),
                                url: "",
                                href: "",
                                hasChildren: !!sub,
                                children: parseLevel(sub)
                            });
                        }
                    }
                }
                return result;
            }

            const startUl = treeRoot.tagName === 'UL' ? treeRoot : treeRoot.querySelector('ul');
            return parseLevel(startUl);
        });

        fs.writeFileSync(NAV_JSON_FILE, JSON.stringify(treeData, null, 2));
        await page.close();
    }

    const flatten = (nodes) => nodes.forEach(n => { navItems.push(n); if(n.children) flatten(n.children); });
    flatten(treeData);
    console.log(`📊 目录节点总数: ${navItems.length}`);

    // 2. 并发抓取
    let currentIndex = 0;
    let successCount = 0;
    let skipCount = 0;
    let failCount = 0;

    // 辅助函数：生成进度日志
    function getLogPrefix(idx, isRealFetch = false) {
        const total = navItems.length;
        const percent = ((idx + 1) / total * 100).toFixed(1);

        let etaStr = "--";

        if (isRealFetch) {
            if (realFetchStartTime === 0) realFetchStartTime = Date.now();
            realFetchCount++;

            const elapsed = (Date.now() - realFetchStartTime) / 1000;
            const rate = realFetchCount / elapsed; // items per second

            const remaining = total - (idx + 1);
            const etaSec = rate > 0 ? remaining / rate : 0;

            const etaMin = Math.floor(etaSec / 60);
            const etaS = Math.floor(etaSec % 60);
            etaStr = `${etaMin}分${etaS}秒`;
        }

        return `[${percent}%] 成功:${successCount} 复用:${skipCount} 失败:${failCount} | ETA: ${etaStr}`;
    }

    async function worker(id) {
        let page = null;
        let context = null; // 🚀 显式管理上下文

        const createPage = async () => {
            if (page) {
                const idx = activeWorkers.indexOf(page);
                if (idx > -1) activeWorkers.splice(idx, 1);
                try { await page.close(); } catch {}
            }
            if (context) try { await context.close(); } catch {}

            // 🚀 修复：为每个 Worker 创建独立的 Context
            context = await browser.newContext();
            page = await context.newPage();

            activeWorkers.push(page); // 加入追踪

            await page.route("**/*", route => {
                const url = route.request().url().toLowerCase();
                if (["media", "beacon", "csp_report"].includes(route.request().resourceType()) ||
                    url.includes("analytics") || url.includes("tracking")) {
                    route.abort();
                } else {
                    route.continue();
                }
            });
        };

        await createPage();

        while (currentIndex < navItems.length && !isShuttingDown) {
            const idx = currentIndex++;
            const item = navItems[idx];

            if (!item || !item.url || item.url.includes("javascript") || item.url.trim() === "#") {
                console.log(`   ℹ️ [${idx+1}/${navItems.length}] [线程${id}] ${item.text} (📁 纯目录外壳，自动跳过)`);
                continue;
            }

            // 【传入 url 查重】
            const exists = await checkPageExists(db, item.url);
            if (exists) {
                skipCount++;
                console.log(`[${idx+1}/${navItems.length}] [线程${id}] ${item.text} (✓ 数据库极速恢复) | ${getLogPrefix(idx, false)}`);
                continue;
            }

            // console.log(`🚀 [${idx+1}/${navItems.length}] [线程${id}] 开始提取: ${item.text}`);

            let retries = 0;
            let success = false;
            while (retries < 3 && !success && !isShuttingDown) {
                try {
                    if (retries > 0) await new Promise(r => setTimeout(r, 2000));

                    if (page.isClosed()) await createPage();

                    await page.goto(item.url, { waitUntil: "domcontentloaded", timeout: 30000 });

                    const { frame, data } = await capturePageContent(page);
                    const css = frame ? await buildInlineCss(frame) : "";

                    if (data.html) {
                        // 添加表格签名处理（与Python版本保持一致）
                        const tableSigs = processTableSignatures(data.html);
                        // 【同时传入 url 和 title 保存】
                        await saveToDatabase(db, item.url, item.text, data.html, css);
                        successCount++;
                        success = true;
                        console.log(`[${idx+1}/${navItems.length}] [线程${id}] ${item.text} (✓ 抓取成功) | ${getLogPrefix(idx, true)}`);
                    } else {
                        throw new Error("HTML为空");
                    }
                } catch (e) {
                    retries++;
                    console.error(`   ❌ [${item.text}] [线程${id}] 重试 ${retries}/3: ${e.message}`);

                    await createPage();

                    if (retries >= 3) {
                        failCount++;
                        console.error(`   ❌ [${item.text}] [线程${id}] 最终失败`);
                    }
                }
            }
        }

        if (page) {
            const idx = activeWorkers.indexOf(page);
            if (idx > -1) activeWorkers.splice(idx, 1);
            try { await page.close(); } catch {}
        }
        if (context) try { await context.close(); } catch {}
    }

    console.log(`⚡ 启动 ${MAX_CONCURRENCY} 个并发线程...`);
    const workers = Array(MAX_CONCURRENCY).fill(null).map((_, i) => worker(i + 1));
    await Promise.all(workers);

    if (isShuttingDown) return;

    console.log(`\n✅ 抓取结束 | 新增: ${successCount} | 跳过: ${skipCount} | 失败: ${failCount}`);

    // 重置全局索引以匹配数据库顺序
    globalPageIndex = 0;

    // 3. 生成最终 HTML (直接读库)
    console.log("⏳ 正在生成最终 HTML...");
    const writeStream = fs.createWriteStream(FINAL_OUTPUT_FILE);

    writeStream.write(`<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>${SIDEBAR_TITLE}</title>
`);
    writeStream.write(`    <style>\n`);
    const allStyles = await new Promise(r => db.all("SELECT content FROM styles", (e, rows) => r(rows || [])));
    allStyles.forEach(row => writeStream.write(row.content + "\n"));

    // 满血版核心 CSS，单括号，完美层级与符号修复
    const uiCss = `
        body { display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; color: #333; }

        .nx-sidebar { width: 340px; min-width: 250px; display: flex; flex-direction: column; background: #f8f9fa; border-right: 1px solid #dee2e6; }

        .nx-sidebar-header { padding: 15px 10px; background: #f8f9fa; border-bottom: 2px solid #007cba; flex-shrink: 0; }
        .nx-sidebar-content { flex: 1; overflow-y: auto; padding: 5px; }

        .resizer { width: 5px; cursor: col-resize; background: #dee2e6; transition: background 0.2s; }
        .resizer:hover { background: #007cba; }
        .main-content { flex: 1; overflow-y: auto; scroll-behavior: smooth; padding: 0; background: #fff; line-height: 1.6; }
        .content-wrapper { max-width: 100%; margin: 0 auto; padding: 40px; }

        /* 优化点：统一正文主标题颜色 */
        .content-wrapper h1 { font-size: 32px !important; color: #007cba !important; font-weight: bold; margin-bottom: 15px; }
        .content-wrapper h2 { color: #007cba !important; }

        /* 修复点：彻底移除所有针对表格的强制样式，完全依赖页面自带 CSS */

        .nx-sidebar ul, .nx-sidebar ul.root-list { list-style: none; margin: 0; padding: 0; }
        .nx-sidebar li { margin: 2px 0; padding: 0; }

        .nav-item-row { display: flex; align-items: flex-start; margin: 2px 0; }

        ul.nested { 
            display: none; 
            padding-left: 0px !important;
            border-left: 1px solid #888 !important;
            margin-left: 6px !important; 
            margin-top: 2px;
            margin-bottom: 2px; 
        }
        ul.active { display: block; }

        .caret { cursor: pointer; display: inline-block; width: 14px; min-width: 14px; color: #666; font-size: 10px; margin-top: 6px; text-align: center; }
        .caret::before { content: "▶"; display: inline-block; transition: transform 0.2s; }
        .caret-down::before { transform: rotate(90deg); }
        .no-caret { display: inline-block; width: 14px; min-width: 14px; }

        .nx-sidebar a, .nav-text { 
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
        }

        .nav-level-0 > .nav-item-row > a, 
        .nav-level-0 > .nav-item-row > span.nav-text { font-size: 14px; font-weight: 700; padding-top: 5px; padding-bottom: 5px; }

        .nav-level-1 > .nav-item-row > a, 
        .nav-level-1 > .nav-item-row > span.nav-text { font-size: 13px; font-weight: 600; }

        .nav-level-2 > .nav-item-row > a, 
        .nav-level-2 > .nav-item-row > span.nav-text { font-size: 13px; }

        .nav-level-3 > .nav-item-row > a, .nav-level-3 > .nav-item-row > span.nav-text,
        .nav-level-4 > .nav-item-row > a, .nav-level-4 > .nav-item-row > span.nav-text,
        .nav-level-5 > .nav-item-row > a, .nav-level-5 > .nav-item-row > span.nav-text { font-size: 12px; }

        .nx-sidebar a:hover, .folder-text:hover { background: #e2e8f0; color: #005a84; }
        /* 优化点：调整选中目录项的背景色 */
        .selected-link { background: #ADD8E6 !important; color: #000 !important; font-weight: 600 !important; }

        .page-section { margin-bottom: 80px; padding-top: 30px; border-top: 2px solid #eaeaea; }
        .page-section:first-child { border-top: none; padding-top: 0; }

        /* --- REV 2026-02-19 #06: table readability (offline) --- */
        /* 1) no-class：尽量保持单元格不乱换行；同时允许横向滚动兜底。 */
        .main-content table:not([class]) td,
        .main-content table:not([class]) th {
            white-space: nowrap;
        }
        .main-content .content-wrapper:has(table:not([class])) {
            overflow-x: auto;
        }

        /* 2) navigator：同样避免换行导致的“表格竖排”。 */
        .main-content table.navigator td,
        .main-content table.navigator th {
            white-space: nowrap;
        }
        .main-content .content-wrapper:has(table.navigator) {
            overflow-x: auto;
        }

        /* 3) locator：常见于“位于何处?”这类两列小表。 */
        .main-content table.locator {
            table-layout: auto;
            width: max-content;
            max-width: 100%;
        }
        .main-content table.locator td,
        .main-content table.locator th {
            white-space: nowrap;
        }
        .main-content .content-wrapper:has(table.locator) {
            overflow-x: auto;
        }
    `;

    writeStream.write(`${uiCss}
    </style>
</head>
<body>
`);
    writeStream.write(`    <div class="nx-sidebar">
        <div class="nx-sidebar-header">
            <h3 style="color: #007cba; margin: 0; text-align: center; font-size: 24px;">${SIDEBAR_TITLE}</h3>
        </div>
        <div class="nx-sidebar-content">
`);
    writeStream.write(`${renderSidebarHtml(treeData)}        </div>\n    </div>\n`);
    writeStream.write(`    <div class="resizer" id="resizer"></div>\n`);
    writeStream.write(`    <div class="main-content">\n        <div class="content-wrapper">\n`);

    // 重置索引以匹配内容生成
    globalPageIndex = 0;
    let validPageCount = 0;

    for (const item of navItems) {
        if (!item.url || item.url.includes("javascript")) {
            globalPageIndex++; // 跳过的项目也要递增索引以保持一致性
            continue;
        }

        // 🚀 防撞补丁同步：按 url 查询数据库
        const row = await new Promise(r => db.get("SELECT html FROM cache WHERE url = ?", [item.url], (e, row) => r(row)));
        if (row && row.html) {
            const currentPageIndex = globalPageIndex++;
            // 更严格的HTML清理
            let cleanHtml = row.html
                .replace(/<\/body>/gi, '')
                .replace(/<\/html>/gi, '')
                .replace(/<body[^>]*>/gi, '')
                .replace(/<html[^>]*>/gi, '')
                .replace(/<head[^>]*>[\s\S]*?<\/head>/gi, '')
                .replace(/<script[^>]*>.*?<\/script>/gi, '')
                .replace(/<!DOCTYPE[^>]*>/gi, '')
                .trim();

            if (cleanHtml) {
                writeStream.write(`            <div class="page-section" id="page_${currentPageIndex}">${cleanHtml}</div>\n`);
                validPageCount++;
            }
        } else {
            // 如果查不到数据，也要递增索引，保持锚点对齐
            globalPageIndex++;
        }
    }

    const uiJs = `
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
</html>`;

    writeStream.end(async () => {
        console.log('✅ 文件主体写入完成');
        try {
            console.log('📝 开始追加结束标签与JS代码...');
            // 写入结束标签及 JS 代码
            fs.appendFileSync(FINAL_OUTPUT_FILE, `        </div>\n    </div>\n${uiJs}`, 'utf8');
            console.log('✅ 代码追加完成');
        } catch (appendError) {
            console.error('❌ 追加代码失败:', appendError.message);
        }

        console.log(`✅ 成功写入 ${validPageCount} 个页面内容`);
        console.log(`🎉 文件已生成: ${FINAL_OUTPUT_FILE}`);
        
        // 🚀 修复：计算整个脚本的总耗时
        const endTime = Date.now();
        const totalElapsedMs = endTime - scriptStartTime;  // 使用脚本开始时间
        const totalElapsedSec = Math.floor(totalElapsedMs / 1000);
        const mins = Math.floor(totalElapsedSec / 60);
        const secs = totalElapsedSec % 60;
        console.log(`⏱️ 总耗时: ${mins}分 ${secs}秒`);
        
        db.close();
        process.exit();
    });

})();