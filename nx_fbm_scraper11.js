// ==========================================================
// NX2506 文档抓取器 (V105 - 统一正文标题颜色)
// 进化：
// 1. [真并发] 引入 Promise 任务队列，瞬间唤醒 5 个浏览器标签页同时工作！
// 2. [目录秒读] 自动保存目录为 JSON，续传模式下 0.1 秒极速跳过主页探测。
// 3. [自适应探测] 自动寻找最像目录的 UL 节点，不再硬编码 ul.doc-topics。
// 4. [假节点秒过] 自动识别 javascript:void(0) 的空壳目录。
// 5. [尾部切除] 精确移除底部关联链接块 (全词库核弹版)。
// 6. [极限断流] 底层拦截 font、websocket 等多余请求。
// 7. [防爆写入] fs.createWriteStream 流式合成，突破 V8 内存上限。
// 8. [样式持久化] CSS 独立缓存，修复续传模式下样式丢失导致的排版错乱。
// 9. [智能续传] 增加文件大小校验，自动识别并重抓 0KB 或损坏的“假成功”文件。
// 10. [默认展开] 默认展开首层目录，提升阅读体验。
// 11. [优化UI] 调整选中目录项的背景色，提高文字可读性。
// 12. [优化UI] 将正文主标题颜色设为主题蓝，与侧边栏标题呼应。
// ==========================================================

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");
const { chromium } = require("playwright");

// ==========================================
// ⚙️ 全局配置区 (Global Configuration)
// ==========================================
// const START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/feat_based_mach_fbm_overview";
const START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20241101461013487.mfgholemaking/feat_based_mach_fbm_overview";

const FINAL_OUTPUT_FILE = "NX2506基于特征加工-js.html";  // 最终生成的单文件 HTML 名称
const CACHE_DIR_NAME = "NX2506_pages";               // 本地缓存文件夹名称
const SIDEBAR_TITLE = "NX2506&nbsp;&nbsp;基于特征加工"; // 侧边栏大标题
const MAX_CONCURRENCY = 5;                           // 🚀 真并发线程数量 (极速设5，会瞬间弹5个标签页)
const NAV_JSON_FILE = "NX2506_nav_structure.json";   // 目录结构 JSON 文件名
const MIN_FILE_SIZE = 500;                         // ⚠️ 最小文件阈值 (字节)，小于此值视为下载失败，自动重抓

// --- 内部变量 ---
const CACHE_DIR = path.join(__dirname, CACHE_DIR_NAME);
const TARGET_IFRAME_SELECTOR = "#xhtml";
// ==========================================

const md5 = (s) => crypto.createHash("md5").update(s).digest("hex");

function askUser(query) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise((r) => rl.question(query, (ans) => { rl.close(); r(ans); }));
}

function cssTextToAbsoluteUrls(cssText, baseUrl) {
    return String(cssText).replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (m, quote, url) => {
        const raw = String(url).trim();
        if (!raw || raw.startsWith("data:") || raw.startsWith("blob:") || raw.startsWith("#")) return m;
        if (/^https?:\/\//i.test(raw)) return `url(${quote}${raw}${quote})`;
        try { return `url(${quote}${new URL(raw, baseUrl).href}${quote})`; } catch { return m; }
    });
}

async function fetchTextFromFrame(frame, href) {
    return await frame.evaluate(async (href) => {
        try {
            const resp = await fetch(href, { credentials: "include", cache: "force-cache" });
            if (!resp.ok) return ""; return await resp.text();
        } catch { return ""; }
    }, href);
}

async function buildInlineCss(frame) {
    const meta = await frame.evaluate(() => {
        const baseUrl = document.baseURI;
        const inline = Array.from(document.querySelectorAll("style")).map((s) => s.textContent || "").join("\n\n/* ---- style ---- */\n\n");
        const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map((l) => l.href).filter(Boolean);
        return { baseUrl, inline, links };
    });

    let external = "";
    for (const href of meta.links) {
        const css = await fetchTextFromFrame(frame, href);
        if (css && css.trim()) external += `

/* ---- external css: ${href} ---- */

${css}
`;
    }

    const combined = `${meta.inline}\n\n${external}`;
    if (!combined.trim()) return "";
    return cssTextToAbsoluteUrls(combined, meta.baseUrl);
}

