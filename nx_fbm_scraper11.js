// ==========================================================
// NX12 文档抓取器 (V88 - 终极自适应防爆断流版)
// 进化：
// 1. [自适应探测] 自动寻找最像目录的 UL 节点，不再硬编码 ul.doc-topics。
// 2. [假节点秒过] 自动识别 javascript:void(0) 的空壳目录，防止引擎卡死重试。
// 3. [尾部切除] 精确移除底部 Learn more/How do I 关联链接块。
// 4. [极限断流] 底层拦截 font、websocket 等多余请求，压榨网络加载性能。
// 5. [防爆写入] 引入 fs.createWriteStream，突破 V8 内存上限，支持无限页合并。
// ==========================================================

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");
const { chromium } = require("playwright");

// ==========================================
// ⚙️ 全局配置区 (Global Configuration)
// ==========================================
const START_URL = "https://docs.sw.siemens.com/zh-CN/doc/209349590/PL20190529153536917.mfgholemaking/feat_based_mach_fbm_overview";
const FINAL_OUTPUT_FILE = "NX12基于特征加工.html";  // 最终生成的单文件 HTML 名称
const CACHE_DIR_NAME = "NX12_pages";               // 本地缓存文件夹名称
const SIDEBAR_TITLE = "NX12&nbsp;&nbsp;基于特征加工"; // 侧边栏大标题
const MAX_CONCURRENCY = 5;                           // 并发数量 (注:当前JS版为极稳顺序抓取,暂作预留)
const NAV_JSON_FILE = "NX12_nav_structure.json";   // 目录结构 JSON 文件名

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
        try {
            return `url(${quote}${new URL(raw, baseUrl).href}${quote})`;
        } catch {
            return m;
        }
    });
}

async function fetchTextFromFrame(frame, href) {
    return await frame.evaluate(async (href) => {
        try {
            const resp = await fetch(href, { credentials: "include", cache: "force-cache" });
            if (!resp.ok) return "";
            return await resp.text();
        } catch {
            return "";
        }
    }, href);
}

async function buildInlineCss(frame) {
    const meta = await frame.evaluate(() => {
        const baseUrl = document.baseURI;
        const inline = Array.from(document.querySelectorAll("style"))
            .map((s) => s.textContent || "")
            .join("\n\n/* ---- style ---- */\n\n");
        const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
            .map((l) => l.href)
            .filter(Boolean);
        return { baseUrl, inline, links };
    });

    let external = "";
    for (const href of meta.links) {
        const css = await fetchTextFromFrame(frame, href);
        if (css && css.trim()) external += `\n\n/* ---- external css: ${href} ---- */\n\n${css}\n`;
    }

    const combined = `${meta.inline}\n\n${external}`;
    if (!combined.trim()) return "";
    return `<style>\n${cssTextToAbsoluteUrls(combined, meta.baseUrl)}\n</style>`;
}

