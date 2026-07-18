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
# 💡 修复：更换为云端已安装的 easyocr 库，彻底消灭导入错误
import easyocr

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

# ==================== 双轨同步数据引擎 ====================
def sync_db_to_json():
    """将 SQLite 数据库中的最新数据读取出来，强制以英文标准字段刷新到 quotes.json 中"""
    json_file = 'quotes.json'
    print("\n🔄 开始从数据库同步数据到前端 JSON 看板...")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        # 从数据库中捞出最新的所有记录
        cursor.execute("SELECT id, supplier, quote_number, quote_date, category, ordered, botanical_name, size, net_price, extension, file_source, imported_at FROM quote_items ORDER BY id DESC")
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            # 💡 核心修复：显式强制映射为前端需要的英文键名，彻底防止环境干扰变成本地化中文键名
            item = {
                "id": row[0],
                "supplier": row[1],
                "quote_number": row[2],
                "quote_date": row[3],
                "category": row[4],
                "ordered": row[5],
                "botanical_name": row[6],
                "size": row[7],
                "net_price": row[8],
                "extension": row[9],
                "file_source": row[10],
                "imported_at": row[11] if row[11] else datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
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
    """【解析 PDF】提取矢量文字，遇到纯图片 PDF 自动转图交给 OCR"""
    print(f"📄 正在解析 PDF 报价单: {file_path}")
    items = []
    current_category = "Shrub"
    is_pure_image = True
    
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            
            # 如果能提取出表格和文本，说明是原生电子版 PDF
            is_pure_image = False
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
                    
    # 💡 核心兜底逻辑：如果 PDF 提取不出文字（说明是照片转的假 PDF）
    if is_pure_image or not items:
        print("⚠️ 检测到该 PDF 可能是纯图片/扫描件，正在启动‘假PDF转图’并调用 OCR 识别...")
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    temp_img_path = f"{file_path}_page_{i}.png"
                    # 将 PDF 页面转换为图片保存
                    page.to_image(resolution=200).original.save(temp_img_path)
                    # 递交给 OCR 解析
                    ocr_items = parse_image(temp_img_path)
                    items.extend(ocr_items)
                    if os.path.exists(temp_img_path):
                        os.remove(temp_img_path)
        except Exception as e:
            print(f"❌ 假 PDF 转换或识别失败: {e}")
            
    return items

def parse_image(file_path):
    """【解析图片】使用 EasyOCR 精准提取文字行并匹配报价"""
    global ocr
    if ocr is None:
        # 初始化 EasyOCR 识别器，指定支持英文
        ocr = easyocr.Reader(['en'])
        
    print(f"🖼️ 正在通过 EasyOCR 解析图片/扫描件报价单: {file_path}")
    
    try:
        # EasyOCR 帮我们按行合并对齐文本，返回纯文本行列表
        result = ocr.readtext(file_path, detail=0)
        print("OCR 原始识别文字行:", result)
    except Exception as e:
        print(f"❌ OCR 引擎识别失败: {e}")
        return []

    items = []
    current_category = "Shrub"
    
    # 遍历所有文字行，寻找以数字（订购数量）开头的报价线索
    for i, text in enumerate(result):
        text_str = text.strip()
        
        # 类别切换自动识别
        if text_str in ["Grass", "Shrub", "Tree", "Conifer"]:
            current_category = text_str
            continue
            
        # 匹配数量开头，比如 "33 Carex morrowii" 或者单独的数字 "33"
        match_qty = re.match(r'^(\d+)\s*(.*)', text_str)
        if match_qty:
            ordered = int(match_qty.group(1))
            if ordered >= 1000:  # 排除掉年份或电话号码干扰
                continue
                
            rest_text = match_qty.group(2).strip()
            
            # 如果这一行只有数字，植物名字在换行位置，我们向后拉取 3 行合并分析
            lookahead_parts = [rest_text] if rest_text else []
            for offset in range(1, 4):
                if i + offset < len(result):
                    next_line = result[i + offset].strip()
                    # 如果下一行又是单独的非价格数字开头，说明进入新的植物行了，停止截取
                    if re.match(r'^\d+', next_line) and not re.match(r'^\d+\.\d{2}', next_line):
                        break
                    lookahead_parts.append(next_line)
            
            combined_text = " ".join(lookahead_parts)
            
            # 提取价格（寻找类似于 8.50, 12.95 这样的浮点数）
            prices = [float(p) for p in re.findall(r'\d+\.\d{2}', combined_text)]
            
            # 提取尺寸规格（寻找类似于 #1, #2, 2gal 这样的关键规格）
            size_match = re.search(r'(#\d|\d+gal)', combined_text, re.IGNORECASE)
            size = size_match.group(1) if size_match else "N/A"
            
            # 提取植物名称：从合并文本中把数量、价格、尺寸符号剔除，剩下的就是纯植物名
            botanical_name = combined_text
            if size != "N/A":
                botanical_name = botanical_name.replace(size, "")
            for p in prices:
                botanical_name = botanical_name.replace(f"{p:.2f}", "").replace(str(p), "")
            
            botanical_name = re.sub(r'[\$\,\#]', '', botanical_name).strip()
            
            if "total" in botanical_name.lower() or not botanical_name:
                continue
                
            # 计算最终价格
            if len(prices) >= 2:
                net_price = prices[-2]
                extension = prices[-1]
            else:
                net_price = prices[0] if prices else 0.0
                extension = ordered * net_price
                
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
                            
                            # 格式自适应多轨分流
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
                                    (supplier_name, "AUTO_IMPORT", "2026-07-18", 
                                     i["category"], i["ordered"], i["botanical_name"], i["size"], i["net_price"], i["extension"], filename)
                                    for i in items
                                ]
                                cursor.executemany(sql, records)
                                conn.commit()
                                print(f"💾 成功将 {len(records)} 行数据存入统一数据库！")
                                
                                # 入库后立刻驱动 JSON 同步更新前端大屏
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
    # 安全兜底，整个流水线跑完后再全量校准一次 JSON
    try:
        sync_db_to_json()
    except Exception:
        pass
