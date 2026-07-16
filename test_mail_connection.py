import imaplib
import email
from email.header import decode_header
import os

EMAIL_ACCOUNT = os.environ.get("GMAIL_USER", "Fengzd3@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_PASS")

def test_gmail_connection():
    print("=== [步骤 2: 邮件收取通道测试] ===")
    if not APP_PASSWORD:
        print("❌ 错误：未配置 GMAIL_PASS 密码。")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(EMAIL_ACCOUNT, APP_PASSWORD)
        print("🎉 Gmail IMAP 登录成功！")
        mail.select("inbox")
        
        status, data = mail.search(None, 'SUBJECT "Quote"')
        mail_ids = data[0].split()
        print(f"🔎 检索完成：收件箱内共有 {len(mail_ids)} 封含 'Quote' 关键字的邮件。")
        
        mail.close()
        mail.logout()
    except Exception as e:
        print(f"❌ 收件测试失败: {e}")

if __name__ == "__main__":
    test_gmail_connection()
