import os
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
def send_whatsapp_notification(phone: str, message: str) -> bool:
    try:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        if not all([account_sid, auth_token, twilio_whatsapp_number]):
            logging.error("❌ Twilio credentials not configured")
            return False
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=message,
            from_=f"whatsapp:{twilio_whatsapp_number}",
            to=f"whatsapp:{phone}"
        )
        logging.info(f"✅ WhatsApp сообщение отправлено. SID: {message.sid}")
        return True
    except TwilioRestException as e:
        logging.error(f"❌ Twilio ошибка: {e.code} - {e.msg}")
        return False
    except Exception as e:
        logging.error(f"❌ Неизвестная ошибка отправки WhatsApp: {e}")
        return False
def send_new_request_whatsapp(user_name: str,user_phone: str,service_path: str,user_id: int):
    if os.getenv("NOTIFY_WHATSAPP_ENABLED","0")!="1":
        logging.info("WhatsApp уведомления отключены в настройках")
        return False
    admin_phone = os.getenv("WHATSAPP_ADMIN_PHONE")
    if not admin_phone:
        logging.error("❌ WHATSAPP_ADMIN_PHONE не задан в настройках")
        return False
    message=("🆕 *Новая заявка через Telegram бота!*\n\n"
        f"*Имя:* {user_name or '—'}\n"
        f"*Телефон клиента:* {user_phone}\n"
        f"*ID в Telegram:* {user_id}\n"
        f"*Услуга:* {service_path}\n\n"
        f"Время: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return send_whatsapp_notification(admin_phone,message)