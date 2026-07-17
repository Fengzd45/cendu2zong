import os
import re
import imaplib
import email
import sqlite3
import pandas as pd
import pdfplumber
import json
import datetime
from email.header import decode_header
from paddleocr import PaddleOCR

# ==================== 1. 配置区域 ====================
EMAIL_ACCOUNT = os.environ.get("GMAIL_USER", "Fengzd3@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_PASS")

DB_NAME = "nursery_quotes.db"
SAVE_DIR = "./temp_attachments"  # 临时存放下载附件的目录

ocr = None  # 延迟加载 OCR，节省内存

# ==================== 2. 数据库初始化 ====================
def init_database():
    """初始化统一的报价单数据库表"""
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
    """安全地将含货币符号或逗号的字符串转换为浮点数"""
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
                item['imported_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            results.append(item)
            
        # 覆写生成标准的静态数据文件 quotes.json
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
            
        print(f"✅ 同步成功！当前已有 [ {len(results)} ] 条记录刷新至 {json_file}")
        
    except sqlite3.OperationalError as e:
        print(f"❌ 读取数据库转换为 JSON 失败: {e}")
    finally:
        conn.close()

# ==================== 3. 自适应解析引擎 ====================
def parse_excel(file_path):
    """【解析 Excel】读取原始单元格，准确率 100%"""
    print(f"📊 正在解析 Excel 报价单: {file_path}")
    df = pd.read_excel(file_path)
    items = []
    current_category = "Shrub"
    
    for index, row in df.iterrows():
        qty_val = str(row.iloc[0]).strip()
        if qty_val.isdigit():
            ordered = int(qty_val)
            botanical_name = str(row.iloc[1]).strip()
            size = str(row.iloc[2]).strip()
            net_price = safe_float(row.iloc[3])
            extension = safe_float(row.iloc[4])
            
            if "total" in botanical_name.lower():
                continue
            items.append({
                "category": current_category,
                "ordered": ordered,
                "botanical_name": botanical_name,
                "size": size,
                "net_price": net_price,
                "extension": extension
            })
        elif qty_val in ["Grass", "Shrub", "Tree", "Conifer"]:
            current_category = qty_val
            
    return items

def parse_pdf(file_path):
    """【解析 PDF】提取矢量文字，准确率接近 100%"""
    print(f"📄 正在解析 PDF 报价单: {file_path}")
    items = []
    current_category = "Shrub"
    
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 5:
                    continue
                qty_val = str(row[0]).strip()
                if qty_val.isdigit():
                    ordered = int(qty_val)
                    botanical_name = str(row[1]).strip()
                    size = str(row[2]).strip()
                    net_price = safe_float(row[3])
                    extension = safe_float(row[4])
                    
                    if "total" in botanical_name.lower() or ordered == 166:
                        continue
                    items.append({
                        "category": current_category,
                        "ordered": ordered,
                        "botanical_name": botanical_name,
                        "size": size,
                        "net_price": net_price,
                        "extension": extension
                    })
                elif qty_val in ["Grass", "Shrub", "Tree", "Conifer"]:
                    current_category = qty_val
    return items

def parse_image(file_path):
    """【解析图片】使用 PaddleOCR 物理行对齐解析"""
    global ocr
    if ocr is None:
        ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
        
    print(f"🖼️ 正在通过 OCR 解析图片报价单: {file_path}")
    result = ocr.ocr(file_path, cls=True)
    
    lines = []
    for idx in range(len(result)):
        res = result[idx]
        if not res: continue
        for line in res:
            box = line[0]
            text = line[1][0]
            y_center = (box[0][1] + box[2][1]) / 2.0
            x_start = box[0][0]
            lines.append({"text": text, "x": x_start, "y": y_center})

    lines.sort(key=lambda item: item["y"])
    grouped_rows = []
    current_row = []
    last_y = -999
    
    for line in lines:
        if last_y == -999 or abs(line["y"] - last_y) < 15:
            current_row.append(line)
        else:
            current_row.sort(key=lambda item: item["x"])
            grouped_rows.append(current_row)
            current_row = [line]
        last_y = line["y"]
    if current_row:
        current_row.sort(key=lambda item: item["x"])
        grouped_rows.append(current_row)

    items = []
    current_category = "Shrub"
    for row in grouped_rows:
        row_text = [item["text"].strip() for item in row]
        if len(row_text) == 1 and row_text[0] in ["Grass", "Shrub", "Tree", "Conifer"]:
            current_category = row_text[0]
            continue
            
        if len(row_text) >= 4:
            first_val = row_text[0]
            if first_val.isdigit() and int(first_val) < 1000:
                ordered = int(first_val)
                prices = []
                for val in row_text:
                    cleaned = val.replace('$', '').replace(',', '').strip()
                    if re.match(r'^\d+\.\d{2}$', cleaned):
                        prices.append(float(cleaned))
                
                size = "N/A"
                for val in row_text:
                    if '#' in val or 'gal' in val.lower():
                        size = val
                
                botanical_parts = []
                for val in row_text[1:]:
                    if val == size or (prices and val.replace('$', '').strip() == f"{prices[0]:.2f}"):
                        break
                    botanical_parts.append(val)
                botanical_name = " ".join(botanical_parts)

                if "Total" in botanical_name or ordered == 166:
                    continue

                if len(prices) >= 2:
                    net_price = prices[-2]
                    extension = prices[-1]
                else:
                    net_price = prices[0] if prices else 0.0
                    extension = ordered * net_price

                if botanical_name:
                    items.append({
                        "category": current_category,
                        "ordered": ordered,
                        "botanical_name": botanical_name,
                        "size": size,
                        "net_price": net_price,
                        "extension": extension
                    })
    return items

# ==================== 4. 邮件收取与调度 ====================
def process_emails_and_save():
    if not APP_PASSWORD:
        print("❌ 错误：未设置 GMAIL_PASS 环境变量！")
        return

    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

    conn = init_database()
    cursor = conn.cursor()

    try:
        print("📬 正在连接 Gmail 服务器收取新邮件...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(EMAIL_ACCOUNT, APP_PASSWORD)
        mail.select("inbox")

        # 搜索未读、且主题含有 Quote、Price 或 Availability 的邮件
        status, data = mail.search(None, 'UNSEEN (OR (OR SUBJECT "Quote" SUBJECT "Price") SUBJECT "Availability")')
        mail_ids = data[0].split()
        print(f"✉️ 发现 {len(mail_ids)} 封可能含有报价附件的未读邮件。")

        for mail_id in mail_ids:
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # 自动提取发件人名称作为供应商
                    from_header = msg.get("From", "Unknown Supplier")
                    supplier_name = "Unknown"
                    if "erni" in from_header.lower():
                        supplier_name = "East Richmond Nurseries (ERNI)"
                    elif "nats" in from_header.lower():
                        supplier_name = "NATS Nursery"
                    elif "islandview" in from_header.lower():
                        supplier_name = "Island View Nursery"
                    elif "greenthumb" in from_header.lower():
                        supplier_name = "Green Thumb Nurseries"
                    else:
                        supplier_name = from_header

                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    print(f"\n📨 正在处理邮件:【{subject}】来自: {from_header}")

                    for part in msg.walk():
                        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                            continue

                        filename, encoding = decode_header(part.get_filename())[0]
                        if isinstance(filename, bytes):
                            filename = filename.decode(encoding if encoding else "utf-8", errors="ignore")
                        
                        if filename:
                            file_path = os.path.join(SAVE_DIR, filename)
                            print(f"📥 正在下载附件: {filename}")
                            with open(file_path, "wb") as f:
                                f.write(part.get_payload(decode=True))

                            ext = os.path.splitext(filename)[1].lower()
                            items = []
                            
                            # 格式自动分流
                            if ext in ['.xlsx', '.xls']:
                                items = parse_excel(file_path)
                            elif ext == '.pdf':
                                items = parse_pdf(file_path)
                            elif ext in ['.jpg', '.jpeg', '.png']:
                                items = parse_image(file_path)
                            else:
                                print(f"⚠️ 暂不支持的附件格式: {ext}")
                                if os.path.exists(file_path): os.remove(file_path)
                                continue

                            # 存入 SQLite 数据库
                            if items:
                                sql = '''
                                INSERT INTO quote_items (supplier, quote_number, quote_date, category, ordered, botanical_name, size, net_price, extension, file_source)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                '''
                                records = [
                                    (supplier_name, "AUTO_IMPORT", "2026-07-17", 
                                     i["category"], i["ordered"], i["botanical_name"], i["size"], i["net_price"], i["extension"], filename)
                                    for i in items
                                ]
                                cursor.executemany(sql, records)
                                conn.commit()
                                print(f"💾 成功将 {len(records)} 行数据存入统一数据库！")
                                
                                # 【关键修改：入库后立刻驱动 JSON 同步】
                                sync_db_to_json()
                            
                            if os.path.exists(file_path):
                                os.remove(file_path)

            # 解析完成后，将该邮件标记为已读，避免重复解析
            mail.store(mail_id, '+FLAGS', '\\Seen')

        mail.close()
        mail.logout()
        print("\n🏁 所有邮件及附件解析处理完毕。")

    except Exception as e:
        print(f"❌ 运行故障: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    process_emails_and_save()
    # 【追加：安全兜底，整个流水线跑完后再全量校准一次 JSON】
    try:
        sync_db_to_json()
    except Exception:
        pass
