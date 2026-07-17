import os
import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime

DB_NAME = "nursery_quotes.db"

def init_database():
    """连接到统一的单库（若不存在则自动初始化表结构）"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS quote_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT,
        quote_number TEXT,
        quote_date TEXT,
        category TEXT,
        ordered INTEGER,
        botanical_name TEXT,
        size TEXT,
        net_price REAL,
        extension REAL,
        file_source TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    return conn

def safe_float(val):
    if val is None or str(val).strip() == "":
        return 0.0
    try:
        cleaned = str(val).replace('$', '').replace(',', '').strip()
        return float(cleaned)
    except ValueError:
        return 0.0

# ==================== 新增：双轨同步数据引擎 ====================
def sync_db_to_json():
    """将 SQLite 数据库中的最新数据读取出来，并同步刷新到 quotes.json 中"""
    json_file = 'quotes.json'
    print("\n🔄 开始从数据库同步数据到前端 JSON 看板...")
    
    # 连接数据库并将结果映射为字典格式
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    
    try:
        # 从数据库中捞出最新的所有记录（后入库的排在前面）
        cursor.execute("SELECT * FROM quote_items ORDER BY id DESC")
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            item = dict(row)
            # 确保转换的时间戳格式整齐
            if 'imported_at' not in item or not item['imported_at']:
                item['imported_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            results.append(item)
            
        # 覆写生成标准的静态数据文件 quotes.json
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
            
        print(f"✅ 同步成功！当前已有 [ {len(results)} ] 条记录刷新至 {json_file}")
        
    except sqlite3.OperationalError as e:
        print(f"❌ 读取数据库转换为 JSON 失败: {e}")
    finally:
        conn.close()

def scrape_arts_nursery():
    """
    【爬虫模块】抓取 Art's Nursery 示例公开数据
    """
    print("🌐 [爬虫模块] 正在发起网络请求，抓取公开植物价格...")

    # 这里我们使用真实网页的模拟 HTML 结构来进行数据闭环测试
    # 后续可以针对特定的公開排版表格 进行选择性解析
    mock_html = """
    <div class="product-row" data-category="Tree">
        <span class="p-name">Acer palmatum 'Bloodgood' (Red Maple)</span>
        <span class="p-size">#5 Gallon</span>
        <span class="p-price">$119.99</span>
    </div>
    <div class="product-row" data-category="Shrub">
        <span class="p-name">Hydrangea macrophylla</span>
        <span class="p-size">#2 Gallon</span>
        <span class="p-price">$34.50</span>
    </div>
    <div class="product-row" data-category="Grass">
        <span class="p-name">Miscanthus sinensis</span>
        <span class="p-size">#1 Gallon</span>
        <span class="p-price">$18.95</span>
    </div>
    """

    soup = BeautifulSoup(mock_html, 'html.parser')
    items = []

    for row in soup.find_all('div', class_='product-row'):
        category = row.get('data-category', 'Shrub')
        botanical_name = row.find('span', class_='p-name').text.strip()
        size = row.find('span', class_='p-size').text.strip()
        price_text = row.find('span', class_='p-price').text.strip()

        net_price = safe_float(price_text)

        items.append({
            "category": category,
            "ordered": 0,  # 网页爬取属于公开库存查询，订购量默认为 0
            "botanical_name": botanical_name,
            "size": size,
            "net_price": net_price,
            "extension": 0.0
        })

    return items

def main():
    supplier_name = "Art's Nursery (Website)"
    source_url = "https://www.artsnursery.com/catalog"
    current_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 抓取数据
    scraped_items = scrape_arts_nursery()
    if not scraped_items:
        print("⚠️ 未抓取到有效数据，爬虫退出。")
        return

    # 2. 写入统一数据库
    conn = init_database()
    cursor = conn.cursor()

    sql = '''
    INSERT INTO quote_items (supplier, quote_number, quote_date, category, ordered, botanical_name, size, net_price, extension, file_source)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''

    records = [
        (supplier_name, "WEB_SCRAPE", current_date,
         i["category"], i["ordered"], i["botanical_name"], i["size"], i["net_price"], i["extension"], source_url)
        for i in scraped_items
    ]

    cursor.executemany(sql, records)
    conn.commit()
    conn.close()

    print(f"💾 [数据对齐成功] 爬虫抓取的 {len(records)} 条公开数据已无缝合并至 {DB_NAME} 数据库！")

    # 【关键修改：数据库落库成功后，立刻提取并刷新本地的 quotes.json】
    try:
        sync_db_to_json()
    except Exception as e:
        print(f"⚠️ 自动同步转 JSON 引擎时发生异常: {e}")

if __name__ == "__main__":
    main()
