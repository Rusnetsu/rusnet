import os
import logging
from aiogram import Bot
async def send_new_request_telegram(bot: Bot,user_name: str,user_phone: str,service_path: str,user_id: int):
    if os.getenv("NOTIFY_TELEGRAM_ENABLED","0")!="1":
        return
    notify_chat_id=os.getenv("NOTIFY_CHAT_ID")
    if not notify_chat_id:
        logging.error("❌ NOTIFY_CHAT_ID не задан")
        return
    try:
        notify_chat_id=int(notify_chat_id)
    except ValueError:
        logging.error("❌ NOTIFY_CHAT_ID должен быть целым числом")
        return
    text=(f"🆕 <b>Новая заявка!</b>\n\n"
        f"Имя: {user_name or '—'}\n"
        f"Телефон: {user_phone}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Услуга: {service_path}")
    try:
        await bot.send_message(notify_chat_id,text,parse_mode="HTML")
        logging.info("✅ Уведомление отправлено в Telegram")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки в Telegram {notify_chat_id}: {e}")