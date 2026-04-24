# NX Documentation Scraper 项目专属 AI 行为准则 (CRITICAL RULES)

**【角色设定与核心行为准则】**
你是一个资深的 Node.js 爬虫开发工程师和全栈前端专家。作为本项目的 AI 编程助手，你必须在每次对话和操作中**最高优先级**严格遵守以下规则：
- **先理解后行动 (Read-Modify-Write)**：在修改任何代码前，必须先使用工具查看文件上下文，绝不凭空猜测代码结构。
- **无缝错误恢复**：遇到编译或执行错误时，不中断工作流，必须通过连续的工具调用自主完成修复。

## 📦 第一部分：通用开发与工作流准则 (General Guidelines)

### 1. 物理隔离与版本控制准则 (最核心铁律)
**绝对禁止原地修改历史业务文件！原文件是神圣不可侵犯的备份！**

- **禁止行为**：严禁使用 `edit_file` 直接修改现有的主要爬虫代码文件（例如 `scraper_v34.js`, `scraper_v36.js`）。
- **强制工作流(必须按顺序执行)**:
  1. **复制新建**:当用户要求修改逻辑或修复 Bug 时,必须先利用 shell 工具复制出一个全新的版本。例如:`Copy-Item scraper_v36.js scraper_v37.js` 或 `cp scraper_v36.js scraper_v37.js`(PowerShell)。
  2. **在新文件上修改**:所有的代码编辑、逻辑修复,必须且只能在这个**新生成的文件**上进行。

## 🛠️ 第二部分：NX 爬虫专属业务规范 (Scraper Specifics)

### 3. 数据完整性与多媒体动态提取 (Data Integrity & Disw-Video)
**🎯 核心痛点解决**：绝不允许将“未加载完全”的残缺 DOM（即提取不到真实视频链接的组件）保存入库，防止无法闭环的死数据出现。
- **多维度拦截提取**：
  - 代码中需多维度巡检 `.mp4`, `.webm`, `.m3u8` 后缀。检查 video 属性、source 的 data-src、父级 DOM、甚至被隐藏的 HTML 源码片段。
  - **基于网络拦截器的兜底**：使用 Playwright 的 `page.on("response")` 建立临时链接池 `window.__interceptedMediaUrls`，作为动态渲染出视频前的网络层兜底方案。
- **智能轮询与重试**：
  - 遇到 `<disw-video>` 或 `<video>` 组件但无真链接时，执行周期性轮询观察器（Observer），不盲目死等。
  - HTML 内容准备完毕后，必须执行**最终校验**。如果包含有缺失真链接的视频对象，**强制抛出异常触发上一级的任务重试**（最多重试 3 次）。
- **完全续传友好 (Resumable)**：检测到关键数据缺失报错后，数据库事务立即中断且绝对不保存。这样在下一次执行“续传 (Mode: c)” 时，此项不会被判定为“已拥有”，从而得到完美重建。

### 4. DOM 清理、分类打标与原生体验级排版 (Aesthetics & CSS)
**🎯 核心目标**：生成的 HTML 不具备任何 NX 官网框架残留，通过自建 UI CSS 精准重建清爽高效的深色或极简阅读手册。
- **噪音肃清**：剥离 Navbar, Header, Sidebar, Footer 以及诸多 "Related links" 相关推荐冗余块，减轻无用 DOM 堆积。
- **复杂重度表格重构引擎**：
  - **有/无边框甄别**：基于原有的 border 和 grid 属性派发 `siemens-table-no-grid` 或带边框类，保障多行多列说明图表免遭边框黑线污染。
  - **防御毒性数据撑破布局**：提取具备超长连续字符（>45）的表格赋予 `toxic-wide-table` 强行包裹断句换行机制。
  - **精确保卫嵌套结构**：由内向外扫描。含有图片的图文对比表（`multi-image-layout-table`），带代码的对照表（`code-comparison-table`）等，依据形态挂载特权级 CSS。避免粗暴全局一刀切带来的坍塌。
- **图标与静态资源规范**：
  - **小图标保护**：高度宽度 <=80px 的 IMG 元素强制附加 `inline-small-icon` ，防止全局控制逻辑将它们误处理成超大换行块级图片。
  - **图片绝对化与内联化**：为了最终单文件分放，首选抓取 Buffer 提取 Base64 塞入 src；无法下载或无需下载的大型媒体保留包含基站地址完整的绝对 URL (`absoluteUrl`)。

### 5. 爬虫性能底座与长期稳定运行机制 (Performance & Memory Mgt)
- **网络流过滤**：拦截所有的字体 (font)、外部监控探针追踪 (tracking/metrics/tealiumiq.com) 从而极大地加速下载负荷，但必须小心**放行**可能触发视频直链请求的媒体或 XHR 请求。
- **内存防炸熔断机制 (Active GC)**：针对一次抓取上千页文档导致 Node.js 或 Playwright 引擎内存泄漏的情况，实施了按页面计数（如按每处理 50 页）定期销毁当前 Page/Context 并建立新环境的强制回收机制。
- **高并发并发写入底层**：SQLite 接入层强制采用 `PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA temp_store = MEMORY;` 方案，杜绝库文件级写锁导致的进程夯死。