async function capturePageContent(page) {
    const iframeElement = await page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 20000 });
    const frame = await iframeElement.contentFrame();
    if (!frame) throw new Error("TARGET_IFRAME contentFrame() is null");

    await frame.evaluate(async () => {
        const scroller = document.scrollingElement || document.body;
        const step = window.innerHeight; let pos = 0;
        while (pos < scroller.scrollHeight) { pos += step; window.scrollTo(0, pos); await new Promise((r) => setTimeout(r, 100)); }
        window.scrollTo(0, 0);
    });

    const data = await frame.evaluate(() => {
        const docContent = document.querySelector(".doc-content") || document.body;
        const mainContainer = document.querySelector(".main.content-container") || document.querySelector(".doc-frame") || docContent;
        if (!mainContainer || mainContainer.innerText.trim().length < 50) return null;

        const clone = mainContainer.cloneNode(true);
        // 暴力切除 Siemens 官方所有可能泄露出来的顶栏和导航
        const junk = ['header', 'nav', '.sw-header', '.global-header', '.aw-layout-header', '#header'];
        junk.forEach(s => clone.querySelectorAll(s).forEach(el => el.remove()));

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
                if (wrapper && wrapper !== clone && (wrapper.textContent || '').length < 2000) wrapper.remove();
                else {
                    let next = el.nextElementSibling;
                    while (next) { let tmp = next; next = next.nextElementSibling; tmp.remove(); }
                    el.remove();
                }
            }
        });

        const baseUrl = document.baseURI;
        clone.querySelectorAll("img").forEach((img) => {
            const src = img.getAttribute("data-src") || img.getAttribute("src");
            if (src) img.setAttribute("src", new URL(src, baseUrl).href);
        });

        clone.querySelectorAll("h3, h4, strong").forEach((el) => {
            if (el && el.textContent && el.textContent.trim() === "Background commands") {
                const h2 = document.createElement("h2"); h2.textContent = el.textContent.trim(); el.replaceWith(h2);
            }
        });

        return { html: clone.innerHTML, docClass: docContent.className, mainClass: mainContainer.className, baseUrl, textLen: mainContainer.innerText.trim().length };
    });

    return { frame, data };
}

function renderSidebarHtml(nodes, level = 0) {
    if (!nodes || nodes.length === 0) return "";
    let html = level === 0 ? '<ul class="root-list active">' : '<ul class="nested">';
    nodes.forEach((node) => {
        const hash = md5((node.url || "") + node.text);
        html += `    <li class="nav-level-${level}">\n        <div class="nav-item-row">\n`;
        if (node.hasChildren && node.children && node.children.length > 0) {
            // 优化点：首层目录的箭头默认向下
            const caretClass = level === 0 ? "caret caret-down" : "caret";
            html += `            <span class="${caretClass}" onclick="toggleNode(this)"></span>\n`;
            if (!node.url || node.url.includes("javascript:void(0)")) {
                html += `            <span class="folder-text" onclick="toggleNode(this.previousElementSibling)">${node.text}</span>\n`;
            } else {
                html += `            <a href="#${hash}" onclick="handleManualClick(this)">${node.text}</a>\n`;
            }
        } else {
            html += `            <span class="no-caret"></span>\n`;
            if (node.url && !node.url.includes("javascript:void(0)")) {
                html += `            <a href="#${hash}" onclick="handleManualClick(this)">${node.text}</a>\n`;
            } else {
                html += `            <span class="nav-text">${node.text}</span>\n`;
            }
        }
        html += `        </div>\n`;

        // 递归渲染子节点
        if (node.hasChildren && node.children) {
            let subHtml = renderSidebarHtml(node.children, level + 1);
            // 优化点：首层目录的子菜单默认展开
            if (level === 0) {
                subHtml = subHtml.replace('<ul class="nested">', '<ul class="nested active">', 1);
            }
            html += subHtml;
        }
        html += `    </li>\n`;
    });
    return html + "</ul>\n";
}

