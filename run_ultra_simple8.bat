@echo off
chcp 65001 >nul
echo ================================================================================
echo 🚀 ultra_simple8.py 增强版爬虫 - 批量处理模式
echo ================================================================================
echo.
echo ✨ 已集成功能:
echo   - 视频提取引擎 (5层策略)
echo   - 表格精准分类 (6种类型)
echo   - 小图标保护机制
echo   - 代码块清理
echo   - CSS URL 绝对化
echo   - 播放器修复
echo   - 批量处理 download_list.txt 中的所有主题
echo.
echo 📋 配置文件: download_list.txt
echo.
python test_download_list.py
echo.
echo ⚙️  配置检查:
python -c "import ast; tree = ast.parse(open('ultra_simple8.py', encoding='utf-8').read()); print('✅ Python 语法检查通过')"
echo.
echo ================================================================================
echo 按任意键开始运行...
pause >nul
echo.
echo ▶️  正在启动爬虫...
echo ================================================================================
python ultra_simple8.py
echo.
echo ================================================================================
echo 爬虫执行完毕！
echo ================================================================================
pause
