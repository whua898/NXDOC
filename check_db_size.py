import sqlite3
import os

db_path = "NX12_pages.db"
cache_dir = "NX12_pages"

def check_db():
    if not os.path.exists(db_path):
        print(f"数据库 {db_path} 不存在")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT count(*) FROM cache")
    count = cursor.fetchone()[0]
    
    cursor.execute("SELECT title, html FROM cache LIMIT 5")
    rows = cursor.fetchall()
    
    dirty_count = 0
    total_len = 0
    
    print(f"数据库总记录数: {count}")
    
    cursor.execute("SELECT html FROM cache")
    all_rows = cursor.fetchall()
    
    for row in all_rows:
        html = row[0]
        total_len += len(html)
        if 'doc-sidebar' in html or 'id="doc-sidebar"' in html:
            dirty_count += 1
            
    print(f"数据库 HTML 总大小: {total_len / 1024 / 1024:.2f} MB")
    print(f"包含侧边栏 (doc-sidebar) 的页面数: {dirty_count}")
    
    conn.close()

def check_cache_dir():
    if not os.path.exists(cache_dir):
        print(f"缓存目录 {cache_dir} 不存在")
        return

    files = os.listdir(cache_dir)
    print(f"缓存目录文件数: {len(files)}")
    
    total_size = 0
    for f in files:
        fp = os.path.join(cache_dir, f)
        total_size += os.path.getsize(fp)
        
    print(f"缓存目录总大小: {total_size / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    print("--- 检查数据库 ---")
    check_db()
    print("\n--- 检查文件缓存 ---")
    check_cache_dir()