import asyncio
import logging
from aiogram import Bot,Dispatcher
from utils.handlers import main_router as router
from utils.database import init_db
from aiogram.fsm.storage.memory import MemoryStorage
import os
from pathlib import Path
BOT_DATA_PATH=Path(os.getenv("BOT_DATA_PATH","data"))
DB_PATH=BOT_DATA_PATH/"users.db"
BOT_CONTENT_DB=BOT_DATA_PATH/"bot_content.db"
BOT_DATA_PATH.mkdir(parents=True,exist_ok=True)
from dotenv import load_dotenv
load_dotenv(BOT_DATA_PATH.parent/".env")
BOT_TOKEN=os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в .env")
LOG_PATH=BOT_DATA_PATH/"bot.log"
logging.basicConfig(level=logging.INFO,format='%(asctime)s - %(message)s',handlers=[logging.FileHandler(LOG_PATH,encoding='utf-8'),logging.StreamHandler()])
class LoggingMiddleware:
    async def __call__(self,handler,event,data):
        if hasattr(event,'message') and event.message:
            message=event.message
            user=message.from_user
            full_name=f"{user.first_name} {user.last_name or ''}".strip()
            username=f"@{user.username}"if user.username else "без ника"
            phone=message.contact.phone_number if message.contact else "не указан"
            text=message.text or message.caption or "[не текстовое сообщение]"
            logging.info(f"Пользователь: {full_name} ({username}) | ID: {user.id} | Телефон: {phone} | Сообщение: {text}")
        return await handler(event,data)
async def main():
    init_db()
    bot=Bot(token=BOT_TOKEN)
    dp=Dispatcher(storage=MemoryStorage())
    dp.update.middleware(LoggingMiddleware())
    dp.include_router(router)
    logging.info("Бот запущен")
    print(f'🤖 Бот запущен! Данные в: {BOT_DATA_PATH}')
    await dp.start_polling(bot)
if __name__=='__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную")
        print('⏹️ Бот остановлен')