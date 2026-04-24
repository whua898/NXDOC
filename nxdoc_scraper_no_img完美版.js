/**
 * ============================================================================
 * NX Documentation Scraper (Optimized Version)
 *
 * Features:
 * - Playwright-based headless scraping
 * - SQLite caching with WAL mode for high performance
 * - Absolute URLs for images (No downloading, lightweight version)
 * - Intelligent table layout preservation and responsive CSS injection
 * ============================================================================
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");
const { chromium } = require("playwright");
const sqlite3 = require("sqlite3").verbose();

// 优化：CSS 内存缓存，避免重复下载相同样式文件
const globalCssCache = new Map();

// ==========================================
// ⚙️ 全局配置区
// ==========================================
// ==========================================
// 📋 批量任务配置区 (从 download_list.txt 读取)
// ==========================================

function loadSubjectsFromFile(filename = "download_list.txt") {
  try {
    const content = fs.readFileSync(filename, "utf8");
    const lines = content
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    const subjects = [];
    // 每两行为一组：标题 + URL
    for (let i = 0; i < lines.length; i += 2) {
      if (i + 1 < lines.length) {
        const title = lines[i];
        const url = lines[i + 1];

        // 生成安全的名称
        const name = title.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5]/g, "_");

        if (url.startsWith("http") && title.length > 0) {
          subjects.push({
            name: name,
            url: url,
            title: title,
          });
        }
      }
    }

    return subjects;
  } catch (error) {
    if (error.code === "ENOENT") {
      console.error(
        `❌ 配置文件 ${filename} 不存在，请创建 download_list.txt 文件`,
      );
    } else {
      console.error(`❌ 读取配置文件出错: ${error.message}`);
    }
    return [];
  }
}

const SUBJECTS = loadSubjectsFromFile();

// 动态变量声明（原常量改为 let）
let START_URL = "";
let FINAL_OUTPUT_FILE = "";
let CACHE_DIR_NAME = "NX12_pages"; // 保持固定
let SIDEBAR_TITLE = "";
let MAX_CONCURRENCY = 9; // 保持较低并发以稳定运行
let NAV_JSON_FILE = "";
let CACHE_DB_FILE = "";
let OUTPUT_DIR = "output"; // 专门的输出目录

// 固定常量保持不变
const CACHE_DIR = path.join(__dirname, CACHE_DIR_NAME);
const TARGET_IFRAME_SELECTOR = "#xhtml";
const md5 = (s) => crypto.createHash("md5").update(s).digest("hex");

// ==========================================
// 🚀 数据库核心函数
// ==========================================

function initDatabase() {
  const db = new sqlite3.Database(CACHE_DB_FILE);
  db.serialize(() => {
    // 优化 SQLite 性能：启用 WAL 模式与内存临时存储，提升并发写入速度
    db.run(`PRAGMA journal_mode = WAL;`);
    db.run(`PRAGMA synchronous = NORMAL;`);
    db.run(`PRAGMA temp_store = MEMORY;`);
    db.run(
      `CREATE TABLE IF NOT EXISTS styles (hash TEXT PRIMARY KEY, content TEXT)`,
    );
    db.run(
      `CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, title TEXT, html TEXT, css_hash TEXT)`,
    );
  });
  return db;
}

function checkPageExists(db, url) {
  return new Promise((resolve) => {
    db.get("SELECT 1 FROM cache WHERE url = ?", [url], (err, row) => {
      resolve(!!row);
    });
  });
}

function saveToDatabase(db, url, title, html, cssBlocks) {
  return new Promise((resolve, reject) => {
    let cssHash = "";
    if (Array.isArray(cssBlocks) && cssBlocks.length > 0) {
      cssHash = md5(cssBlocks.join(""));
    } else if (typeof cssBlocks === "string" && cssBlocks.trim()) {
      cssHash = md5(cssBlocks);
    }

    db.serialize(() => {
      if (Array.isArray(cssBlocks) && cssBlocks.length > 0) {
        const stmt = db.prepare(
          "INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)",
        );
        for (const css of cssBlocks) {
          if (css && css.trim()) {
            stmt.run(md5(css), css);
          }
        }
        stmt.finalize();
      } else if (typeof cssBlocks === "string" && cssBlocks.trim()) {
        const stmt = db.prepare(
          "INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)",
        );
        stmt.run(md5(cssBlocks), cssBlocks);
        stmt.finalize();
      }

      const stmt2 = db.prepare(
        "INSERT OR REPLACE INTO cache (url, title, html, css_hash) VALUES (?, ?, ?, ?)",
      );
      stmt2.run(url, title, html, cssHash, (err) => {
        if (err) reject(err);
        else resolve();
      });
      stmt2.finalize();
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
  return String(cssText).replace(
    /url\(\s*(['"]?)([^'")]+)\1\s*\)/g,
    (m, quote, url) => {
      const raw = String(url).trim();
      if (
        !raw ||
        raw.startsWith("data:") ||
        raw.startsWith("blob:") ||
        raw.startsWith("#")
      )
        return m;
      if (/^https?:\/\//i.test(raw)) return `url(${quote}${raw}${quote})`;
      try {
        return `url(${quote}${new URL(raw, baseUrl).href}${quote})`;
      } catch {
        return m;
      }
    },
  );
}

function processTableSignatures(htmlContent) {
  return [];
}

async function buildInlineCss(frame) {
  if (!frame) return [];

  const meta = await frame.evaluate(() => {
    const baseUrl = document.baseURI;
    const inline = Array.from(document.querySelectorAll("style")).map(
      (s) => s.textContent || "",
    );
    const links = Array.from(
      document.querySelectorAll('link[rel="stylesheet"]'),
    )
      .map((l) => l.href)
      .filter(Boolean);
    return { baseUrl, inline, links };
  });

  let cssBlocks = [];

  for (const text of meta.inline) {
    if (text.trim()) {
      cssBlocks.push(cssTextToAbsoluteUrls(text, meta.baseUrl));
    }
  }

  for (const href of meta.links) {
    let cssText = "";
    if (globalCssCache.has(href)) {
      cssText = globalCssCache.get(href);
    } else {
      try {
        cssText = await frame.evaluate(async (url) => {
          try {
            const resp = await fetch(url);
            return resp.ok ? await resp.text() : "";
          } catch {
            return "";
          }
        }, href);
        if (cssText) {
          globalCssCache.set(href, cssText);
        }
      } catch {}
    }
    if (cssText && cssText.trim()) {
      cssBlocks.push(
        cssTextToAbsoluteUrls(`/* ${href} */\n${cssText}`, meta.baseUrl),
      );
    }
  }

  return cssBlocks;
}

// 核心逻辑：在浏览器上下文中预处理 DOM 与表格结构
async function capturePageContent(page) {
  try {
    // 优化：Iframe 智能等待，加速无 iframe 页面的处理
    await Promise.race([
      page.waitForSelector(TARGET_IFRAME_SELECTOR, { timeout: 15000 }),
      page.waitForFunction(
        () => {
          // 如果页面已经完全加载，且确实没有 iframe，提前结束等待
          return (
            document.readyState === "complete" &&
            document.querySelectorAll("iframe").length === 0
          );
        },
        { timeout: 15000 },
      ),
    ]);
  } catch (e) {
    // 忽略超时，继续往下找
  }

  let frame = page.frames().find((f) => f.name() === "xhtml");

  if (!frame) {
    frame = page
      .frames()
      .find(
        (f) => f.url().includes("/documentation/") || f.url().includes("help"),
      );
  }

  if (!frame) {
    const element = await page.$(TARGET_IFRAME_SELECTOR);
    if (element) frame = await element.contentFrame();
  }

  if (!frame) throw new Error("无法获取目标 Frame (frame is null/undefined)");

  // 🚀 优化：滚动页面到底部，触发所有懒加载的图片和视频
  try {
    await frame.evaluate(async () => {
      // 找到所有可能懒加载的元素
      const lazyEls = document.querySelectorAll("disw-video, video, img");
      if (lazyEls.length > 0) {
        // 快速滚动一遍触发 IntersectionObserver
        for (const el of lazyEls) {
          try {
            el.scrollIntoView({ behavior: "instant", block: "center" });
          } catch (e) {}
        }
        // 滚回顶部
        window.scrollTo(0, 0);
        const container =
          document.querySelector(".doc-content") ||
          document.querySelector(".main.content-container");
        if (container) container.scrollTop = 0;
      }
    });
  } catch (e) {}

  // 🚀 优化：等待页面中的视频组件（disw-video）加载出真实的播放地址
  try {
    const mediaUrls = page.__mediaUrls || [];
    await frame.evaluate(async (interceptedUrls) => {
      window.__interceptedMediaUrls = interceptedUrls;
      const videos = document.querySelectorAll("disw-video");
      if (videos.length > 0) {
        await Promise.all(
          Array.from(videos).map((v) => {
            return new Promise((resolve) => {
              if (
                v.querySelector("video source") ||
                v.querySelector("video[src]")
              ) {
                return resolve();
              }
              const observer = new MutationObserver(() => {
                if (
                  v.querySelector("video source") ||
                  v.querySelector("video[src]")
                ) {
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
          }),
        );
      }

      // 🚀 优化：尝试从父容器提取真实的 mp4 地址（针对 blob: 视频及未加载的视频）
      document.querySelectorAll("video").forEach((v) => {
        let realSrc = null;

        // 1. 检查 video 自身的属性
        Array.from(v.attributes).forEach((attr) => {
          if (
            attr.value.includes(".mp4") ||
            attr.value.includes(".webm") ||
            attr.value.includes(".m3u8")
          ) {
            realSrc = attr.value;
          }
        });

        // 2. 检查 source 子节点
        if (!realSrc) {
          v.querySelectorAll("source").forEach((s) => {
            let src =
              s.getAttribute("src") ||
              s.getAttribute("data-src") ||
              s.getAttribute("data-video-src");
            if (
              src &&
              (src.includes(".mp4") ||
                src.includes(".webm") ||
                src.includes(".m3u8"))
            ) {
              realSrc = src;
            }
          });
        }

        // 3. 向上遍历父节点查找
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
          let parent = v.parentElement;
          while (parent && parent.tagName !== "BODY") {
            Array.from(parent.attributes).forEach((attr) => {
              if (
                attr.value.includes(".mp4") ||
                attr.value.includes(".webm") ||
                attr.value.includes(".m3u8")
              ) {
                realSrc = attr.value;
              }
            });
            if (realSrc) break;

            if (parent.tagName.toLowerCase() === "disw-video") {
              const videoUrl =
                parent.getAttribute("video-url") ||
                parent.getAttribute("src") ||
                parent.getAttribute("data-video-url") ||
                parent.getAttribute("data-src");
              if (videoUrl) {
                realSrc = videoUrl;
                break;
              }
              // 如果 disw-video 内部有 JSON 配置
              let match = parent.innerHTML.match(
                /(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i,
              );
              if (match) {
                realSrc = match[1];
                break;
              }
            }

            parent = parent.parentElement;
          }
        }

        // 4. 全局搜索 script 标签中的视频链接
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
          let scripts = document.querySelectorAll("script");
          for (let script of scripts) {
            let match = script.textContent.match(
              /(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i,
            );
            if (match) {
              realSrc = match[1];
              break;
            }
          }
        }

        // 5. 尝试从 window.__interceptedMediaUrls 中获取
        if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
          if (
            window.__interceptedMediaUrls &&
            window.__interceptedMediaUrls.length > 0
          ) {
            realSrc = window.__interceptedMediaUrls[0];
          }
        }

        if (realSrc) {
          v.setAttribute("data-real-src", realSrc);
        }
      });

      // 额外检查 disw-video，防止 video 标签尚未生成
      document.querySelectorAll("disw-video").forEach((v) => {
        let realSrc =
          v.getAttribute("video-url") ||
          v.getAttribute("src") ||
          v.getAttribute("data-video-url") ||
          v.getAttribute("data-src");
        if (!realSrc) {
          Array.from(v.attributes).forEach((attr) => {
            if (
              attr.value.includes(".mp4") ||
              attr.value.includes(".webm") ||
              attr.value.includes(".m3u8")
            ) {
              realSrc = attr.value;
            }
          });
        }
        if (!realSrc) {
          let match = v.innerHTML.match(
            /(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i,
          );
          if (match) realSrc = match[1];
        }
        if (
          !realSrc &&
          window.__interceptedMediaUrls &&
          window.__interceptedMediaUrls.length > 0
        ) {
          realSrc = window.__interceptedMediaUrls[0];
        }
        if (realSrc) {
          let video = v.querySelector("video");
          if (!video) {
            video = document.createElement("video");
            video.setAttribute("data-real-src", realSrc);
            v.appendChild(video);
          } else if (!video.getAttribute("data-real-src")) {
            video.setAttribute("data-real-src", realSrc);
          }
        }
      });
    }, mediaUrls);
  } catch (e) {}

  return await frame
    .evaluate(async () => {
      const unwanted = [
        ".navbar",
        ".header",
        ".footer",
        "#doc-sidebar",
        ".breadcrumb",
        ".site-header",
        ".topbar",
        ".app-header",
        ".global-nav",
        ".nav-header",
        ".doc-sidebar",
        "#topic-navigator",
        ".hidden-md-up",
        "#feedback-btns",
        ".gutter",
        ".cookie-banner",
        ".cookie-consent",
        ".gdpr-banner",
        ".disw-video-loader-container",
      ];
      document.querySelectorAll(unwanted.join(",")).forEach((e) => e.remove());

      const footerKeywords = [
        "Learn more",
        "How do I",
        "Look up more details",
        "See also",
        "See Also",
        "Related Concepts",
        "Related Reference",
        "Related Topics",
        "Related Tasks",
        "Related Information",
        "Related Links",
        "相关概念",
        "相关参考",
        "相关主题",
        "相关任务",
        "相关信息",
        "相关链接",
        "了解更多",
        "如何操作",
        "如何...",
        "查找更多详细信息",
        "另请参见",
      ];

      Array.from(document.querySelectorAll("*")).forEach((el) => {
        const txt = (el.textContent || "").trim();
        if (
          footerKeywords.includes(txt) &&
          /^(H[1-6]|STRONG|B|DIV|SPAN)$/i.test(el.tagName)
        ) {
          const wrapper = el.closest(
            ".container-fluid, .related-links, .topic-links, .familylinks",
          );
          if (
            wrapper &&
            wrapper !== document.body &&
            (wrapper.textContent || "").length < 2000
          ) {
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

      const container =
        document.querySelector("div.doc-content") || document.body;

      // 🌟 识别小图标，防止被误判为大图居中
      container.querySelectorAll("img").forEach((img) => {
        if (
          (img.clientWidth > 0 && img.clientWidth <= 80) ||
          (img.naturalWidth > 0 && img.naturalWidth <= 80)
        ) {
          img.classList.add("inline-small-icon");
        }
      });

      const clone = container.cloneNode(true);

      const containerWrappers = clone.querySelectorAll(
        ".main.content-container, .content-container, .doc-content",
      );
      containerWrappers.forEach((wrapper) => {
        wrapper.style.cssText =
          "border:none !important; box-shadow:none !important; margin:0 !important; padding:0 !important;";
      });

      const baseUrl = document.baseURI;

      // 前置处理：将所有图片的相对链接转换为绝对链接，并移除懒加载以保证滚动流畅
      clone.querySelectorAll("img").forEach((img) => {
        if (img.hasAttribute("src")) {
          const absoluteUrl = new URL(img.getAttribute("src"), baseUrl).href;
          img.src = absoluteUrl;
          // 移除懒加载，强制浏览器在初始加载时获取图片，避免滚动时实时请求导致卡顿
          img.removeAttribute("loading");
          img.setAttribute("decoding", "async");
        }
      });

      clone.querySelectorAll("a, video, source, track").forEach((el) => {
        // 尝试从 data-src 或 data-video-src 或 data-real-src 恢复真实的 src
        ["data-real-src", "data-src", "data-video-src"].forEach((attr) => {
          if (el.hasAttribute(attr)) {
            const val = el.getAttribute(attr);
            if (val && !val.startsWith("blob:") && !val.startsWith("data:")) {
              el.setAttribute("src", val);
            }
          }
        });

        if (el.tagName === "A" && el.hasAttribute("href")) {
          try {
            el.href = new URL(el.getAttribute("href"), baseUrl).href;
          } catch (e) {}
        } else if (
          (el.tagName === "VIDEO" ||
            el.tagName === "SOURCE" ||
            el.tagName === "TRACK") &&
          el.hasAttribute("src")
        ) {
          const src = el.getAttribute("src");
          if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
            try {
              el.src = new URL(src, baseUrl).href;
            } catch (e) {}
          }
        }
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

          // 🚀 核心修复：开启原生视频控制条，并彻底剥离官方自定义播放器
          el.setAttribute("controls", "true");
          el.setAttribute("preload", "metadata");
          el.style.cssText =
            "width:100%;height:auto;aspect-ratio:16/9;display:block;background:#000;";

          // 彻底剥离官方的自定义播放器（Plyr）和包裹层，防止其 CSS 隐藏原生控制条或拦截点击
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
              plyr
                .querySelectorAll(
                  ".plyr__controls, .plyr__control--overlaid, .plyr__captions, .plyr__poster",
                )
                .forEach((c) => c.remove());
            }
          }

          // 强制显示被隐藏的播放器容器（官方在加载时会加 display: none）
          let current = el;
          while (current && current.tagName !== "BODY") {
            if (current.style && current.style.display === "none") {
              current.style.display = "block";
            }
            current = current.parentNode;
          }
        }
      });

      // =========================================================================
      // 清理无用 UI 元素：移除代码块的 Copy 按钮及工具栏
      // =========================================================================
      clone
        .querySelectorAll(".codeblock-toolbar")
        .forEach((toolbar) => toolbar.remove());
      clone.querySelectorAll("button, a, div, span").forEach((el) => {
        const txt = (el.textContent || "").trim().toLowerCase();
        if (
          txt === "copy" &&
          (el.tagName === "BUTTON" ||
            (el.className &&
              typeof el.className === "string" &&
              el.className.toLowerCase().includes("copy")))
        ) {
          el.remove();
        }
      });

      // =========================================================================
      // 表格分类与精准排版修复
      // =========================================================================

      // 1. 全局清理：剥离表格的硬编码宽度和内联样式，保留百分比宽度
      clone.querySelectorAll("table").forEach((tbl) => {
        // NX12 uses border="0", NX2506 uses borderless class.
        // NX2506 also uses siemens-table-grid for tables that SHOULD have borders.
        if (
          tbl.getAttribute("border") === "0" ||
          tbl.classList.contains("borderless")
        ) {
          tbl.classList.add("siemens-table-no-grid");
        } else if (
          tbl.getAttribute("border") === "1" ||
          tbl.classList.contains("siemens-table-grid")
        ) {
          tbl.classList.add("siemens-table-with-grid");
        }

        // 剥离固定宽度，保留百分比和比例宽度（如 50%, 99*）以维持原文的比例排版
        const tblW = tbl.getAttribute("width");
        if (!tblW || (!tblW.includes("%") && !tblW.includes("*")))
          tbl.removeAttribute("width");

        tbl.removeAttribute("style");
        tbl.querySelectorAll("col, colgroup, td, th").forEach((el) => {
          const w = el.getAttribute("width");
          if (!w || (!w.includes("%") && !w.includes("*")))
            el.removeAttribute("width");
          el.removeAttribute("style");
        });
      });

      // 2. 嵌套表格处理：自底向上识别并保护嵌套的子表格
      clone.querySelectorAll("table td table").forEach((tbl) => {
        tbl.classList.add("nested-child-table"); // 赋予子表格身份

        // 精确筛选排除被误判的表格：树状图和导航表绝对不是多图对照表
        if (
          tbl.classList.contains("tree") ||
          tbl.classList.contains("navigator") ||
          tbl.closest(".tree") ||
          tbl.closest(".navigator")
        ) {
          tbl.classList.add("nested-text-table");
          tbl.classList.add("siemens-table-no-grid");
        }
        // 只有包含大图的内嵌表格，才是排版用的“无边框多图对照表”
        // 排除只包含小图标（如 inline-small-icon）的表格
        else if (tbl.querySelector("img:not(.inline-small-icon)")) {
          tbl.classList.add("multi-image-layout-table");
          // 只有当它没有明确要求网格时，才去网格
          if (!tbl.classList.contains("siemens-table-with-grid")) {
            tbl.classList.add("siemens-table-no-grid"); // 消除网格黑线
          }
        } else {
          // 纯文本参数表（如 Parameters 详情），必须保留边框和自然宽度
          tbl.classList.add("nested-text-table");
        }
      });

      // 3. 外层表格分类：根据内容特征（图片、代码、长文本）为表格打上分类标签
      // 过滤掉已被识别的子表格和特殊功能表
      clone
        .querySelectorAll(
          "table:not(.locator):not(.navigator):not(.siemens-table-no-grid):not(.nested-child-table)",
        )
        .forEach((table) => {
          // 精确保护官方选项表：强制作为大父表处理，保留两列排版，免疫毒性判定
          if (table.classList.contains("siemens-options-table")) {
            table.classList.add("nested-parent-table");
            return;
          }

          let hasNested = table.querySelector("table") !== null; // 探测是否身为大父表
          let hasImg =
            table.querySelector("img, object, svg, picture") !== null;
          let hasPre = table.querySelector("pre") !== null;
          let hasPreInFirstCol = false;

          // 计算是否有“毒性”超宽文本或超大首列
          let isToxic = false;
          let isFirstColHuge = false;
          const rows = Array.from(table.rows || []);
          rows.forEach((row) => {
            if (row.cells.length > 0) {
              if ((row.cells[0].textContent || "").trim().length > 150)
                isFirstColHuge = true;
              if (row.cells[0].querySelector("pre")) hasPreInFirstCol = true;
            }
            Array.from(row.cells).forEach((cell) => {
              const text = (cell.innerHTML || "").replace(/<[^>]+>/g, " ");
              const words = text.trim().split(/[^a-zA-Z0-9\u4e00-\u9fa5_-]+/);
              for (let w of words) if (w.length > 45) isToxic = true;
            });
          });

          // 统一套上滚动保护壳
          if (
            table.parentElement &&
            !table.parentElement.classList.contains("table-scroll-wrapper")
          ) {
            const wrapper = document.createElement("div");
            wrapper.className = hasImg
              ? "table-scroll-wrapper has-horizontal-scroll"
              : "table-scroll-wrapper";
            table.parentNode.insertBefore(wrapper, table);
            wrapper.appendChild(table);
          }

          // 包含内嵌子表格的父表，保持自然排版
          if (hasNested) {
            table.classList.add("nested-parent-table");
          }
          // 左侧长代码、右侧媒体的组合表
          else if (hasImg && isFirstColHuge) {
            table.classList.add("media-code-table");
          }
          // 首列包含 pre 的代码对照表，防止首列被过度挤压
          else if (hasPreInFirstCol) {
            table.classList.add("code-comparison-table");
          }
          // 首列极长的纯文本表，保持自然排版
          else if (isFirstColHuge) {
            table.classList.add("nested-parent-table"); // 借用 nested-parent-table 的自然排版属性
          }
          // 包含极长单词或 pre 代码块的宽表，需特殊处理以防撑破布局
          else if (isToxic || hasPre) {
            table.classList.add("toxic-wide-table");
            if (
              table.parentElement &&
              table.parentElement.classList.contains("table-scroll-wrapper")
            ) {
              table.parentElement.classList.add("toxic-scroll-wrapper");
            }
            rows.forEach((row) => {
              if (row.cells.length > 1) {
                row.cells[0].classList.add("toxic-col-left");
              }
            });
          }
          // 普通含图片的表格
          else if (hasImg) {
            table.classList.add("image-table-layout");
          }
        });

      // 4. 表头修复：为图文混排表格的纯文本首行添加伪表头类，以便后续居中对齐
      clone
        .querySelectorAll(
          "table.image-table-layout, table.multi-image-layout-table",
        )
        .forEach((table) => {
          const firstRow = table.querySelector("tr");
          if (firstRow) {
            // 如果首行没有大图片，则认为是表头，强制居中
            if (!firstRow.querySelector("img:not(.inline-small-icon)")) {
              firstRow.classList.add("pseudo-header-row");
            }
          }
        });

      // 文本清理：利用 TreeWalker 替换不可见的特殊空白字符为普通空格
      const walker = document.createTreeWalker(
        clone,
        NodeFilter.SHOW_TEXT,
        null,
        false,
      );
      let node;
      while ((node = walker.nextNode())) {
        if (/[\\\u00A0\\\u202F\\\u2007\\\u2060]/.test(node.nodeValue)) {
          node.nodeValue = node.nodeValue.replace(
            /[\\\u00A0\\\u202F\\\u2007\\\u2060]/g,
            " ",
          );
        }
      }

      // 移除残留的外部 CSS、JS 等标签
      clone
        .querySelectorAll('script, link[rel="stylesheet"], style, meta, title')
        .forEach((el) => el.remove());

      // 替换 HTML 中的 &nbsp; 为普通空格
      let finalHtml = clone.innerHTML
        .trim()
        .replace(/&nbsp;/gi, " ")
        .replace(/\u00A0/g, " ");

      return {
        html: finalHtml,
        docClass: container.className,
        mainClass: container.id,
      };
    })
    .then((data) => ({ frame, data }));
}

// ==========================================
// 🌲 目录树处理 & 工具函数
// ==========================================

let globalPageIndex = 0;

function renderSidebarHtml(nodes, level = 0) {
  if (!nodes || nodes.length === 0) return "";
  let html = level === 0 ? '<ul class="root-list active">\n' : "";

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

      if (
        !node.url ||
        node.url.includes("javascript:void(0)") ||
        node.url.trim() === "#"
      ) {
        html += `            <span class="folder-text" onclick="toggleNode(this.previousElementSibling)">${node.text}</span>\n`;
      } else {
        html += `            <a href="#page_${currentPageIndex}" onclick="handleManualClick(this)">${node.text}</a>\n`;
      }

      html += `        </div>\n`;
      if (hasChildren) {
        const activeClass = level === 0 ? " active" : "";
        html += `        <ul class="nested${activeClass}">\n`;
        buildTree(node.children, level + 1);
        html += `        </ul>\n`;
      }
      html += `    </li>\n`;
    });
  }

  buildTree(nodes, level);
  if (level === 0) html += "</ul>\n";
  return html;
}

function askUser(question) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer);
    });
  });
}

// ==========================================
// 🚀 核心逻辑封装
// ==========================================

async function startJob(sub, mode) {
  START_URL = sub.url;
  SIDEBAR_TITLE = sub.title;

  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  FINAL_OUTPUT_FILE = path.join(`${sub.name}.html`);
  CACHE_DB_FILE = path.join(OUTPUT_DIR, `db_${sub.name}.db`);
  NAV_JSON_FILE = path.join(OUTPUT_DIR, `nav_${sub.name}.json`);

  console.log(`\n\n${"=".repeat(60)}`);
  console.log(` 当前主题: ${sub.title}`);
  console.log(` 数据库: ${CACHE_DB_FILE} |  输出: ${FINAL_OUTPUT_FILE}`);
  console.log(`${"=".repeat(60)}`);

  const scriptStartTime = Date.now();
  let realFetchStartTime = 0;
  let realFetchCount = 0;

  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });

  const db = initDatabase();
  const dbCount = await getDatabaseStats(db);
  console.log(`📊 数据库当前记录数: ${dbCount}`);

  if (mode === "r") {
    console.log("🗑️ 清空数据库...");
    await new Promise((r) => db.run("DELETE FROM cache", r));
    await new Promise((r) => db.run("DELETE FROM styles", r));
    if (fs.existsSync(NAV_JSON_FILE)) fs.unlinkSync(NAV_JSON_FILE);
  }

  let browser;
  try {
    console.log("🚀 正在启动后台浏览器引擎...");
    browser = await chromium.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    });
  } catch (e) {
    console.error("❌ 浏览器启动失败:", e.message);
    throw e;
  }

  const activeWorkers = [];
  let isShuttingDown = false;

  async function gracefulShutdown() {
    if (isShuttingDown) return;
    isShuttingDown = true;
    console.log("\n\n🛑 收到中断信号，正在优雅退出当前任务...");

    console.log(`🔒 正在关闭 ${activeWorkers.length} 个并发标签页...`);
    await Promise.all(
      activeWorkers.map((page) => page.close().catch(() => {})),
    );

    if (browser) {
      console.log("🔒 正在关闭浏览器引擎...");
      await browser.close().catch(() => {});
    }

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

  const shutdownHandler = () => gracefulShutdown();
  process.on("SIGINT", shutdownHandler);
  process.on("SIGTERM", shutdownHandler);

  let treeData = [];
  let navItems = [];

  if (fs.existsSync(NAV_JSON_FILE) && mode !== "a" && mode !== "r") {
    console.log("✅ 读取本地目录缓存...");
    treeData = JSON.parse(fs.readFileSync(NAV_JSON_FILE, "utf-8"));
  } else {
    console.log("📋 正在探测目录结构...");
    const page = await browser.newPage();

    // 恢复 domcontentloaded，让主进程尽快释放
    await page
      .goto(START_URL, { waitUntil: "domcontentloaded", timeout: 60000 })
      .catch(() => {});

    console.log("⏳ 正在动态侦测侧边栏渲染状态...");

    // 动态等待侧边栏渲染：检测导航树中是否已挂载足够的节点
    try {
      await page.waitForFunction(
        () => {
          const navRoot =
            document.querySelector("ul.doc-topics") ||
            document.querySelector('[role="tree"]') ||
            document.querySelector(".nx-sidebar ul") ||
            document.querySelector("ul");
          return navRoot && navRoot.querySelectorAll("li").length > 3;
        },
        { timeout: 30000 },
      );
      console.log("✅ 侦测到侧边栏 DOM 挂载完毕！");
    } catch (e) {
      console.log("⚠️ 动态等待超时，尝试强行向下解析...");
    }

    console.log("📋 正在识别侧边栏并自动展开所有导航节点...");
    treeData = await page.evaluate(async () => {
      function findBestNavRoot() {
        let root =
          document.querySelector("ul.doc-topics") ||
          document.querySelector('[role="tree"]');
        if (root) return root;
        let allUls = Array.from(document.querySelectorAll("ul"));
        if (allUls.length === 0) return null;
        allUls.sort(
          (a, b) =>
            b.querySelectorAll("a").length - a.querySelectorAll("a").length,
        );
        return allUls[0];
      }

      const treeRoot = findBestNavRoot();
      if (!treeRoot) return [];

      let lastCount = 0;
      let stuck = 0;
      while (stuck < 6) {
        const expandables = Array.from(
          document.querySelectorAll(
            "li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded), li[aria-expanded='false'] > button",
          ),
        );

        if (expandables.length === 0) {
          stuck++;
          await new Promise((r) => setTimeout(r, 1500));
          continue;
        }

        for (let el of expandables) {
          try {
            el.scrollIntoView({ block: "center", inline: "nearest" });
            el.click();
          } catch (e) {}
        }
        await new Promise((r) => setTimeout(r, 2000));

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
        const lis = ul.querySelectorAll(":scope > li");
        for (let li of lis) {
          const a = li.querySelector(
            ":scope > a, :scope > div > a, .toc-node-content a",
          );
          const sub =
            li.querySelector(":scope > ul") ||
            li.querySelector(":scope > div > ul");

          if (a) {
            result.push({
              text: a.innerText.trim(),
              url: a.href,
              href: a.href,
              hasChildren: !!sub,
              children: parseLevel(sub),
            });
          } else {
            const titleSpan = li.querySelector(
              ":scope > span, :scope > div > span",
            );
            if (titleSpan) {
              result.push({
                text: titleSpan.innerText.trim(),
                url: "",
                href: "",
                hasChildren: !!sub,
                children: parseLevel(sub),
              });
            }
          }
        }
        return result;
      }

      const startUl =
        treeRoot.tagName === "UL" ? treeRoot : treeRoot.querySelector("ul");
      return parseLevel(startUl);
    });

    fs.writeFileSync(NAV_JSON_FILE, JSON.stringify(treeData, null, 2));
    await page.close();
  }

  const flatten = (nodes) =>
    nodes.forEach((n) => {
      navItems.push(n);
      if (n.children) flatten(n.children);
    });
  flatten(treeData);
  console.log(`📊 目录节点总数: ${navItems.length}`);

  let currentIndex = 0;
  let successCount = 0;
  let skipCount = 0;
  let failCount = 0;

  function getLogPrefix(idx, isRealFetch = false) {
    const total = navItems.length;
    const percent = (((idx + 1) / total) * 150).toFixed(1);
    let etaStr = "--";

    if (isRealFetch) {
      if (realFetchStartTime === 0) realFetchStartTime = Date.now();
      realFetchCount++;

      const elapsed = (Date.now() - realFetchStartTime) / 1500;
      const rate = realFetchCount / elapsed;

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
    let context = null;

    const createPage = async () => {
      if (page) {
        const idx = activeWorkers.indexOf(page);
        if (idx > -1) activeWorkers.splice(idx, 1);
        try {
          await page.close();
        } catch {}
      }
      if (context)
        try {
          await context.close();
        } catch {}

      context = await browser.newContext();
      page = await context.newPage();

      activeWorkers.push(page);

      await page.route("**/*", (route) => {
        const req = route.request();
        const type = req.resourceType();
        const url = req.url().toLowerCase();

        // 🚀 优化：拦截无关的网络请求（如字体、埋点、广告等），但必须放行 xhr/fetch/media 以便视频组件获取真实链接
        if (
          ["font", "beacon", "csp_report", "websocket"].includes(type) ||
          url.includes("analytics") ||
          url.includes("tracking") ||
          url.includes("telemetry") ||
          url.includes("metrics") ||
          url.includes("googletagmanager.com") ||
          url.includes("google-analytics.com") ||
          url.includes("tealiumiq.com") ||
          url.includes("tiqcdn.com") ||
          url.includes("assets.adobedtm.com") ||
          url.includes("smetrics.siemens.com")
        ) {
          route.abort();
        } else {
          route.continue();
        }
      });

      page.__mediaUrls = [];
      page.on("response", (response) => {
        const url = response.url();
        const type = response.headers()["content-type"] || "";
        if (
          type.includes("video/") ||
          type.includes("application/vnd.apple.mpegurl") ||
          url.includes(".mp4") ||
          url.includes(".webm") ||
          url.includes(".m3u8")
        ) {
          if (!url.includes("blank.mp4")) {
            page.__mediaUrls.push(url);
          }
        }
      });
    };

    await createPage();

    while (currentIndex < navItems.length && !isShuttingDown) {
      const idx = currentIndex++;
      const item = navItems[idx];

      if (
        !item ||
        !item.url ||
        item.url.includes("javascript") ||
        item.url.trim() === "#"
      ) {
        console.log(
          `   ℹ️ [${idx + 1}/${navItems.length}] [线程${id}] ${item.text} (📁 纯目录外壳，自动跳过)`,
        );
        continue;
      }

      const exists = await checkPageExists(db, item.url);
      if (exists) {
        skipCount++;
        console.log(
          `[${idx + 1}/${navItems.length}] [线程${id}] ${item.text} (✓ 数据库极速恢复) | ${getLogPrefix(idx, false)}`,
        );
        continue;
      }

      let retries = 0;
      let success = false;
      while (retries < 3 && !success && !isShuttingDown) {
        try {
          if (retries > 0) await new Promise((r) => setTimeout(r, 2000));
          if (page.isClosed()) await createPage();
          page.__mediaUrls = []; // Reset media URLs for the new page

          await page.goto(item.url, {
            waitUntil: "domcontentloaded",
            timeout: 30000,
          });

          const { frame, data } = await capturePageContent(page);
          const css = frame ? await buildInlineCss(frame) : [];

          if (data.html) {
            const tableSigs = processTableSignatures(data.html);
            await saveToDatabase(db, item.url, item.text, data.html, css);
            successCount++;
            success = true;
            console.log(
              `[${idx + 1}/${navItems.length}] [线程${id}] ${item.text} (✓ 抓取成功) | ${getLogPrefix(idx, true)}`,
            );
          } else {
            throw new Error("HTML为空");
          }
        } catch (e) {
          retries++;
          console.error(
            `   ❌ [${item.text}] [线程${id}] 重试 ${retries}/3: ${e.message}`,
          );
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
      try {
        await page.close();
      } catch {}
    }
    if (context)
      try {
        await context.close();
      } catch {}
  }

  console.log(`⚡ 启动 ${MAX_CONCURRENCY} 个并发线程...`);
  const workers = Array(MAX_CONCURRENCY)
    .fill(null)
    .map((_, i) => worker(i + 1));
  await Promise.all(workers);

  if (isShuttingDown) return;

  console.log(
    `\n✅ 抓取结束 | 新增: ${successCount} | 跳过: ${skipCount} | 失败: ${failCount}`,
  );

  globalPageIndex = 0;

  console.log("⏳ 正在生成最终 HTML...");
  const writeStream = fs.createWriteStream(FINAL_OUTPUT_FILE);

  writeStream.write(`<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${SIDEBAR_TITLE}</title>