(async () => {
    const startTime = Date.now();
    console.clear();
    console.log("============================================================");
    console.log("🚀 [启动指引] V105 统一正文标题颜色");
    console.log("1. taskkill /f /im msedge.exe");
    console.log("2. msedge.exe --remote-debugging-port=9222");
    console.log("⚠️ 注意：执行期间浏览器会劈开多个标签页疯狂跳动，请勿操作鼠标！");
    console.log("============================================================\n");

    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const modeInput = await askUser("[1] 续传 (自动修复失败页面)\n[2] 彻底重抓\n请选择模式 [1/2] (默认1，直接回车): ");
    const mode = modeInput.trim() || "1";

    if (mode === "2") {
        console.log("🗑️ 正在清洗缓存，准备从零重抓...");
        if (fs.existsSync(CACHE_DIR)) {
            fs.readdirSync(CACHE_DIR).forEach((f) => fs.unlinkSync(path.join(CACHE_DIR, f)));
        }
        if (fs.existsSync(NAV_JSON_FILE)) fs.unlinkSync(NAV_JSON_FILE); // 👈 清理旧的目录缓存
    }

    let browser;
    try {
        browser = await chromium.connectOverCDP("http://localhost:9222");
    } catch (e) {
        console.error("❌ 无法连上 9222 端口，请确保 Edge 以远程调试模式启动。");
        process.exit();
    }

    let treeData = [];
    let navItems = [];

    // 🌟 核心升级：读取缓存目录，秒过探测阶段！
    if (mode === "1" && fs.existsSync(NAV_JSON_FILE)) {
        console.log(`✅ 检测到目录树缓存文件: ${NAV_JSON_FILE}`);
        console.log("⚡ 启动极速秒读模式，跳过主页探测！");
        try {
            treeData = JSON.parse(fs.readFileSync(NAV_JSON_FILE, "utf-8"));
            const flatten = (nodes) => nodes.forEach((n) => { navItems.push(n); flatten(n.children); });
            flatten(treeData);
            console.log(`📊 共加载 ${navItems.length} 个页面节点，准备【${MAX_CONCURRENCY} 并发】抓取...`);
        } catch(e) {
            console.error("❌ 目录 JSON 读取失败，请删掉该文件或选择 [2] 重抓。");
            process.exit();
        }
    } else {
        // 如果是重抓或没有 JSON 文件，则老老实实打开探测页
        const mainPage = await browser.contexts()[0].newPage();
        await mainPage.route("**/*", (route) => {
            const blockTypes = ["media", "beacon", "font", "websocket", "csp_report"];
            if (blockTypes.includes(route.request().resourceType())) route.abort(); else route.continue();
        });
        await mainPage.setViewportSize({ width: 1280, height: 960 });

        try {
            await mainPage.goto(START_URL, { waitUntil: "domcontentloaded" });
            console.log("📋 正在自适应深度爆破目录树 (由于是首次或选了重抓)...");

            treeData = await mainPage.evaluate(async () => {
                function findBestNavRoot() {
                    let root = document.querySelector("ul.doc-topics") || document.querySelector('[role="tree"]');
                    if (root) return root;
                    let allUls = Array.from(document.querySelectorAll('ul'));
                    if (allUls.length === 0) return null;
                    allUls.sort((a,b) => b.querySelectorAll('a').length - a.querySelectorAll('a').length);
                    return allUls[0];
                }

                const treeRoot = findBestNavRoot();
                if (!treeRoot) throw new Error("找不到侧边栏目录容器");

                let lastCount = 0; let stuck = 0;
                while(stuck < 6) {
                    const btns = Array.from(document.querySelectorAll("li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded)"));
                    if (btns.length === 0) { stuck++; await new Promise(r => setTimeout(r, 1000)); continue; }
                    btns.forEach(b => { try{ b.scrollIntoView({block: 'center'}); b.click(); }catch(e){} });
                    await new Promise(r => setTimeout(r, 1500));
                    let cur = document.querySelectorAll("li").length;
                    if(cur > lastCount) { lastCount = cur; stuck = 0; } else { stuck++; }
                }

                function getNodes(ul) {
                    const res = [];
                    if (!ul) return res;
                    ul.querySelectorAll(":scope > li").forEach((li) => {
                        const a = li.querySelector(":scope > a, :scope > div > a, .toc-node-content a");
                        const sub = li.querySelector(":scope > ul") || li.querySelector(":scope > div > ul");
                        if (a) res.push({ text: a.innerText.trim(), url: a.href, hasChildren: !!sub, children: getNodes(sub) });
                        else {
                            const span = li.querySelector(":scope > span, :scope > div > span");
                            if(span) res.push({ text: span.innerText.trim(), url: "", hasChildren: !!sub, children: getNodes(sub) });
                        }
                    });
                    return res;
                }
                const rootUl = treeRoot.tagName === 'UL' ? treeRoot : treeRoot.querySelector('ul');
                return getNodes(rootUl);
            });

            // 💾 爆破成功后立刻保存为 JSON！
            fs.writeFileSync(NAV_JSON_FILE, JSON.stringify(treeData, null, 2), "utf-8");
            console.log(`✅ 目录树探测完毕，已存入缓存: ${NAV_JSON_FILE}`);

            const flatten = (nodes) => nodes.forEach((n) => { navItems.push(n); flatten(n.children); });
            flatten(treeData);

            console.log(`📊 共发现 ${navItems.length} 个页面节点，准备【${MAX_CONCURRENCY} 并发】抓取...`);
        } catch (e) {
            console.error("❌ 目录探测失败，请检查网址或网络环境。");
            process.exit();
        } finally {
            await mainPage.close();
        }
    }


    // ==============================================
    // 🚀 真·并发任务调度核心 (Promise Worker Pool)
    // ==============================================
    let currentIndex = 0;

    async function processTaskQueue(workerId) {
        const workerPage = await browser.contexts()[0].newPage();

        await workerPage.route("**/*", (route) => {
            const blockTypes = ["media", "beacon", "font", "websocket", "csp_report"];
            if (blockTypes.includes(route.request().resourceType())) route.abort();
            else route.continue();
        });
        await workerPage.setViewportSize({ width: 1280, height: 960 });

        while (currentIndex < navItems.length) {
            const taskIndex = currentIndex++;
            if (taskIndex >= navItems.length) break;

            const { text, url } = navItems[taskIndex];

            if (!url || url.includes("javascript:void(0)")) {
                console.log(`ℹ️ [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 跳过空目录: ${text}`);
                continue;
            }

            const filePath = path.join(CACHE_DIR, `${String(taskIndex).padStart(3, "0")}.html`);

            // ⚠️ 智能续传检查：
            // 1. 文件必须存在
            // 2. 文件大小必须 > MIN_FILE_SIZE (防止空文件或报错文件)
            if (fs.existsSync(filePath)) {
                const stats = fs.statSync(filePath);
                if (stats.size > MIN_FILE_SIZE) {
                    // console.log(`⏩ [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 已存在且完整，跳过: ${text}`);
                    continue;
                } else {
                    console.log(`⚠️ [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 文件损坏或为空 (${stats.size}B)，准备重抓: ${text}`);
                }
            }

            try {
                await workerPage.goto(url, { waitUntil: "domcontentloaded", timeout: 40000 });
                const { frame, data } = await capturePageContent(workerPage);

                if (!data || !data.html) {
                    console.error(`❌ [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 内容提取为空: ${text}`);
                    continue;
                }

                // 🎨 核心修复：将 CSS 独立保存为文件，确保续传时能读取到
                const rawCss = await buildInlineCss(frame);
                if (rawCss) {
                    const cssHash = md5(rawCss);
                    const cssPath = path.join(CACHE_DIR, `style_${cssHash}.css`);
                    if (!fs.existsSync(cssPath)) {
                        fs.writeFileSync(cssPath, rawCss);
                    }
                }

                const wrappedHtml = `<div class="${data.docClass}"><div id="${md5((url || "") + text)}" class="${data.mainClass}">${data.html}</div></div><hr/>`;
                fs.writeFileSync(filePath, wrappedHtml);
                console.log(`🎯 [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 快照: ${text} (${data.textLen}字)`);
            } catch (e) {
                console.error(`❌ [${taskIndex + 1}/${navItems.length}] [线程${workerId}] 抓取异常: ${text}`);
            }
        }

        await workerPage.close();
    }

    console.log(`⚡ 瞬间开启 ${MAX_CONCURRENCY} 个标签页并发流水线，请坐和放宽...`);
    const workers = [];
    for (let i = 1; i <= MAX_CONCURRENCY; i++) {
        workers.push(processTaskQueue(i));
    }

    await Promise.all(workers);
    console.log("✅ 所有并发线程收工，页面抓取完成！");


    // ==============================================
    // 🛡️ V8 流式防爆合成 (流式写入)
    // ==============================================
    console.log("⏳ 正在合成并流式写入最终 HTML (防御 V8 内存溢出)...");

    // 🎨 核心修复：从缓存目录读取所有 CSS 文件
    let globalCssContent = "";
    const allFiles = fs.readdirSync(CACHE_DIR);
    const cssFiles = allFiles.filter(f => f.startsWith("style_") && f.endsWith(".css"));
    cssFiles.forEach(f => {
        globalCssContent += fs.readFileSync(path.join(CACHE_DIR, f), "utf-8") + "\n";
    });
    console.log(`🎨 已合并 ${cssFiles.length} 个样式文件。`);

    const offlineFixCss = ``;
    const uiCss = `<style>
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

        .nx-sidebar ul, .nx-sidebar ul.root-list { list-style: none; margin: 0; padding: 0; }
        .nx-sidebar li { margin: 2px 0; padding: 0; }

        .nav-item-row { display: flex; align-items: flex-start; margin: 2px 0; }

        ul.nested { 
            display: none; 
            padding-left: 0px; 
            border-left: 1px solid #888; 
            margin-left: 6px; 
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

        /* --- REV 2026-02-18 #02: table readability (offline) --- */
        /* 只针对 Siemens 文档里的 locator 表格（常见于"位于何处?"这类两列小表）。 */
        .main-content table.locator {
            table-layout: auto;
            width: max-content;           /* allow table to be as wide as needed */
            max-width: 100%;
        }
        .main-content table.locator td,
        .main-content table.locator th {
            white-space: nowrap;          /* prevent tight tables from wrapping into multiple lines */
        }
        /* 仅当页面存在 locator 表格时，横向滚动兜底（让表格自己滚，不挤压内容）。 */
        .main-content .content-wrapper:has(table.locator) {
            overflow-x: auto;
        }
    </style>`;

    const uiJs = `<script>
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
            // 🚀 这里去掉了对旧版 ASIDE 的依赖，改用 BODY 兜底
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

        window.onload = function() {
            const r = document.getElementById('resizer'); 
            const s = document.querySelector('.nx-sidebar'); 
            let m = false;
            r.addEventListener('mousedown', () => m = true);
            document.addEventListener('mousemove', (e) => { 
                if (m && e.clientX > 200 && e.clientX < 800) s.style.width = e.clientX + 'px'; 
            });
            document.addEventListener('mouseup', () => m = false);
        }
    </script>`;

    const writeStream = fs.createWriteStream(FINAL_OUTPUT_FILE, { encoding: "utf-8" });

    writeStream.write(`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
    <style>\n${globalCssContent}\n</style>
    ${uiCss}
    ${offlineFixCss}
    </head><body>
    <div class="nx-sidebar">
        <div class="nx-sidebar-header">
            <h3 style="color: #007cba; margin: 0; text-align: center; font-size: 24px;">${SIDEBAR_TITLE}</h3>
        </div>
        <div class="nx-sidebar-content">${renderSidebarHtml(treeData)}</div>
    </div>
    <div class="resizer" id="resizer"></div>
    <div class="main-content">
        <div class="content-wrapper">\n`);

    const cacheFiles = fs.readdirSync(CACHE_DIR).sort();
    for (const f of cacheFiles) {
        if (f.endsWith('.html')) {
            writeStream.write(fs.readFileSync(path.join(CACHE_DIR, f), "utf-8") + "\n");
        }
    }

    writeStream.write(`</div></div>\n${uiJs}\n</body></html>`);
    writeStream.end();

    await new Promise((resolve) => writeStream.on("finish", resolve));

    const endTime = Date.now();
    const elapsedSec = Math.floor((endTime - startTime) / 1000);
    const mins = Math.floor(elapsedSec / 60);
    const secs = elapsedSec % 60;

    console.log(`🎉 任务完成！输出文件: ${FINAL_OUTPUT_FILE}`);
    console.log(`⏱️ 总耗时: ${mins}分 ${secs}秒`);

    process.exit();
})();