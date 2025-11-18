import smtplib
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
def send_new_request_email(user_name: str, user_phone: str, service_path: str, user_id: int):
    if os.getenv("NOTIFY_EMAIL_ENABLED", "0") != "1":
        return
    email_login = os.getenv("EMAIL_LOGIN")
    email_password = os.getenv("EMAIL_PASSWORD")
    if not email_login or not email_password:
        logging.error("❌ EMAIL_LOGIN или EMAIL_PASSWORD не заданы")
        return
    smtp_server = os.getenv("SMTP_SERVER", "smtp.mail.ru")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    subject = "🆕 Новая заявка через Telegram-бот"
    body = f"""Новая заявка от клиента:
Имя: {user_name}
Телефон: {user_phone}
ID в Telegram: {user_id}
Выбранная услуга: {service_path}
Время: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    notify_emails = os.getenv("NOTIFY_EMAILS", "")
    if not notify_emails:
        logging.error("❌ NOTIFY_EMAILS не заданы")
        return
    to_emails = [email.strip() for email in notify_emails.split(',') if email.strip()]
    msg = MIMEMultipart()
    msg["From"] = email_login
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(email_login, email_password)
            server.sendmail(email_login, to_emails, msg.as_string())
        logging.info("✅ Email-уведомление отправлено")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки email: {e}")