async function capturePageContent(page) {
    const iframeElement = await page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 20000 });
    const frame = await iframeElement.contentFrame();
    if (!frame) throw new Error("TARGET_IFRAME contentFrame() is null");

    await frame.evaluate(async () => {
        const scroller = document.scrollingElement || document.body;
        const step = window.innerHeight;
        let pos = 0;
        while (pos < scroller.scrollHeight) {
            pos += step;
            window.scrollTo(0, pos);
            await new Promise((r) => setTimeout(r, 100));
        }
        window.scrollTo(0, 0);
    });

    const data = await frame.evaluate(() => {
        const docContent = document.querySelector(".doc-content") || document.body;
        const mainContainer =
            document.querySelector(".main.content-container") ||
            document.querySelector(".doc-frame") ||
            docContent;

        if (!mainContainer || mainContainer.innerText.trim().length < 50) return null;

        const clone = mainContainer.cloneNode(true);
        const trash = [".hidden-md-up.m-t", "#topic-navigator", "#feedback-btns", ".search-container", ".card-loader-overlay"];
        trash.forEach((sel) => clone.querySelectorAll(sel).forEach((el) => el.remove()));

        // 👇👇👇 终极无死角垃圾清理 (中英双语全词库核弹版) 👇👇👇
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
        // 👆👆👆 终极清理结束 👆👆👆

        const baseUrl = document.baseURI;
        clone.querySelectorAll("img").forEach((img) => {
            const src = img.getAttribute("data-src") || img.getAttribute("src");
            if (src) img.setAttribute("src", new URL(src, baseUrl).href);
        });

        clone.querySelectorAll("h3, h4, strong").forEach((el) => {
            if (el && el.textContent && el.textContent.trim() === "Background commands") {
                const h2 = document.createElement("h2");
                h2.textContent = el.textContent.trim();
                el.replaceWith(h2);
            }
        });

        return {
            html: clone.innerHTML,
            docClass: docContent.className,
            mainClass: mainContainer.className,
            baseUrl,
            textLen: mainContainer.innerText.trim().length,
        };
    });

    return { frame, data };
}

function renderSidebarHtml(nodes, isRoot = true) {
    if (!nodes || nodes.length === 0) return "";
    let html = isRoot ? '<ul class="root-list">' : '<ul class="nested active">';
    nodes.forEach((node) => {
        const hash = md5((node.url || "") + node.text);
        html += `<li>`;
        if (node.hasChildren) {
            html += `<span class="caret caret-down" onclick="toggleNode(this)"></span>`;
            if (!node.url || node.url.includes("javascript:void(0)")) {
                html += `<span class="folder-text" onclick="toggleNode(this.previousElementSibling)">${node.text}</span>`;
            } else {
                html += `<a href="#${hash}" onclick="handleManualClick(this)">${node.text}</a>`;
            }
            html += renderSidebarHtml(node.children, false);
        } else {
            html += `<span class="no-caret"></span><a href="#${hash}" onclick="handleManualClick(this)">${node.text}</a>`;
        }
        html += `</li>`;
    });
    return html + "</ul>";
}

