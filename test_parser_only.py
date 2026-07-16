import sqlite3

def run_db_test():
    print("=== [步骤 1: 本地数据库与写入测试] ===")
    try:
        conn = sqlite3.connect("test_nursery.db")
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
            extension REAL
        )
        ''')
        conn.commit()
        print("✅ 测试数据库创建成功。")
        
        cursor.execute('''
        INSERT INTO quote_items (supplier, quote_number, quote_date, category, ordered, botanical_name, size, net_price, extension)
        VALUES ('TEST_NURSERY', '000001', '2026-05-20', 'Shrub', 10, 'Test Plant', '#2', 10.0, 100.0)
        ''')
        conn.commit()
        
        cursor.execute("SELECT * FROM quote_items")
        row = cursor.fetchone()
        print(f"✅ 成功读取测试记录 -> 品名: {row[6]}, 合计: ${row[9]}")
        conn.close()
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")

if __name__ == "__main__":
    run_db_test()