`);
  writeStream.write(`    <style>\n`);

  const allStyles = await new Promise((r) =>
    db.all("SELECT content FROM styles", (e, rows) => r(rows || [])),
  );
  allStyles.forEach((row) => writeStream.write(row.content + "\n"));

  writeStream.write(`\n/* ====================================================================
   UI框架样式开始 - 仅包含必需的布局和导航样式
   ==================================================================== */
`);

  const uiFrameworkCss = `
        /* 🚀 修改：加入 font-size: 14px 控制整体字号 */
        .nx-sidebar { width: 340px; min-width: 250px; display: flex; flex-direction: column; background: #f8f9fa; border-right: 1px solid #dee2e6; overflow: hidden; font-size: 13px; }
        .nx-sidebar-header { padding: 15px 10px; border-bottom: 2px solid #007cba; flex-shrink: 0; }
        .nx-sidebar-content { flex: 1; overflow-y: auto; padding: 5px; }
        .nx-sidebar ul, .nx-sidebar li { list-style: none; margin: 0; padding: 0; }
        .nx-sidebar ul.nested { display: none; margin-left: 8px; border-left: 1px solid #888; }
        .nx-sidebar ul.active { display: block; }
        
        /* 🚀 修改：margin 改为 0，让行与行之间更紧凑 */
        .nav-item-row { display: flex; align-items: flex-start; margin: 0; }
        
        .caret { cursor: pointer; width: 14px; min-width: 14px; font-size: 10px; margin-top: 4px; text-align: center; } /* 微调箭头位置 */
        /* 🚀 加上 display: inline-block，激活 transform 旋转权限！ */
        .caret::before { content: "▶"; display: inline-block; transition: transform 0.2s; }
        .caret-down::before { content: "▼"; transform: none; }
        .no-caret { width: 14px; min-width: 14px; }
        
        /* 🚀 修改：减小 padding 和 line-height，让链接条目更窄 */
        .nx-sidebar a { text-decoration: none; color: #005f87; padding: 2px 5px; border-radius: 4px; line-height: 1.25; flex: 1; }
        .nx-sidebar a:hover { background: #e2e8f0; }
        .selected-link { background: #ADD8E6 !important; color: #000 !important; }
        .resizer { width: 5px; cursor: col-resize; background: #dee2e6; }
        .resizer:hover { background: #007cba; }
        .main-content { flex: 1; min-width: 0; overflow-y: auto; overflow-x: auto; background: #fff; }
        
        /* 🚀 提升高级感：将左右内边距扩大至 40px，上下 20px，提供极佳的宽屏阅读呼吸感 */
        .content-wrapper { padding: 20px 40px !important; overflow-x: hidden !important; }
        
        .page-section { margin-bottom: 80px; padding-top: 30px; border-top: 2px solid #eaeaea; }
        .page-section:first-child { border-top: none; padding-top: 0; }
`;
  writeStream.write(uiFrameworkCss);

  writeStream.write(`\n/* ====================================================================
   异常修正样式 - 仅针对具体问题进行精准修正
   ==================================================================== */
`);

  const exceptionFixCss = `
    /* ====================================================================
       异常修正样式 - 核心排版引擎 (配合爬虫精准打标版)
       ==================================================================== */

    /* --------------------------------------------------------------------
       【1】 基础表格体系重置 & 滚动控制
       -------------------------------------------------------------------- */
    .page-section table:not(.locator):not(.navigator):not(.siemens-table-no-grid) { 
        display: table !important; 
        width: auto !important;
        max-width: none !important; 
        border-collapse: collapse !important;
    }
    
    /* 只给明确声明有边框的表格加上边框，外层表格使用适中的灰色 */
    .page-section table.siemens-table-with-grid {
        border: 1px solid #aaa !important;
    }

    .page-section table.siemens-table-with-grid > tbody > tr > td,
    .page-section table.siemens-table-with-grid > tbody > tr > th,
    .page-section table.siemens-table-with-grid > thead > tr > td,
    .page-section table.siemens-table-with-grid > thead > tr > th,
    .page-section table.siemens-table-with-grid > tfoot > tr > td,
    .page-section table.siemens-table-with-grid > tfoot > tr > th,
    .page-section table.siemens-table-with-grid > tr > td,
    .page-section table.siemens-table-with-grid > tr > th {
        border: 1px solid #aaa !important;
    }

    /* 嵌套的内层表格边框更浅，避免视觉喧宾夺主 */
    .page-section table.nested-child-table.siemens-table-with-grid {
        border: 1px solid #e5e5e5 !important;
    }
    .page-section table.nested-child-table.siemens-table-with-grid > tbody > tr > td,
    .page-section table.nested-child-table.siemens-table-with-grid > tbody > tr > th,
    .page-section table.nested-child-table.siemens-table-with-grid > thead > tr > td,
    .page-section table.nested-child-table.siemens-table-with-grid > thead > tr > th,
    .page-section table.nested-child-table.siemens-table-with-grid > tfoot > tr > td,
    .page-section table.nested-child-table.siemens-table-with-grid > tfoot > tr > th,
    .page-section table.nested-child-table.siemens-table-with-grid > tr > td,
    .page-section table.nested-child-table.siemens-table-with-grid > tr > th {
        border: 1px solid #e5e5e5 !important;
    }
    .page-section table:not(.locator):not(.navigator):not(.siemens-table-no-grid) td > div { 
        width: auto !important; 
        max-width: none !important; 
    }
    
    .table-scroll-wrapper { width: 100%; max-width: 100%; overflow-x: auto; overflow-y: visible; }
    .table-scroll-wrapper table:not(.siemens-table-no-grid) { width: auto !important; max-width: none !important; }
    .toxic-scroll-wrapper { max-height: 85vh; overflow-y: auto; }

    .page-section table.siemens-table-no-grid {
        width: 100% !important;
        table-layout: auto !important;
        border: none !important;
    }
    .page-section table.siemens-table-no-grid td,
    .page-section table.siemens-table-no-grid th {
        border: none !important;
    }

    /* --------------------------------------------------------------------
       【2】 特殊表格场景策略 (毒性表、嵌套表、图文表)
       -------------------------------------------------------------------- */
    /* 🚀 场景 A：真正的毒性超宽表。恢复核打击能力，强制切断连续长字符！ */
    table.toxic-wide-table {
        width: 100% !important; 
        table-layout: fixed !important; 
    }
    table.toxic-wide-table td, table.toxic-wide-table th {
        word-break: break-all !important; /* 恢复暴力断词，因为嵌套表已经免疫了 */
        overflow-wrap: anywhere !important;
        white-space: normal !important;
    }
    table.toxic-wide-table .toxic-col-left {
        width: 150px !important; 
        min-width: 150px !important;
        max-width: 150px !important; 
        word-break: normal !important; /* 首列短词保留正常换行 */
    }

    /* 🚀 场景 B：嵌套父子表。确保它们排版自然，绝对不继承断词 */
    table.nested-parent-table, table.nested-child-table {
        table-layout: auto !important;
        width: 100% !important;
    }
    table.nested-parent-table td, table.nested-child-table td {
        word-break: normal !important;
        overflow-wrap: break-word !important;
        white-space: normal !important;
    }

    /* 🚀 场景 E：代码对照表 (首列包含 pre)。强制固定布局，防止被长代码挤压 */
    table.code-comparison-table {
        table-layout: fixed !important;
        width: 100% !important;
    }
    table.code-comparison-table td {
        word-break: normal !important;
        overflow-wrap: break-word !important;
        white-space: normal !important;
        vertical-align: top !important;
    }

    /* 场景 C：左代码/右图片表 */
    table.media-code-table {
        width: auto !important;
        max-width: none !important;
        table-layout: auto !important;
    }
    table.media-code-table td:first-child, 
    table.media-code-table td:first-child * {
        white-space: nowrap !important;
        word-break: normal !important;
        overflow-wrap: normal !important;
        max-width: none !important;
    }
    table.media-code-table td:first-child pre { white-space: pre !important; }

    /* 场景 D：纯图片对照表 (去除多余补丁，回归精简) */
    table.image-table-layout {
        table-layout: auto !important;
        width: auto !important; 
        max-width: 100% !important; 
    }
    table.image-table-layout td {
        vertical-align: middle !important; 
        white-space: normal !important;
    }
    table.image-table-layout img {
        max-width: none !important; /* 防止图片被长文本挤压变小 */
    }
    table.image-table-layout pre, table.image-table-layout code {
        white-space: pre !important; 
        overflow: visible !important; 
        max-height: none !important; 
        width: auto !important;
        max-width: none !important;
    }

    /* --------------------------------------------------------------------
       【3】 表格内部元素细节修复 (代码块、缩进、关键字、子标题)
       -------------------------------------------------------------------- */
    .page-section .keyword {
        font-weight: bold !important;
    }
    .page-section tr.subheader, .page-section tr.subheader > td, .page-section tr.subheader > th {
        background-color: #f0f4f8 !important; /* 浅蓝灰背景 */
    }

    .page-section table pre, .page-section table code, .page-section table .code-text {
        white-space: pre-wrap !important;
        word-break: break-word !important;
        max-width: 100% !important;
    }
    .page-section table pre {
        background: #f8f9fa !important;       
        border: 1px solid #ddd !important;
        border-radius: 4px !important;
        padding: 8px 12px !important;         
        display: block !important;            
        max-height: 85vh !important;
        overflow-y: auto !important;
        overflow-x: visible !important;
        margin-top: 8px !important; 
    }
    .page-section table code, .page-section table .code-text {
        display: inline !important;
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        margin: 0 !important;
    }
    .page-section table dd { margin-left: 10px !important; }

    /* --------------------------------------------------------------------
       【4】 非表格语义化排版修复 (提示框、字典列表)
       -------------------------------------------------------------------- */
    .page-section .attention,
    .page-section div[type="example"],
    .page-section div[type="note"],
    .page-section div[type="warning"] {
        box-sizing: border-box !important;
        max-width: 100% !important;
        width: auto !important;
        overflow-wrap: break-word !important;
        word-wrap: break-word !important;
        margin: 10px 0 !important;
    }
    .page-section .attention pre, 
    .page-section .attention code, 
    .page-section .attention .code-text {
        white-space: pre-wrap !important;
        word-break: break-word !important;
        max-width: 100% !important;
    }

    .page-section dl {
        display: grid !important;
        grid-template-columns: 150px minmax(0, 1fr) !important;
        width: 100% !important;
        margin-top: 10px !important;
        margin-bottom: 15px !important;
        row-gap: 8px;
    }
    .page-section dl > dt, .page-section dl > div > dt {
        grid-column: 1 !important; width: 150px !important; min-width: 150px !important;
        word-break: break-word !important; margin: 0 !important; padding: 6px !important; font-weight: bold;
    }
    .page-section dl > dd, .page-section dl > div > dd {
        grid-column: 2 !important; margin: 0 !important; padding: 6px !important; width: 100% !important;
    }
    .page-section dl > div { display: contents !important; }

    /* --------------------------------------------------------------------
       【5】 Bootstrap 伪装表格深度修复 (div.row 网格折行)
       -------------------------------------------------------------------- */
    .page-section div.row {
        display: flex !important; 
        flex-wrap: wrap !important; 
        width: 100% !important; 
        margin: 0 !important; 
        box-sizing: border-box !important;
    }
    .page-section div.row > div[class*="col"] {
        box-sizing: border-box !important; 
        padding: 6px !important; 
        margin: 0 !important;
    }
    /* 左侧窄列：150px */
    .page-section div.row > div[class~="col-1"], .page-section div.row > div[class~="col-2"], .page-section div.row > div[class~="col-3"], .page-section div.row > div[class~="col-4"],
    .page-section div.row > div[class~="col-sm-1"], .page-section div.row > div[class~="col-sm-2"], .page-section div.row > div[class~="col-sm-3"], .page-section div.row > div[class~="col-sm-4"],
    .page-section div.row > div[class~="col-md-1"], .page-section div.row > div[class~="col-md-2"], .page-section div.row > div[class~="col-md-3"], .page-section div.row > div[class~="col-md-4"],
    .page-section div.row > div[class~="col-lg-1"], .page-section div.row > div[class~="col-lg-2"], .page-section div.row > div[class~="col-lg-3"], .page-section div.row > div[class~="col-lg-4"] {
        flex: 0 0 150px !important; 
        width: 150px !important; 
        max-width: 150px !important; 
        word-break: break-word !important;
    }
    /* 右侧宽列：利用 calc(100% - 150px) 完美折行 */
    .page-section div.row > div[class~="col-8"], .page-section div.row > div[class~="col-9"], .page-section div.row > div[class~="col-10"], .page-section div.row > div[class~="col-11"],
    .page-section div.row > div[class~="col-sm-8"], .page-section div.row > div[class~="col-sm-9"], .page-section div.row > div[class~="col-sm-10"], .page-section div.row > div[class~="col-sm-11"],
    .page-section div.row > div[class~="col-md-8"], .page-section div.row > div[class~="col-md-9"], .page-section div.row > div[class~="col-md-10"], .page-section div.row > div[class~="col-md-11"],
    .page-section div.row > div[class~="col-lg-8"], .page-section div.row > div[class~="col-lg-9"], .page-section div.row > div[class~="col-lg-10"], .page-section div.row > div[class~="col-lg-11"] {
        flex: 0 0 calc(100% - 150px) !important; 
        width: calc(100% - 150px) !important; 
        max-width: calc(100% - 150px) !important; 
        min-width: 0 !important;
    }
    /* 满宽行列 */
    .page-section div.row > div[class~="col-12"], .page-section div.row > div[class~="col-sm-12"], .page-section div.row > div[class~="col-md-12"], .page-section div.row > div[class~="col-lg-12"] {
        flex: 0 0 100% !important; 
        width: 100% !important; 
        max-width: 100% !important; 
        min-width: 0 !important;
    }
    /* 保护原生表格不被 flex 影响，防止两列表格坍塌为一列 */
    .page-section table tr, .page-section table tr.row {
        display: table-row !important;
    }
    .page-section table td, .page-section table th, .page-section table td.entry, .page-section table th.entry {
        display: table-cell !important;
    }

    /* --------------------------------------------------------------------
       【6】 侧边栏及全局外围样式控制
       -------------------------------------------------------------------- */
    .nx-sidebar .folder-text { 
        cursor: pointer !important; 
        color: #005f87; 
        padding: 2px 5px; 
        border-radius: 4px; 
        line-height: 1.25; 
        flex: 1; 
        display: inline-block; 
        transition: all 0.2s ease; 
    }
    .nx-sidebar .folder-text:hover { background: #e2e8f0; color: #007cba; }
    .nx-sidebar-content { padding-bottom: 20px !important; }
    
    pre, pre.codeblock, pre[class*="language-"] {
        white-space: pre-wrap !important; 
        word-wrap: normal !important; 
        word-break: normal !important; 
        overflow-wrap: break-word !important;
        max-height: 85vh !important; 
        overflow-y: auto !important; 
        overflow-x: visible !important;
        display: block !important; 
        background: #f8f9fa !important;
        padding: 12px !important; 
        border: 1px solid #ddd !important; 
        border-radius: 4px !important;
    }
    
    /* 消除 div.codeblock 带来的双重滚动条和双重边框 */
    div.codeblock {
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        background: transparent !important;
        overflow: visible !important;
        max-height: none !important;
    }
    p code, span code, p .code-text, span .code-text, b code, b .code-text { 
        display: inline !important; 
        background: transparent !important; 
        border: none !important; 
        padding: 0 !important; 
        margin: 0 !important; 
    }
    `;
  writeStream.write(exceptionFixCss);

  writeStream.write(`
/* ====================================================================
   关键修复：body布局样式（必须放在最后）
   ==================================================================== */
        body { 
            display: flex !important; 
            height: 100vh !important; 
            margin: 0 !important; 
            overflow: hidden !important; 
            font-family: "Segoe UI", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, Roboto, sans-serif !important;
        }

/* ====================================================================
   嵌套多图排版 (V6 破壁版：剥离 wrapper 束缚，恢复全宽平分)
   ==================================================================== */
        table.multi-image-layout-table {
            width: 100% !important;
            table-layout: auto !important; /* 允许根据内容自适应列宽 */
            margin: 0 !important;
            border-collapse: collapse !important;
            box-shadow: none !important;
            background: transparent !important;
        }
        
        table.multi-image-layout-table td, 
        table.multi-image-layout-table th {
            width: auto !important;
            max-width: none !important;
            min-width: 0 !important;
            text-align: center !important; /* 图片及说明文字绝对居中 */
            vertical-align: middle !important;
            padding: 4px !important;
            background: transparent !important;
            white-space: normal !important; /* 允许说明文字换行 */
        }
        
        table.multi-image-layout-table img {
            display: inline-block !important;
            margin: 0 auto !important;
            max-width: 100% !important;
            height: auto !important;
        }

/* ====================================================================
   全局所有表格表头统一强制居中修复 (增强版：修复官方将 td 误作 th 的问题)
   ==================================================================== */
        .page-section table th:not([align="left"]):not([align="right"]),
        .page-section table th:not([align="left"]):not([align="right"]) > p,
        .page-section table th:not([align="left"]):not([align="right"]) > div,
        .page-section table th:not([align="left"]):not([align="right"]) > span,
        .page-section table thead td:not([align="left"]):not([align="right"]),
        .page-section table thead td:not([align="left"]):not([align="right"]) > p,
        .page-section table thead td:not([align="left"]):not([align="right"]) > div,
        .page-section table thead td:not([align="left"]):not([align="right"]) > span,
        .page-section table tr.pseudo-header-row td,
        .page-section table tr.pseudo-header-row th,
        .page-section table tr.pseudo-header-row td *,
        .page-section table tr.pseudo-header-row th * {
            text-align: center !important;
            vertical-align: middle !important;
        }
        .page-section table th > p:last-child,
        .page-section table thead td > p:last-child {
            margin-bottom: 0 !important;
        }

/* ====================================================================
   修复作为分隔线的空单元格被 padding 撑开的问题 (提升优先级)
   ==================================================================== */
        .page-section table td[bgcolor="silver"],
        .page-section table td[height="1"],
        .page-section table td[height="2"],
        .page-section table td[height="3"],
        .page-section table td[height="4"],
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td[bgcolor="silver"],
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td[height="1"],
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td[height="2"],
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td[height="3"],
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td[height="4"] {
            padding: 0 !important;
            height: 1px !important;
            border: none !important;
            background-color: silver !important;
        }

/* ====================================================================
   Locator 表格第一列宽度异常修复（包含表头居中特权）
   ==================================================================== */
        table.locator { display: table !important; table-layout: auto !important; width: auto !important; max-width: 100% !important; border-collapse: collapse !important; border: 1px solid #555 !important; margin: 8px 0 !important; font-size: 13px !important; line-height: 1.3 !important; }
        table.locator td, table.locator th { border: 1px solid #555 !important; padding: 4px 2px !important; vertical-align: middle !important; word-wrap: break-word !important; }
        
        table.locator th, 
        table.locator thead td, 
        table.locator th > *, 
        table.locator thead td > * { text-align: center !important; }

        table.locator td:first-child, table.locator th:first-child { width: 155px !important; min-width: 155px !important; max-width: 155px !important; white-space: normal !important; background-color: #f2f2f2 !important; font-weight: 600 !important; color: #555 !important; word-break: normal !important; text-align: center !important; }
        table.locator td:first-child *, table.locator th:first-child * { white-space: normal !important; word-break: normal !important; word-wrap: break-word !important; margin: 0 !important; padding: 0 !important; text-align: center !important; }
        table.locator td:first-child code, table.locator td:first-child pre { display: inline !important; white-space: normal !important; word-break: break-word !important; }
        table.locator p { margin: 0 !important; padding: 0 !important; }

/* ====================================================================
   全局表格紧凑化修复（消除多余行高和留白）
   ==================================================================== */
        .page-section table {
            margin: 12px 0 !important;
            font-size: 14px !important;
            line-height: 1.4 !important;
        }
        .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) td, .page-section table:not(.siemens-table-no-grid):not(.navigator):not(.locator) th {
            padding: 6px 10px !important;
            vertical-align: middle !important;
        }
        .page-section table td p, .page-section table th p,
        .page-section table td div, .page-section table th div,
        .page-section table td ul, .page-section table th ul,
        .page-section table td dl, .page-section table th dl {
            margin-top: 0 !important;
            margin-bottom: 4px !important;
        }
        .page-section table td > :last-child, .page-section table th > :last-child {
            margin-bottom: 0 !important;
        }
        .page-section table dl {
            row-gap: 12px !important;
            margin-top: 8px !important;
            margin-bottom: 8px !important;
        }
        .page-section table dl > dt, .page-section table dl > div > dt,
        .page-section table dl > dd, .page-section table dl > div > dd {
            padding: 6px 8px !important;
        }
        .page-section table dl p {
            margin-bottom: 0 !important;
        }

/* ====================================================================
   图片默认居中修复 (增强版：精准排除树状图组件与各种行内小图标)
   ==================================================================== */
        .page-section img:not([src*="icon" i]):not([src*="ont_" i]):not([src*="mfn_" i]):not([src*="button" i]):not([src*="checkbox" i]):not(.inline-small-icon) {
            display: block;
            margin-left: auto !important;
            margin-right: auto !important;
            max-width: 100%;
        }
        .page-section img[src*="icon" i],
        .page-section img[src*="ont_" i],
        .page-section img[src*="mfn_" i],
        .page-section img[src*="button" i],
        .page-section img[src*="checkbox" i],
        .page-section img.inline-small-icon {
            display: inline-block !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
            margin-bottom: 0 !important;
            vertical-align: middle !important;
        }
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
  writeStream.write(
    `${renderSidebarHtml(treeData)}        </div>\n    </div>\n`,
  );
  writeStream.write(`    <div class="resizer" id="resizer"></div>\n`);
  writeStream.write(
    `    <div class="main-content">\n        <div class="content-wrapper">\n`,
  );

  globalPageIndex = 0;
  let validPageCount = 0;

  for (const item of navItems) {
    if (!item.url || item.url.includes("javascript")) {
      globalPageIndex++;
      continue;
    }

    const row = await new Promise((r) =>
      db.get("SELECT html FROM cache WHERE url = ?", [item.url], (e, row) =>
        r(row),
      ),
    );
    if (row && row.html) {
      const currentPageIndex = globalPageIndex++;
      let cleanHtml = row.html
        .replace(/<\/body>/gi, "")
        .replace(/<\/html>/gi, "")
        .replace(/<body[^>]*>/gi, "")
        .replace(/<html[^>]*>/gi, "")
        .replace(/<head[^>]*>[\s\S]*?<\/head>/gi, "")
        .replace(/<script[^>]*>.*?<\/script>/gi, "")
        .replace(/<!DOCTYPE[^>]*>/gi, "")
        .trim();

      if (cleanHtml) {
        // 🚀 废除正则包裹，直接输出纯净 HTML，保护嵌套表格的结构完整性！
        writeStream.write(
          `            <div class="page-section" id="page_${currentPageIndex}">${cleanHtml}</div>\n`,
        );
        validPageCount++;
      }
    } else {
      globalPageIndex++;
    }
  }

  // 前端交互脚本：处理侧边栏展开/折叠、锚点跳转及侧边栏拖拽调整宽度
  const uiJs = `
    <script>
    window.toggleNode = function(span) {
        if (!span) return;
        const li = span.closest('li');
        if (!li) return;
        const ul = li.querySelector(':scope > ul.nested');
        if (ul) { 
            ul.classList.toggle("active"); 
            span.classList.toggle("caret-down"); 
        }
    };

    window.handleManualClick = function(a) {
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

        setTimeout(() => {
            try {
                const targetId = a.getAttribute('href');
                if (targetId && targetId.startsWith('#')) {
                    const targetEl = document.querySelector(targetId);
                    if (targetEl) targetEl.scrollIntoView({ behavior: 'auto', block: 'start' });
                }
            } catch(e) {}
        }, 10);
    };

    const resizer = document.getElementById('resizer');
    const sidebarEl = document.querySelector('.nx-sidebar');
    if (resizer && sidebarEl) {
        resizer.onmousedown = () => {
            document.onmousemove = e => {
                if (e.clientX > 200 && e.clientX < 800) sidebarEl.style.width = e.clientX + 'px';
            };
            document.onmouseup = () => document.onmousemove = null;
        };
    }
    </script>`;

  await new Promise((resolve, reject) => {
    writeStream.end((err) => {
      process.removeListener("SIGINT", shutdownHandler);
      process.removeListener("SIGTERM", shutdownHandler);

      if (err) {
        reject(err);
        return;
      }

      console.log("✅ 文件主体写入完成");
      db.close();

      try {
        console.log("📝 开始追加结束标签与JS代码...");
        fs.appendFileSync(
          FINAL_OUTPUT_FILE,
          `        </div>
    </div>
${uiJs}
</body>
</html>`,
          "utf8",
        );
        console.log("✅ 代码追加完成");

        console.log(`✅ 成功写入页面内容`);
        console.log(`🎉 文件已生成: ${FINAL_OUTPUT_FILE}`);

        const totalElapsedMs = Date.now() - scriptStartTime;
        const totalElapsedSec = Math.floor(totalElapsedMs / 1500);
        const mins = Math.floor(totalElapsedSec / 60);
        const secs = totalElapsedSec % 60;
        console.log(`⏱️ 总耗时: ${mins}分 ${secs}秒`);

        console.log(`✅ 主题 [${sub.name}] 处理完毕！\n`);

        if (browser) browser.close().catch(() => {});

        resolve();
      } catch (appendError) {
        console.error("❌ 追加代码失败:", appendError.message);
        reject(appendError);
      }
    });
  });
}

// ==========================================
// 🚀 真正的程序入口
// ==========================================

(async () => {
  console.clear();
  console.log("============================================================");
  console.log(" NX文档批量抓取系统 (Optimized Version - Video UI Extraction)");
  console.log("============================================================");

  if (!fs.existsSync("download_list.txt")) {
    console.error("❌ 请先创建 download_list.txt 配置文件！");
    process.exit(1);
  }

  if (SUBJECTS.length === 0) {
    console.error("❌ download_list.txt 文件中没有找到有效的主题配置");
    process.exit(1);
  }

  const modeInput = await askUser(
    "\n 请选择全局运行模式: [a]全自动  [c]续传  [r]重抓  (默认 c): ",
  );
  const mode = modeInput.trim().toLowerCase() || "c";

  const totalStartTime = Date.now();

  for (const sub of SUBJECTS) {
    try {
      await startJob(sub, mode);
    } catch (err) {
      console.error(
        `\n⚠️ 主题 [${sub.name}] 发生致命错误，跳过并继续下一个:`,
        err.message,
      );
    }
  }

  const totalElapsed = Math.floor((Date.now() - totalStartTime) / 1500);
  console.log(`\n\n${"=".repeat(60)}`);
  console.log(
    ` 🎉 所有批量任务执行完毕！总耗时: ${Math.floor(totalElapsed / 60)}分 ${totalElapsed % 60}秒`,
  );
  console.log(`${"=".repeat(60)}`);
  process.exit(0);
})();
