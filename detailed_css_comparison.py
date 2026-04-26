#!/usr/bin/env python3
"""
详细对比 JS 版和 Python 版 HTML 文件的 CSS 差异
"""
import re
from pathlib import Path

def extract_css(html_content):
    """提取 HTML 中的 CSS 内容"""
    match = re.search(r'<style>(.*?)</style>', html_content, re.DOTALL)
    if match:
        return match.group(1)
    return ""

def extract_inline_styles(html_content):
    """提取所有 inline style 属性"""
    return re.findall(r'style="([^"]*)"', html_content)

def extract_img_tags(html_content):
    """提取所有 img 标签"""
    return re.findall(r'<img[^>]+>', html_content, re.IGNORECASE)

def analyze_small_icons(img_tags):
    """分析小图标的处理情况"""
    icons_with_class = []
    icons_without_class = []
    
    for img in img_tags:
        has_inline_class = 'inline-small-icon' in img
        src_match = re.search(r'src="([^"]+)"', img)
        src = src_match.group(1) if src_match else ""
        
        # 检查是否是小图标（基于 URL 特征）
        is_icon_by_url = any(keyword in src.lower() for keyword in ['icon', 'ont_', 'mfn_', 'button', 'checkbox'])
        
        info = {
            'src': src[:80] + '...' if len(src) > 80 else src,
            'has_class': has_inline_class,
            'is_icon_by_url': is_icon_by_url,
            'full_tag': img[:200]
        }
        
        if has_inline_class:
            icons_with_class.append(info)
        else:
            icons_without_class.append(info)
    
    return icons_with_class, icons_without_class

def compare_css_rules(js_css, py_css):
    """对比 CSS 规则差异"""
    js_rules = set(re.findall(r'([^{]+\{[^}]+\})', js_css))
    py_rules = set(re.findall(r'([^{]+\{[^}]+\})', py_css))
    
    only_in_js = js_rules - py_rules
    only_in_py = py_rules - js_rules
    common = js_rules & py_rules
    
    return {
        'only_in_js': only_in_js,
        'only_in_py': only_in_py,
        'common_count': len(common),
        'js_total': len(js_rules),
        'py_total': len(py_rules)
    }

def main():
    subjects = [
        "NX12_后处理配置器",
        "NX2506_后处理配置器", 
        "NX2512_后处理配置器",
    ]
    
    print("=" * 80)
    print("JS 版 vs Python 版 HTML 文件深度对比分析")
    print("=" * 80)
    
    for subject in subjects:
        js_file = Path(f"(js版) {subject}.html")
        py_file = Path(f"{subject}.html")
        
        if not js_file.exists() or not py_file.exists():
            print(f"\n⚠️ 跳过 {subject}: 文件不存在")
            continue
        
        print(f"\n{'=' * 80}")
        print(f"📊 主题: {subject}")
        print(f"{'=' * 80}")
        
        # 读取文件
        with open(js_file, 'r', encoding='utf-8') as f:
            js_html = f.read()
        with open(py_file, 'r', encoding='utf-8') as f:
            py_html = f.read()
        
        # 文件大小对比
        js_size = len(js_html)
        py_size = len(py_html)
        diff_ratio = abs(js_size - py_size) / js_size * 100
        
        print(f"\n📏 文件大小:")
        print(f"   JS 版:     {js_size:,} bytes ({js_size/1024/1024:.2f} MB)")
        print(f"   Python 版: {py_size:,} bytes ({py_size/1024/1024:.2f} MB)")
        print(f"   差异率:    {diff_ratio:.2f}%")
        
        # 提取 CSS
        js_css = extract_css(js_html)
        py_css = extract_css(py_html)
        
        print(f"\n🎨 CSS 内容对比:")
        print(f"   JS 版 CSS 长度:     {len(js_css):,} 字符")
        print(f"   Python 版 CSS 长度: {len(py_css):,} 字符")
        print(f"   差异:               {len(py_css) - len(js_css):+,} 字符")
        
        # CSS 规则对比
        css_diff = compare_css_rules(js_css, py_css)
        print(f"\n📋 CSS 规则统计:")
        print(f"   JS 版规则数:     {css_diff['js_total']}")
        print(f"   Python 版规则数: {css_diff['py_total']}")
        print(f"   共同规则数:      {css_diff['common_count']}")
        print(f"   仅 JS 版有:      {len(css_diff['only_in_js'])} 条")
        print(f"   仅 Python 版有:  {len(css_diff['only_in_py'])} 条")
        
        if css_diff['only_in_js']:
            print(f"\n   ⚠️ 仅 JS 版有的 CSS 规则（前 5 条）:")
            for i, rule in enumerate(list(css_diff['only_in_js'])[:5], 1):
                print(f"   {i}. {rule[:150]}...")
        
        if css_diff['only_in_py']:
            print(f"\n   ⚠️ 仅 Python 版有的 CSS 规则（前 5 条）:")
            for i, rule in enumerate(list(css_diff['only_in_py'])[:5], 1):
                print(f"   {i}. {rule[:150]}...")
        
        # 图片标签对比
        js_imgs = extract_img_tags(js_html)
        py_imgs = extract_img_tags(py_html)
        
        print(f"\n🖼️  图片标签统计:")
        print(f"   JS 版图片数:     {len(js_imgs)}")
        print(f"   Python 版图片数: {len(py_imgs)}")
        
        # 小图标分析
        js_with_class, js_without_class = analyze_small_icons(js_imgs)
        py_with_class, py_without_class = analyze_small_icons(py_imgs)
        
        print(f"\n🔍 小图标识别分析:")
        print(f"   JS 版带 inline-small-icon 类:    {len(js_with_class)} 个")
        print(f"   JS 版不带该类:                   {len(js_without_class)} 个")
        print(f"   Python 版带 inline-small-icon 类: {len(py_with_class)} 个")
        print(f"   Python 版不带该类:                {len(py_without_class)} 个")
        
        # 找出 Python 版缺少类的小图标
        missing_class = []
        for py_icon in py_without_class:
            if py_icon['is_icon_by_url']:
                # 检查是否在 JS 版中有类
                for js_icon in js_with_class:
                    if js_icon['src'] == py_icon['src']:
                        missing_class.append(py_icon)
                        break
        
        if missing_class:
            print(f"\n   ❌ Python 版未正确添加类的小图标（前 5 个）:")
            for i, icon in enumerate(missing_class[:5], 1):
                print(f"   {i}. {icon['src']}")
        
        # 检查是否有明显的 CSS 缺失
        critical_selectors = [
            '.inline-small-icon',
            'img:not([src*="icon"',
            'img[src*="ont_"',
            'img[src*="mfn_"',
        ]
        
        print(f"\n🔑 关键 CSS 选择器检查:")
        for selector in critical_selectors:
            js_has = selector in js_css
            py_has = selector in py_css
            status = "✅" if js_has == py_has else "❌"
            print(f"   {status} '{selector[:40]}': JS={'✓' if js_has else '✗'}, Py={'✓' if py_has else '✗'}")
    
    print("\n" + "=" * 80)
    print("✅ 对比分析完成")
    print("=" * 80)

if __name__ == "__main__":
    main()
