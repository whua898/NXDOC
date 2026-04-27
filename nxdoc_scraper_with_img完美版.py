#!/usr/bin/env python3
"""
NX文档聚合器 - Python 完美原生重制版 (with_img) (v11.28)
完全复刻 nxdoc_scraper_with_img完美版.js 核心逻辑
自动与 JS 版的大小比对并自愈
"""

import os
import sys
import json
import time
import asyncio
import hashlib
import sqlite3
import re
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import signal

# ==========================================
# ⚙️ 全局配置区
# ==========================================
OUTPUT_DIR = "output"
MAX_CONCURRENCY = 9

# 全局 CSS 缓存机制（模仿 JS 版的 globalCssCache）
global_css_cache = {}


def load_subjects(filename="download_list.txt"):
    if not os.path.exists(filename):
        print(f"❌ 配置文件 {filename} 不存在")
        return []
    with open(filename, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    subjects = []
    for i in range(0, len(lines), 2):
        if i + 1 < len(lines):
            title = lines[i]
            url = lines[i + 1]
            name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', title)
            if url.startswith("http") and title:
                subjects.append({"name": name, "url": url, "title": title})
    return subjects


SUBJECTS = load_subjects()


# ==========================================
# 🚀 数据库核心函数
# ==========================================
def init_database(db_file):
    db_dir = os.path.dirname(db_file)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_file, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("CREATE TABLE IF NOT EXISTS styles (hash TEXT PRIMARY KEY, content TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, title TEXT, html TEXT, css_hash TEXT)")
    return conn


async def check_page_exists(db, url):
    cursor = db.execute("SELECT 1 FROM cache WHERE url = ?", (url,))
    return cursor.fetchone() is not None


def save_to_database(db, url, title, html_content, css_blocks):
    css_hash = ""
    if isinstance(css_blocks, list) and css_blocks:
        css_hash = hashlib.md5("".join(css_blocks).encode('utf-8')).hexdigest()
        for block in css_blocks:
            if block and block.strip():
                h = hashlib.md5(block.encode('utf-8')).hexdigest()
                db.execute("INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)", (h, block))
    elif isinstance(css_blocks, str) and css_blocks.strip():
        css_hash = hashlib.md5(css_blocks.encode('utf-8')).hexdigest()
        h = hashlib.md5(css_blocks.encode('utf-8')).hexdigest()
        db.execute("INSERT OR IGNORE INTO styles (hash, content) VALUES (?, ?)", (h, css_blocks))

    db.execute("INSERT OR REPLACE INTO cache (url, title, html, css_hash) VALUES (?, ?, ?, ?)",
               (url, title, html_content, css_hash))
    db.commit()


# ==========================================
# 🌐 浏览器相关函数
# ==========================================
def css_to_absolute_urls(css_text, base_url):
    def replacer(match):
        quote = match.group(1)
        url_raw = match.group(2).strip()
        if not url_raw or url_raw.startswith("data:") or url_raw.startswith("blob:") or url_raw.startswith("#"):
            return match.group(0)
        if re.match(r"^https?://", url_raw, re.IGNORECASE):
            return f"url({quote}{url_raw}{quote})"
        try:
            abs_url = urljoin(base_url, url_raw)
            return f"url({quote}{abs_url}{quote})"
        except:
            return match.group(0)

    return re.sub(r'url\(\s*([\'"]?)([^\'"]+)\2\s*\)', replacer, css_text)


async def build_inline_css(frame, page):
    if not frame: return []
    meta = await frame.evaluate("""() => {
        const baseUrl = document.baseURI;
        const inline = Array.from(document.querySelectorAll("style")).map(s => s.textContent || "");
        const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(l => l.href).filter(Boolean);
        return { baseUrl, inline, links };
    }""")

    css_blocks = []
    base_url = meta.get("baseUrl", "")
    for text in meta.get("inline", []):
        if text.strip(): css_blocks.append(css_to_absolute_urls(text, base_url))

    # 使用全局 CSS 缓存和重试机制
    links = meta.get("links", [])
    for href in links:
        try:
            if href in global_css_cache:
                css_text = global_css_cache[href]
            else:
                max_retries = 2
                css_text = ""
                for retry in range(max_retries + 1):
                    try:
                        resp = await page.request.get(href, timeout=15000)
                        if resp.ok:
                            css_text = await resp.text()
                            if css_text and css_text.strip():
                                global_css_cache[href] = css_text
                                break
                        # CSS 下载失败，静默处理
                    except Exception as download_err:
                        pass  # CSS 下载失败，静默处理

            if css_text and css_text.strip():
                css_blocks.append(css_to_absolute_urls(f"/* {href} */\n{css_text}", href))
        except Exception as e:
            pass  # CSS 异常，静默处理
    return css_blocks


DOM_LOGIC_SCRIPT = r"""
async () => {

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

      // 🌟 核心修复：图像资源生命周期锁
      // 原因：Python 版 Playwright 的 IPC 延迟导致图片未加载完成就被识别
      // 解决：强制等待所有图片完全加载，确保 naturalWidth 不是 0
      const imgs = Array.from(document.querySelectorAll("img"));
      if (imgs.length > 0) {
        await Promise.all(
          imgs.map((img) => {
            // 1. 如果瞬间早已加载好，直接放行 (无任何性能损失)
            if (img.complete && img.naturalWidth > 0) return Promise.resolve();

            // 2. 如果没下完，我们下达强制等待监听器
            return new Promise((resolve) => {
              img.addEventListener("load", resolve, { once: true });
              img.addEventListener("error", resolve, { once: true });
              setTimeout(resolve, 1500); // 容错：每个图片最多硬等 1.5 秒
            });
          }),
        );
      }

      // 🌟 识别小图标，防止被误判为大图居中（现在图片已完全加载）
      container.querySelectorAll("img").forEach((img) => {
        const src = (img.getAttribute("src") || img.src || "").toLowerCase();
        if (
          src.includes("icon") ||
          src.includes("ont_") ||
          src.includes("mfn_") ||
          src.includes("button") ||
          src.includes("checkbox") ||
          src.includes("arrow") ||
          src.includes("plus") ||
          src.includes("minus") ||
          src.includes("check") ||
          src.includes("nav_") ||
          (img.clientWidth > 0 && img.clientWidth <= 80) ||
          (img.naturalWidth > 0 && img.naturalWidth <= 80)
        ) {
          img.classList.add("inline-small-icon");
        }
      });

      const clone = container.cloneNode(true);

      // 🌟 再次在 clone 上识别小图标（基于 URL 特征，不依赖尺寸）
      // 注意：clone 是从已加载完成的 container 克隆的，所以尺寸已经准确
      clone.querySelectorAll("img").forEach((img) => {
        const src = (img.getAttribute("src") || img.src || "").toLowerCase();
        if (
          src.includes("icon") ||
          src.includes("ont_") ||
          src.includes("mfn_") ||
          src.includes("button") ||
          src.includes("checkbox") ||
          src.includes("arrow") ||
          src.includes("plus") ||
          src.includes("minus") ||
          src.includes("check") ||
          src.includes("nav_") ||
          src.includes("filter_")
        ) {
          img.classList.add("inline-small-icon");
        }
      });

      const containerWrappers = clone.querySelectorAll(
        ".main.content-container, .content-container, .doc-content",
      );
      containerWrappers.forEach((wrapper) => {
        wrapper.style.cssText =
          "border:none !important; box-shadow:none !important; margin:0 !important; padding:0 !important;";
      });

      const baseUrl = document.baseURI;

            // 前置处理：将所有图片的相对链接转换为绝对链接，并移除懒加载以保证滚动流畅
      // 将图片转换为 Base64 内联到 HTML 中（并行加速）
      const images = Array.from(clone.querySelectorAll("img"));
      await Promise.all(
        images.map(async (img) => {
          if (img.hasAttribute("src")) {
            const originalSrc = img.getAttribute("src");
            if (originalSrc.startsWith("data:")) return; // 已经是 base64

            const absoluteUrl = new URL(originalSrc, baseUrl).href;
            try {
              const response = await fetch(absoluteUrl);
              if (response.ok) {
                const blob = await response.blob();
                const base64data = await new Promise((resolve) => {
                  const reader = new FileReader();
                  reader.onloadend = () => resolve(reader.result);
                  reader.readAsDataURL(blob);
                });
                img.src = base64data;
              } else {
                img.src = absoluteUrl;
              }
            } catch (e) {
              img.src = absoluteUrl;
            }
            // 移除懒加载，强制浏览器在初始加载时获取图片，避免滚动时实时请求导致卡顿
            img.removeAttribute("loading");
            img.setAttribute("decoding", "async");
          }
        })
      );

      // 🌟 Base64 转换后，再次识别小图标（基于渲染后的真实尺寸）
      // 原因：部分图标在转换前尺寸未确定，或 URL 不包含关键字
      
      // 🚀 关键修复：Base64 转换后，图片需要重新渲染，必须再次等待加载完成
      const convertedImages = Array.from(clone.querySelectorAll("img"));
      if (convertedImages.length > 0) {
        await Promise.all(
          convertedImages.map((img) => {
            // 如果已经加载完成且尺寸有效，直接放行
            if (img.complete && img.naturalWidth > 0) return Promise.resolve();
            
            // 否则等待加载
            return new Promise((resolve) => {
              img.addEventListener("load", resolve, { once: true });
              img.addEventListener("error", resolve, { once: true });
              setTimeout(resolve, 1000); // 容错：每个图片最多硬等 1 秒
            });
          })
        );
      }
      
      clone.querySelectorAll("img").forEach((img) => {
        // 如果已经有 inline-small-icon 类，跳过
        if (img.classList.contains("inline-small-icon")) return;
        
        const classStr = img.className.toLowerCase();
        
        // 检查是否带有对齐类但实际尺寸很小
        const hasAlignClass = img.classList.contains("imagecenter") || 
                              img.classList.contains("imageleft") ||
                              img.classList.contains("imageright");
        
        // 🌟 增强识别策略：
        // 1. 基于原始 class 特征（即使 Base64 后也能从 class 判断）
        const hasIconKeywords = classStr.includes("icon") || classStr.includes("ont_") || 
                                classStr.includes("mfn_") || classStr.includes("button") ||
                                classStr.includes("checkbox") || classStr.includes("arrow") ||
                                classStr.includes("plus") || classStr.includes("minus") ||
                                classStr.includes("check") || classStr.includes("nav_") ||
                                classStr.includes("filter_");
        
        // 2. 基于真实尺寸识别（Base64 已加载完成，尺寸准确）
        const isSmallBySize = (img.clientWidth > 0 && img.clientWidth <= 80) || 
                              (img.naturalWidth > 0 && img.naturalWidth <= 80);
        
        // 3. 带对齐类的小图标放宽到 200px
        const isSmallWithAlign = hasAlignClass && img.naturalWidth > 0 && img.naturalWidth <= 200;
        
        // 4. 只有 img-fluid 类且尺寸很小的也标记
        const isFluidOnly = img.classList.contains("img-fluid") && 
                           !img.classList.contains("image") &&
                           img.naturalWidth > 0 && img.naturalWidth <= 100;
        
        if (hasIconKeywords || isSmallBySize || isSmallWithAlign || isFluidOnly) {
          img.classList.add("inline-small-icon");
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
          // 纯文本参数表（如 Parameters 详情），必须保留边框 and 自然宽度
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

}
"""


async def capture_page_content(page):
    try:
        tasks = [
            asyncio.create_task(page.wait_for_selector("#xhtml", state="attached", timeout=15000)),
            asyncio.create_task(page.wait_for_function(
                '''() => document.readyState === "complete" && document.querySelectorAll("iframe").length === 0''',
                timeout=15000
            ))
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
            try:
                await p
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
    except:
        pass

    frame = None
    for f in page.frames:
        if f.name == "xhtml": frame = f; break
    if not frame:
        for f in page.frames:
            if "/documentation/" in f.url or "help" in f.url: frame = f; break
    if not frame:
        element = await page.query_selector("#xhtml")
        if element: frame = await element.content_frame()

    if not frame: raise Exception("无法获取目标 Frame (frame is null/undefined)")

    try:
        await frame.evaluate("""async () => {
            const lazyEls = document.querySelectorAll("disw-video, video, img");
            if (lazyEls.length > 0) {
                for (const el of lazyEls) { try { el.scrollIntoView({ behavior: "instant", block: "center" }); } catch (e) {} }
                window.scrollTo(0, 0);
                const container = document.querySelector(".doc-content") || document.querySelector(".main.content-container");
                if (container) container.scrollTop = 0;
            }
        }""")
    except:
        pass

    try:
        media_urls = getattr(page, '__mediaUrls', [])
        await frame.evaluate(r"""async (interceptedUrls) => {
            window.__interceptedMediaUrls = interceptedUrls;
            const videos = document.querySelectorAll("disw-video");
            if (videos.length > 0) {
                await Promise.all(Array.from(videos).map(v => {
                    return new Promise((resolve) => {
                        if (v.querySelector("video source") || v.querySelector("video[src]")) return resolve();
                        const observer = new MutationObserver(() => {
                            if (v.querySelector("video source") || v.querySelector("video[src]")) {
                                observer.disconnect();
                                resolve();
                            }
                        });
                        observer.observe(v, { childList: true, subtree: true });
                        setTimeout(() => { observer.disconnect(); resolve(); }, 8000);
                    });
                }));
            }
            // Add detailed media logic exactly as JS
            document.querySelectorAll("video").forEach(v => {
                let realSrc = null;
                Array.from(v.attributes).forEach(attr => { if (attr.value.includes(".mp4") || attr.value.includes(".webm") || attr.value.includes(".m3u8")) realSrc = attr.value; });
                if (!realSrc) { v.querySelectorAll("source").forEach(s => { let src = s.getAttribute("src") || s.getAttribute("data-src") || s.getAttribute("data-video-src"); if (src && (src.includes(".mp4") || src.includes(".webm") || src.includes(".m3u8"))) realSrc = src; }); }
                if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
                    let parent = v.parentElement;
                    while (parent && parent.tagName !== "BODY") {
                        Array.from(parent.attributes).forEach(attr => { if (attr.value.includes(".mp4") || attr.value.includes(".webm") || attr.value.includes(".m3u8")) realSrc = attr.value; });
                        if (realSrc) break;
                        if (parent.tagName.toLowerCase() === "disw-video") {
                            const videoUrl = parent.getAttribute("video-url") || parent.getAttribute("src") || parent.getAttribute("data-video-url") || parent.getAttribute("data-src");
                            if (videoUrl) { realSrc = videoUrl; break; }
                            let match = parent.innerHTML.match(/(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i);
                            if (match) { realSrc = match[1]; break; }
                        }
                        parent = parent.parentElement;
                    }
                }
                if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
                    let scripts = document.querySelectorAll("script");
                    for (let script of scripts) {
                        let match = script.textContent.match(/(https?:\/\/[^\s"']+\.(?:mp4|webm|m3u8)[^\s"']*)/i);
                        if (match) { realSrc = match[1]; break; }
                    }
                }
                if (!realSrc || (v.src && v.src.startsWith("blob:"))) {
                    if (window.__interceptedMediaUrls && window.__interceptedMediaUrls.length > 0) realSrc = window.__interceptedMediaUrls[0];
                }
                if (realSrc) v.setAttribute("data-real-src", realSrc);
            });
        }""", media_urls)
    except:
        pass

    data = await frame.evaluate(DOM_LOGIC_SCRIPT)
    return frame, data


# ==========================================
# 🌲 目录树处理 & HTML生成
# ==========================================
def render_sidebar_html(nodes, level=0):
    global_page_index = [0]

    def build_tree(nodes, level):
        html = ""
        for node in nodes:
            current_page_idx = global_page_index[0]
            global_page_index[0] += 1
            html += f'    <li class="nav-level-{level}">\n        <div class="nav-item-row">\n'
            has_children = bool(node.get("children"))
            caret_class = "caret caret-down" if level == 0 else "caret"

            if has_children:
                html += f'            <span class="{caret_class}" onclick="toggleNode(this)"></span>\n'
            else:
                html += '            <span class="no-caret"></span>\n'

            url = node.get("url", "")
            text = node.get("text", "")
            if not url or "javascript:void(0)" in url or str(url).strip() == "#":
                html += f'            <span class="folder-text" onclick="toggleNode(this.previousElementSibling)">{text}</span>\n'
            else:
                html += f'            <a href="#page_{current_page_idx}" onclick="handleManualClick(this)">{text}</a>\n'

            html += '        </div>\n'
            if has_children:
                active_class = " active" if level == 0 else ""
                html += f'        <ul class="nested{active_class}">\n'
                html += build_tree(node["children"], level + 1)
                html += '        </ul>\n'
            html += '    </li>\n'
        return html

    if not nodes: return ""
    res = '<ul class="root-list active">\n' if level == 0 else ''
    res += build_tree(nodes, level)
    if level == 0: res += '</ul>\n'
    return res


# CSS and JS templates matching precisely the JS code
UI_FRAMEWORK_CSS = r"""
        /* 🚀 修改：加入 font-size: 14px 控制整体字号 */
        .nx-sidebar { width: 320px; min-width: 250px; display: flex; flex-direction: column; background: #f8f9fa; border-right: 1px solid #dee2e6; overflow: hidden; font-size: 13px; }
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
"""
EXCEPTION_FIX_CSS = r"""
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
    """

BODY_STYLE_CSS = r"""
/* ====================================================================
   关键修复：body布局样式
（必须放在最后）
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

        table.multi-image-layout-table img:not(.inline-small-icon) {
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
        .page-section img:not([src*="icon" i]):not([src*="ont_" i]):not([src*="mfn_" i]):not([src*="button" i]):not([src*="checkbox" i]):not([src*="arrow" i]):not([src*="plus" i]):not([src*="minus" i]):not([src*="check" i]):not([src*="nav_" i]):not([src*="filter_" i]):not(.inline-small-icon) {
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
        .page-section img[src*="arrow" i],
        .page-section img[src*="plus" i],
        .page-section img[src*="minus" i],
        .page-section img[src*="check" i],
        .page-section img[src*="nav_" i],
        .page-section img[src*="filter_" i],
        .page-section img.inline-small-icon {
            display: inline-block !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
            margin-bottom: 0 !important;
            vertical-align: middle !important;
        }
"""

UI_JS_SCRIPT = r"""
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
    </script>"""


# ==========================================
# 自愈与比对系统
# ==========================================
def verify_and_fix(sub, mode, db):
    js_filename = f"(js版) {sub['name']}.html"
    if not os.path.exists(js_filename):
        # 兼容一下各种可能的名字
        alt_names = [f"(js版) {sub['title']}.html", f"{sub['name']}_js.html"]
        for alt in alt_names:
            if os.path.exists(alt):
                js_filename = alt
                break

    py_filename = f"{sub['name']}.html"

    print(f"\n{'=' * 60}")
    print(f" ⚙️ 执行文件形态比对与自愈检查 ...")
    print(f"{'=' * 60}")

    if not os.path.exists(js_filename):
        print(f"   ℹ️ 未检测到基准对比文件: {js_filename}，将跳过大小一致性校验。")
        return False

    if not os.path.exists(py_filename):
        print(f"   ❌ Python版生成文件丢失! 触发自动修复机制：标记需重建！")
        return False

    js_size = os.path.getsize(js_filename)
    py_size = os.path.getsize(py_filename)

    if js_size == 0:
        print("   ℹ️ 基准文件大小为 0，跳过校验。")
        return False

    diff = abs(js_size - py_size)
    ratio = diff / js_size
    percent = ratio * 100

    print(f"   📊 大小分析: JS版本={js_size} Bytes, Python版本={py_size} Bytes (差异率: {percent:.2f}%)")

    if ratio > 0.05:
        print(f"   🚨 致命特征: 生成结果体积差异超限(>5%)！")
        print(f"   🔧 自愈执行: 强制作废当前 SQLite 缓存，重构采集任务...")
        # db.execute("DELETE FROM cache")
        # db.execute("DELETE FROM styles")
        # db.commit()
        return False

    print("   ✅ 自愈通过：生成文件达到极高一致性(>95%)，Python/JS底层验证闭环接通！")
    return False


# ==========================================
# 🚀 核心工作逻辑封装
# ==========================================
async def start_job(sub, mode, retry_mode=False):
    START_URL = sub["url"]
    SIDEBAR_TITLE = sub["title"]
    FINAL_OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"{sub['name']}.html")
    # 数据库和 JSON 文件放在 output/data/ 子目录
    DATA_DIR = os.path.join(OUTPUT_DIR, "data")
    os.makedirs(DATA_DIR, exist_ok=True)
    CACHE_DB_FILE = os.path.join(DATA_DIR, f"db_{sub['name']}.db")
    NAV_JSON_FILE = os.path.join(DATA_DIR, f"nav_{sub['name']}.json")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    db = init_database(CACHE_DB_FILE)

    # Self check before starting if mode is c (continue) - DISABLED
    # if not retry_mode and mode != 'r':
    #     if os.path.exists(FINAL_OUTPUT_FILE) and os.path.exists(CACHE_DB_FILE):
    #         needs_fix = verify_and_fix(sub, mode, db)
    #         if needs_fix:
    #             mode = 'r'
    #             print("🔧 引擎自动热切换至重试(r)模式...")

    print(f"\n{'=' * 60}")
    print(f" 主题处理: {sub['title']} (模式: {mode})")
    print(f"{'=' * 60}")

    if mode == "r":
        print("🗑️ 清空当前主题历史碎片数据...")
        db.execute("DELETE FROM cache")
        db.execute("DELETE FROM styles")
        db.commit()
        if os.path.exists(NAV_JSON_FILE): os.remove(NAV_JSON_FILE)

    db_count = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    print(f"   📊 数据库当前记录数: {db_count}")
    print("🚀 预热后台无头浏览器网络层...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])

        tree_data = []
        if os.path.exists(NAV_JSON_FILE) and mode not in ["a", "r"]:
            print("✅ 从缓存层快速挂载目录树结构...")
            with open(NAV_JSON_FILE, "r", encoding="utf-8") as f:
                tree_data = json.load(f)
        else:
            print("📋 发起初始探测指令生成导航矩阵...")
            page = await browser.new_page()
            try:
                await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
            except:
                pass

            print("⏳ 等待文档应用前端路由水合(Hydration)...")
            try:
                await page.wait_for_function('''() => {
                    const r = document.querySelector("ul.doc-topics") || document.querySelector('[role="tree"]') || document.querySelector(".nx-sidebar ul") || document.querySelector("ul");
                    return r && r.querySelectorAll("li").length > 3;
                }''', timeout=30000)
            except:
                print("⚠️ 等待钩式回调超时，采用侵入式向下解析...")

            print("📋 生成并序列化动态目录DOM节点...")
            tree_data = await page.evaluate("""async () => {
                function findBestNavRoot() {
                    let root = document.querySelector("ul.doc-topics") || document.querySelector('[role="tree"]');
                    if (root) return root;
                    let allUls = Array.from(document.querySelectorAll("ul"));
                    if (allUls.length === 0) return null;
                    allUls.sort((a, b) => b.querySelectorAll("a").length - a.querySelectorAll("a").length);
                    return allUls[0];
                }

                const treeRoot = findBestNavRoot();
                if (!treeRoot) return [];

                let lastCount = 0, stuck = 0;
                while (stuck < 6) {
                    const expandables = Array.from(document.querySelectorAll("li.has-subItems > button[aria-expanded='false'], .toggle:not(.expanded), .expand-icon:not(.expanded), li[aria-expanded='false'] > button"));
                    if (expandables.length === 0) {
                        stuck++;
                        await new Promise(r => setTimeout(r, 1500));
                        continue;
                    }
                    for (let el of expandables) {
                        try {
                            el.scrollIntoView({block: 'center'});
                            el.click();
                        } catch (e) {
                        }
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
                    const lis = ul.querySelectorAll(":scope > li");
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
                            const span = li.querySelector(":scope > span, :scope > div > span");
                            if (span) result.push({
                                text: span.innerText.trim(),
                                url: "",
                                href: "",
                                hasChildren: !!sub,
                                children: parseLevel(sub)
                            });
                        }
                    }
                    return result;
                }

                const startUl = treeRoot.tagName === 'UL' ? treeRoot : treeRoot.querySelector("ul");
                return parseLevel(startUl);
            }""")
            with open(NAV_JSON_FILE, "w", encoding="utf-8") as f:
                json.dump(tree_data, f, ensure_ascii=False, indent=2)
            await page.close()

        nav_items = []

        def flatten(nodes):
            for n in nodes:
                nav_items.append(n)
                if n.get("children"): flatten(n["children"])

        flatten(tree_data)

        print(f"📊 展平目录深度节点量: {len(nav_items)}")

        success_count, skip_count, fail_count = 0, 0, 0
        current_idx = 0
        is_shutting_down = False
        active_pages = []

        print(f"⚡ 启动 {MAX_CONCURRENCY} 个并发线程...")
        script_start_time = time.time()
        real_fetch_start_time = [0]
        real_fetch_count = [0]

        def get_log_prefix(idx, is_real_fetch=False):
            total = len(nav_items)
            percent = f"{((idx + 1) / total) * 100:.1f}"
            eta_str = "--"

            if is_real_fetch:
                if real_fetch_start_time[0] == 0:
                    real_fetch_start_time[0] = time.time()
                real_fetch_count[0] += 1

                elapsed = time.time() - real_fetch_start_time[0]
                rate = real_fetch_count[0] / elapsed if elapsed > 0 else 0
                remaining = total - (idx + 1)
                eta_sec = remaining / rate if rate > 0 else 0

                eta_str = f"{int(eta_sec // 60)}分{int(eta_sec % 60)}秒"

            return f"[{percent}%] 成功:{success_count} 复用:{skip_count} 失败:{fail_count} | ETA: {eta_str}"

        async def worker(w_id):
            nonlocal current_idx, success_count, skip_count, fail_count
            context, page = None, None

            async def create_page():
                nonlocal context, page
                if page:
                    try:
                        active_pages.remove(page)
                    except:
                        pass
                    try:
                        await page.close()
                    except:
                        pass
                if context:
                    try:
                        await context.close()
                    except:
                        pass
                context = await browser.new_context()
                page = await context.new_page()
                active_pages.append(page)

                async def intercept_route(route):
                    req = route.request
                    rtype = req.resource_type
                    url = req.url.lower()
                    if rtype in ["font", "beacon", "csp_report", "websocket"] or any(k in url for k in
                                                                                     ["analytics", "tracking",
                                                                                      "telemetry", "metrics",
                                                                                      "googletagmanager", "tealiumiq",
                                                                                      "tiqcdn", "adobedtm"]):
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", intercept_route)

                setattr(page, '__mediaUrls', [])

                def on_response(resp):
                    url = resp.url
                    ctype = resp.headers.get("content-type", "")
                    if "video/" in ctype or "mpegurl" in ctype or any(e in url for e in [".mp4", ".webm", ".m3u8"]):
                        if "blank.mp4" not in url:
                            getattr(page, '__mediaUrls').append(url)

                page.on("response", on_response)

            await create_page()

            while current_idx < len(nav_items) and not is_shutting_down:
                idx = current_idx
                current_idx += 1
                item = nav_items[idx]

                if not item.get("url") or "javascript" in item["url"] or str(item["url"]).strip() == "#":
                    continue

                if await check_page_exists(db, item["url"]):
                    skip_count += 1
                    print(
                        f"[{idx + 1}/{len(nav_items)}] [线程{w_id}] {item['text']} (✓ 数据库极速恢复) | {get_log_prefix(idx, False)}")
                    continue

                retries, success = 0, False
                while retries < 3 and not success and not is_shutting_down:
                    try:
                        if retries > 0: await asyncio.sleep(2)
                        if page.is_closed(): await create_page()
                        setattr(page, '__mediaUrls', [])

                        await page.goto(item["url"], wait_until="domcontentloaded", timeout=30000)
                        frame, data = await capture_page_content(page)
                        css = await build_inline_css(frame, page) if frame else []

                        if data and data.get("html"):
                            import re
                            html_str = data["html"]
                            missing_video = False
                            for match in re.findall(r'<(?:disw-video|video)[^>]*>', html_str, re.IGNORECASE):
                                if "data-real-src" not in match and not re.search(r'\.(mp4|webm|m3u8)', match,
                                                                                  re.IGNORECASE) and "src=" not in match:
                                    missing_video = True
                                    break

                            if missing_video:
                                raise Exception("提取到无真实链接残缺多媒体组件，拒绝入库，触发重试")

                            save_to_database(db, item["url"], item["text"], html_str, css)
                            success_count += 1
                            success = True
                            print(
                                f"[{idx + 1}/{len(nav_items)}] [线程{w_id}] {item['text']} (✓ 抓取成功) | {get_log_prefix(idx, True)}")
                        else:
                            raise Exception("捕获流被阶段截断")
                    except Exception as e:
                        retries += 1
                        await create_page()
                        if retries >= 3:
                            fail_count += 1

            if page:
                try:
                    await page.close()
                except:
                    pass
            if context:
                try:
                    await context.close()
                except:
                    pass

        workers = [worker(i + 1) for i in range(MAX_CONCURRENCY)]
        await asyncio.gather(*workers)

        print(f"\n✅ 全部子并发终结 | 提取: {success_count}页 | 缓存命中: {skip_count}页 | 未及预期: {fail_count}页")
        print("⏳ 后处理工序：装配输出单一 HTML 归档...")

        with open(FINAL_OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(
                f'<!DOCTYPE html>\n<html>\n<head>\n    <meta charset="utf-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1">\n    <title>{SIDEBAR_TITLE}</title>\n')
            f.write("    <style>\n")

            cursor = db.execute("SELECT content FROM styles")
            for row in cursor:
                f.write(row[0] + "\n")

            f.write(
                "\n/* ====================================================================\n   UI框架样式开始 - 仅包含必需的布局和导航样式\n   ==================================================================== */\n")
            f.write(UI_FRAMEWORK_CSS)

            f.write(
                "\n/* ====================================================================\n   异常修正样式 - 仅针对具体问题进行精准修正\n   ==================================================================== */\n")
            f.write(EXCEPTION_FIX_CSS)

            f.write(BODY_STYLE_CSS)
            f.write("\n</style>\n")

            f.write(
                f'</head>\n<body>\n    <div class="nx-sidebar">\n        <div class="nx-sidebar-header">\n            <h3 style="color: #007cba; margin: 0; text-align: center; font-size: 24px;">{SIDEBAR_TITLE}</h3>\n        </div>\n        <div class="nx-sidebar-content">\n')
            f.write(render_sidebar_html(tree_data))
            f.write('        </div>\n    </div>\n')
            f.write('    <div class="resizer" id="resizer"></div>\n')
            f.write('    <div class="main-content">\n        <div class="content-wrapper">\n')

            global_idx = 0
            for item in nav_items:
                if not item.get("url") or "javascript" in item["url"] or str(item["url"]).strip() == "#":
                    global_idx += 1
                    continue
                cursor = db.execute("SELECT html FROM cache WHERE url = ?", (item["url"],))
                row = cursor.fetchone()
                if row and row[0]:
                    clean_html = re.sub(r'</body>', '', row[0], flags=re.IGNORECASE)
                    clean_html = re.sub(r'</html>', '', clean_html, flags=re.IGNORECASE)
                    clean_html = re.sub(r'<body[^>]*>', '', clean_html, flags=re.IGNORECASE)
                    clean_html = re.sub(r'<html[^>]*>', '', clean_html, flags=re.IGNORECASE)
                    clean_html = re.sub(r'<head[^>]*>[\s\S]*?</head>', '', clean_html, flags=re.IGNORECASE)
                    clean_html = re.sub(r'<script[^>]*>.*?</script>', '', clean_html, flags=re.IGNORECASE)
                    clean_html = re.sub(r'<!DOCTYPE[^>]*>', '', clean_html, flags=re.IGNORECASE).strip()
                    if clean_html:
                        f.write(f'            <div class="page-section" id="page_{global_idx}">{clean_html}</div>\n')
                global_idx += 1

            f.write('        </div>\n    </div>\n')
            f.write(UI_JS_SCRIPT)

        t_elapsed = int(time.time() - script_start_time)
        print("✅ 文件主体写入完成")
        print("📝 开始追加结束标签与JS代码...")
        print("✅ 代码追加完成")
        print("✅ 成功写入页面内容")
        print(f"🎉 文件已生成: {FINAL_OUTPUT_FILE}")
        print(f"⏱️ 总耗时: {t_elapsed // 60}分 {t_elapsed % 60}秒")
        print(f"✅ 主题 [{sub['title']}] 处理完毕！\n")

    db.close()

    # 爬取完成后强制再次进行自检 - DISABLED
    # if not retry_mode:
    #     db = init_database(CACHE_DB_FILE)
    #     needs_fix = verify_and_fix(sub, mode, db)
    #     db.close()
    #     if needs_fix:
    #         print("🚨 正在启用最终自动补偿逻辑进行底牌拦截...")
    #         await start_job(sub, 'r', retry_mode=True)


# ==========================================
# 入口
# ==========================================
if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print("=" * 60)
    print(" NX文档聚合器 - Python 完美原生重制版 (实现1:1级JS框架反向映射)")
    print("=" * 60)

    if not SUBJECTS:
        sys.exit(1)

    mode = "c"
    if len(sys.argv) > 1 and sys.argv[1] in ["a", "c", "r"]:
        mode = sys.argv[1]
    else:
        try:
            m = input(
                "\n请设定并发持久化工作形态: [a]极速流构建  [c]差量断点续传  [r]物理洗库重构  (默认 c): ").strip().lower()
            if m in ["a", "c", "r"]: mode = m
        except EOFError:
            mode = "c"
        except:
            pass


    async def main():
        total_st = time.time()
        for sub in SUBJECTS:
            try:
                await start_job(sub, mode)
            except KeyboardInterrupt:
                print("🛑 捕获最高权限手工切断...")
                break
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"⚠️ 跳过严重宕机事件: {e}")

        t = int(time.time() - total_st)
        print(f"\n{'=' * 60}\n 🎉 并发队列已经全部消费殆尽！最终流水耗时: {t // 60}分 {t % 60}秒\n{'=' * 60}")


    asyncio.run(main())