(async () => {
    const startTime = Date.now();
    console.clear();
    console.log("============================================================");
    console.log("🚀 [启动指引]");
    console.log("1. taskkill /f /im msedge.exe");
    console.log("2. msedge.exe --remote-debugging-port=9222");
    console.log("============================================================\n");

    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const modeInput = await askUser("[1] 续传\n[2] 彻底重抓\n请选择模式 [1/2] (默认1，直接回车): ");
    const mode = modeInput.trim() || "1";

    if (mode === "2") {
        console.log("🗑️ 正在清洗缓存，准备从零重抓...");
        fs.readdirSync(CACHE_DIR).forEach((f) => fs.unlinkSync(path.join(CACHE_DIR, f)));
    }

    let browser;
    try {
        browser = await chromium.connectOverCDP("http://localhost:9222");
    } catch (e) {
        console.error("❌ 无法连上 9222 端口。");
        process.exit();
    }

    const page = await browser.contexts()[0].newPage();

    // 🚀 核心升级 1：极限网络断流拦截，屏蔽多余请求
    await page.route("**/*", (route) => {
        const blockTypes = ["media", "beacon", "font", "websocket", "csp_report"];
        if (blockTypes.includes(route.request().resourceType())) {
            route.abort();
        } else {
            route.continue();
        }
    });

    await page.setViewportSize({ width: 1280, height: 960 });

    try {
        await page.goto(START_URL, { waitUntil: "domcontentloaded" });

        console.log("📋 正在自适应深度爆破目录树...");

        const treeData = await page.evaluate(async () => {
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

            let lastCount = 0;
            let stuck = 0;
            while(stuck < 6) {
                const btns = Array.from(document.querySelectorAll("li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded)"));
                if (btns.length === 0) {
                    stuck++;
                    await new Promise(r => setTimeout(r, 1000));
                    continue;
                }
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
                    if (a) {
                        res.push({ text: a.innerText.trim(), url: a.href, hasChildren: !!sub, children: getNodes(sub) });
                    } else {
                        const span = li.querySelector(":scope > span, :scope > div > span");
                        if(span) res.push({ text: span.innerText.trim(), url: "", hasChildren: !!sub, children: getNodes(sub) });
                    }
                });
                return res;
            }
            const rootUl = treeRoot.tagName === 'UL' ? treeRoot : treeRoot.querySelector('ul');
            return getNodes(rootUl);
        });

        const navItems = [];
        const flatten = (nodes) => nodes.forEach((n) => { navItems.push(n); flatten(n.children); });
        flatten(treeData);

        console.log(`📊 共发现 ${navItems.length} 个页面节点，准备抓取...`);

        let inlineCssTag = "";
        let inlineCssCaptured = false;

        for (let i = 0; i < navItems.length; i++) {
            const { text, url } = navItems[i];

            if (!url || url.includes("javascript:void(0)")) {
                console.log(`ℹ️ [${i + 1}/${navItems.length}] 跳过空目录外壳: ${text}`);
                continue;
            }

            const filePath = path.join(CACHE_DIR, `${String(i).padStart(3, "0")}.html`);
            if (fs.existsSync(filePath)) {
                // 读取缓存时，如果是第一页且还没捕获 CSS，顺手把 CSS 处理了（保证续传模式有样式）
                if (!inlineCssCaptured && i === 0) {
                    try {
                         await page.goto(url, { waitUntil: "domcontentloaded", timeout: 40000 });
                         const iframeElement = await page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 20000 });
                         const frame = await iframeElement.contentFrame();
                         inlineCssTag = await buildInlineCss(frame);
                         inlineCssCaptured = true;
                    } catch(e) {}
                }
                continue;
            }

            try {
                await page.goto(url, { waitUntil: "domcontentloaded", timeout: 40000 });
                await page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 20000 });

                const { frame, data } = await capturePageContent(page);
                if (!data || !data.html) {
                    console.error(`❌ [${i + 1}/${navItems.length}] 内容提取为空: ${text}`);
                    continue;
                }

                if (!inlineCssCaptured) {
                    inlineCssTag = await buildInlineCss(frame);
                    inlineCssCaptured = true;
                }

                const wrappedHtml = `<div class="${data.docClass}"><div id="${md5((url || "") + text)}" class="${data.mainClass}">${data.html}</div></div><hr/>`;
                fs.writeFileSync(filePath, wrappedHtml);
                console.log(`🎯 [${i + 1}/${navItems.length}] 快照: ${text} (${data.textLen}字)`);
            } catch (e) {
                console.error(`❌ [${i + 1}/${navItems.length}] 引擎异常: ${text}`);
                console.error(e && e.stack ? e.stack : e);
            }
        }

        console.log("⏳ 正在合成并流式写入最终 HTML (防御 V8 内存溢出)...");

        const offlineFixCss = ``;
        const uiCss = `<style>
            body { display: flex; height: 100vh; margin: 0; overflow: hidden; font-family: sans-serif; }
            aside { width: 360px; min-width: 200px; max-width: 800px; overflow-y: auto; padding: 15px 4px; background: #f9f9f9; flex-shrink: 0; border-right: 1px solid #ddd; position: relative; user-select: none; }
            #resizer { width: 6px; cursor: col-resize; background: #eee; height: 100%; flex-shrink: 0; transition: background 0.1s; }
            #resizer:hover { background: #005f87; }
            
            aside ul { list-style: none; margin: 0; padding: 0; }
            aside li { margin: 2px 0; padding: 0; }
            
            ul.nested { 
                display: none; 
                padding-left: 4px;          
                border-left: 1px solid #888; 
                margin-left: 7px;            
                margin-top: 2px;
                margin-bottom: 2px;
            }
            ul.active { display: block; }
            
            .caret { cursor: pointer; display: inline-block; width: 15px; text-align: center; }
            .caret::before { content: "▶"; font-size: 10px; color: #666; }
            .caret-down::before { content: "▼"; }
            .no-caret { display: inline-block; width: 15px; } 
            
            a, .folder-text { text-decoration: none; color: #005f87; font-size: 14px; padding: 3px 2px; display: inline-block; width: calc(100% - 35px); vertical-align: top; border-radius: 4px; cursor:pointer;}
            a:hover, .folder-text:hover { background: #e2e8f0; color: #005a84; }
            .selected-link { font-weight: bold !important; background: #005a84 !important; color: #fff !important; }
            main { flex: 1; overflow-y: auto; background: #fff; scroll-behavior: smooth; }
            main > .nx12-content { padding-left: 16px; padding-right: 16px; }
        </style>`;

        const uiJs = `<script>
            function toggleNode(s) { var u = s.parentElement.querySelector(".nested"); if (u) { u.classList.toggle("active"); s.classList.toggle("caret-down"); } }
            function handleManualClick(a) {
                document.querySelectorAll(".selected-link").forEach(l => l.classList.remove("selected-link"));
                a.classList.add("selected-link");
                var nextUl = a.nextElementSibling;
                if (nextUl && nextUl.classList.contains('nested')) { nextUl.classList.add("active"); if(a.previousElementSibling) a.previousElementSibling.classList.add("caret-down"); }
                var p = a.parentElement;
                while (p && p.tagName !== 'ASIDE') {
                    if (p.tagName === 'UL') {
                        p.classList.add('active');
                        var c = p.parentElement.querySelector(".caret");
                        if (c) c.classList.add("caret-down");
                    }
                    p = p.parentElement;
                }
            }
            window.onload = function() {
                const r = document.getElementById('resizer'); const s = document.querySelector('aside'); let m = false;
                r.addEventListener('mousedown', () => m = true);
                document.addEventListener('mousemove', (e) => { if (m && e.clientX > 200 && e.clientX < 800) s.style.width = e.clientX + 'px'; });
                document.addEventListener('mouseup', () => m = false);
            }
        </script>`;

        // 🚀 核心升级 2：V8 流式防爆写入
        const writeStream = fs.createWriteStream(FINAL_OUTPUT_FILE, { encoding: "utf-8" });

        // 写入 Head 头部和侧边栏
        writeStream.write(`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
        ${inlineCssTag}
        ${uiCss}
        ${offlineFixCss}
        </head><body>
        <aside><h3 style="color:#007cba; border-bottom:2px solid #007cba; padding-bottom:10px; margin:0 10px 15px 10px; text-align:center; font-size: 24px;">${SIDEBAR_TITLE}</h3>${renderSidebarHtml(treeData)}</aside>
        <div id="resizer"></div>
        <main><div class="nx12-content">\n`);

        // 流式追加所有本地缓存内容
        const cacheFiles = fs.readdirSync(CACHE_DIR).sort();
        for (const f of cacheFiles) {
            if (f.endsWith('.html')) {
                writeStream.write(fs.readFileSync(path.join(CACHE_DIR, f), "utf-8") + "\n");
            }
        }

        // 写入闭合标签和 JS
        writeStream.write(`</div></main>\n${uiJs}\n</body></html>`);
        writeStream.end();

        // 等待流写入彻底完成
        await new Promise((resolve) => writeStream.on("finish", resolve));

        const endTime = Date.now();
        const elapsedSec = Math.floor((endTime - startTime) / 1000);
        const mins = Math.floor(elapsedSec / 60);
        const secs = elapsedSec % 60;

        console.log(`🎉 任务完成！输出文件: ${FINAL_OUTPUT_FILE}`);
        console.log(`⏱️ 总耗时: ${mins}分 ${secs}秒`);

    } finally {
        process.exit();
    }
})();