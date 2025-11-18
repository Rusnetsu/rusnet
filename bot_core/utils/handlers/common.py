import logging
import sqlite3
import os
from aiogram import Router,F,Bot
from aiogram.types import Message,ReplyKeyboardMarkup,KeyboardButton,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from utils.database import get_user_phone,save_user,save_request,BOT_CONTENT_DB
from utils.notifications import send_new_request_email,send_new_request_telegram
from utils.keyboards import build_reply_keyboard
from aiogram.filters import Filter
from aiogram import types
from pathlib import Path
import asyncio
class NotificationChatFilter(Filter):
    async def __call__(self,message:Message)->bool:
        notify_chat_id=os.getenv("NOTIFY_CHAT_ID")
        if not notify_chat_id:
            return True
        try:
            return str(message.chat.id)!=notify_chat_id
        except:
            return True
class CallbackNotificationFilter(Filter):
    async def __call__(self,callback:CallbackQuery)->bool:
        notify_chat_id=os.getenv("NOTIFY_CHAT_ID")
        if not notify_chat_id:
            return True
        try:
            return str(callback.message.chat.id)!=notify_chat_id
        except:
            return True
notification_chat_filter=NotificationChatFilter()
callback_notification_filter=CallbackNotificationFilter()
router=Router()
logger=logging.getLogger(__name__)
REQUIRE_PHONE=os.getenv("REQUIRE_PHONE","1")=="1"
def get_menu_items(parent_key=None):
    conn=sqlite3.connect(BOT_CONTENT_DB)
    conn.row_factory=sqlite3.Row
    cur=conn.cursor()
    if parent_key is None:
        cur.execute("SELECT * FROM menu_items WHERE parent_key IS NULL ORDER BY sort_order")
    else:
        cur.execute("SELECT * FROM menu_items WHERE parent_key = ? ORDER BY sort_order",(parent_key,))
    items=cur.fetchall()
    conn.close()
    return items
def get_item_by_key(key):
    conn=sqlite3.connect(BOT_CONTENT_DB)
    conn.row_factory=sqlite3.Row
    cur=conn.cursor()
    cur.execute("SELECT * FROM menu_items WHERE key = ?",(key,))
    item=cur.fetchone()
    conn.close()
    return item
def build_menu_keyboard(parent_key=None):
    items=get_menu_items(parent_key)
    buttons=[]
    print(f"DEBUG: Building menu keyboard for parent_key='{parent_key}', found {len(items)} items")
    for item in items:
        callback_data=f"menu_{item['key']}"
        print(f"DEBUG: Adding button: {item['title']} -> {callback_data}")
        buttons.append([InlineKeyboardButton(text=item["title"],callback_data=callback_data)])
    if parent_key:
        parent_item=get_item_by_key(parent_key)
        back_target=parent_item["parent_key"]if parent_item else None
        back_data=f"menu_{back_target}"if back_target else"back_to_main"
        buttons.append([InlineKeyboardButton(text="← Назад",callback_data=back_data)])
        print(f"DEBUG: Added back button: {back_data}")
    else:
        buttons.append([InlineKeyboardButton(text="← Назад",callback_data="back_to_main")])
        print(f"DEBUG: Added back to main button")
    keyboard=InlineKeyboardMarkup(inline_keyboard=buttons)
    print(f"DEBUG: Final keyboard has {len(buttons)} rows")
    return keyboard
def build_service_path(key):
    path_parts=[]
    current_key=key
    while current_key:
        item=get_item_by_key(current_key)
        if not item:
            break
        path_parts.insert(0,item["title"])
        current_key=item["parent_key"]
    return" → ".join(path_parts)
def save_detailed_statistics(bot_id,user_data,action_type,service_path=None):
    try:
        admin_db_path=Path("/home/rusnet/sites/myclienty.ru/admin_app/data/admin.db")
        if not admin_db_path.exists():
            logging.warning(f"База админки не найдена: {admin_db_path}")
            return False
        conn=sqlite3.connect(admin_db_path)
        cursor=conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_statistics(id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,user_id INTEGER,phone TEXT,username TEXT,first_name TEXT,last_name TEXT,interaction_type TEXT NOT NULL,interaction_data TEXT,interaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        user_id=user_data.get('id')or user_data.get('user_id')
        if not user_id:
            logging.warning("user_id не найден в user_data, пропускаем сохранение статистики")
            conn.close()
            return False
        cursor.execute("INSERT INTO bot_statistics(bot_id,user_id,phone,username,first_name,last_name,interaction_type,interaction_data)VALUES(?,?,?,?,?,?,?,?)",(bot_id,user_id,user_data.get('phone'),user_data.get('username'),user_data.get('first_name'),user_data.get('last_name'),action_type,service_path))
        conn.commit()
        conn.close()
        logging.info(f"✅ Статистика сохранена: бот {bot_id}, пользователь {user_id}, действие: {action_type}")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения статистики: {e}")
        return False
def get_media_url(file_path,bot_id,media_type="image"):
    if not file_path:
        return None
    base_url=os.getenv("BASE_URL")
    if not base_url:
        base_url="https://myclienty.ru"
        logging.warning(f"⚠️ BASE_URL не задан для бота {bot_id}, используется значение по умолчанию")
    if media_type=="file":
        filename=file_path.split('/')[-1]
        return f"{base_url}/file/{bot_id}/{filename}"
    else:
        return f"{base_url}/admin_app/static/bots/bot_{bot_id}/{file_path}"
@router.message(F.text.startswith("/start"),notification_chat_filter)
async def cmd_start(message:Message):
    user=message.from_user
    display_name=user.first_name or"друг"
    print(f"DEBUG: /start from user {user.id}, phone: {get_user_phone(user.id)}, REQUIRE_PHONE: {REQUIRE_PHONE}")
    if not REQUIRE_PHONE:
        keyboard=build_reply_keyboard()
        print(f"DEBUG: No phone required, showing menu to user {user.id}")
        await message.answer(f"👋 Добро пожаловать, {display_name}!\nВы можете сразу пользоваться функциями бота.",reply_markup=keyboard)
        return
    if get_user_phone(user.id):
        keyboard=build_reply_keyboard()
        print(f"DEBUG: Phone exists, showing reply keyboard to user {user.id}")
        await message.answer(f"Рады видеть вас снова, {display_name}! 👋",reply_markup=keyboard)
    else:
        await message.answer(f"Здравствуйте, {display_name}! 👋\nПожалуйста, подтвердите свой номер телефона для доступа к функциям бота:",reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Оставить номер телефона",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True))
@router.message(F.contact,notification_chat_filter)
async def handle_contact(message:Message):
    contact=message.contact
    user=message.from_user
    save_user(user.id,user.first_name,user.last_name,user.username,contact.phone_number)
    user_data={'user_id':user.id,'phone':contact.phone_number,'username':user.username,'first_name':user.first_name,'last_name':user.last_name}
    bot_id=os.getenv("BOT_ID","1")
    save_detailed_statistics(int(bot_id),user_data,'contact_authorization')
    logger.info(f"Новый пользователь: {user.full_name} (@{user.username}) — {contact.phone_number}")
    keyboard=build_reply_keyboard()
    await message.answer("✅ Спасибо! Ваш номер сохранён. Добро пожаловать!",reply_markup=keyboard)
@router.message(F.text=='Услуги',notification_chat_filter)
async def show_services(message:Message):
    if REQUIRE_PHONE and not get_user_phone(message.from_user.id):
        return
    first_name=message.from_user.first_name or"друг"
    keyboard=build_menu_keyboard()
    await message.answer(f"Здравствуйте, {first_name}! Что вас интересует?",reply_markup=keyboard)
@router.callback_query(F.data.startswith("menu_"),callback_notification_filter)
async def handle_menu_navigation(callback:CallbackQuery):
    if REQUIRE_PHONE and not get_user_phone(callback.from_user.id):
        await callback.answer("Требуется авторизация.",show_alert=True)
        return
    menu_key=callback.data.replace("menu_","")
    item=get_item_by_key(menu_key)
    if not item:
        await callback.answer("Пункт меню не найден")
        return
    item_dict=dict(item)
    children=get_menu_items(menu_key)
    has_children=any(child["button_type"]=="inline"for child in children)
    if has_children:
        keyboard=build_menu_keyboard(menu_key)
        text=item_dict["description"]or f"Выберите опцию:"
        await callback.message.edit_text(text,reply_markup=keyboard,parse_mode="HTML")
    else:
        await show_item_details(callback,item_dict,menu_key)
    await callback.answer()
async def show_item_details(callback:CallbackQuery,item,menu_key):
    bot_id=os.getenv("BOT_ID","1")
    if item.get("image_path"):
        media_url=get_media_url(item["image_path"],bot_id,"image")
        if media_url:
            try:
                file_extension=item["image_path"].split('.')[-1].lower()
                is_video=file_extension in['mp4','mov','avi','webm']
                if is_video:
                    await callback.message.answer_video(media_url)
                else:
                    await callback.message.answer_photo(media_url)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки медиа: {e}")
    text=f"<b>{item['title']}</b>"
    if item.get("description"):
        text+=f"\n\n{item['description']}"
    if item.get("price"):
        text+=f"\n\n💰 Стоимость: {item['price']}"
    buttons=[]
    if item.get("action")=="order":
        buttons.append([InlineKeyboardButton(text="📨 Заказать",callback_data=f"order_{menu_key}")])
    elif item.get("action")=="download"and item.get("file_path"):
        file_url=get_media_url(item["file_path"],bot_id,"file")
        if file_url:
            file_name=item["file_path"].split('/')[-1]
            buttons.append([InlineKeyboardButton(text=f"📥 Скачать {file_name}",url=file_url)])
    back_data=f"menu_{item['parent_key']}"if item.get("parent_key")else"back_to_services"
    buttons.append([InlineKeyboardButton(text="← Назад",callback_data=back_data)])
    keyboard=InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer(text,reply_markup=keyboard,parse_mode="HTML")
@router.callback_query(F.data.startswith("order_"),callback_notification_filter)
async def handle_order(callback:CallbackQuery,bot:Bot):
    if REQUIRE_PHONE and not get_user_phone(callback.from_user.id):
        await callback.answer("Требуется авторизация.",show_alert=True)
        return
    item_key=callback.data.replace("order_","")
    item=get_item_by_key(item_key)
    if not item:
        await callback.answer("Услуга не найдена")
        return
    user=callback.from_user
    phone=get_user_phone(user.id)
    if not phone:
        await callback.answer("Ошибка: телефон не найден")
        return
    service_path=build_service_path(item_key)
    save_request(user.id,service_path)
    user_data={'user_id':user.id,'phone':phone,'username':user.username,'first_name':user.first_name,'last_name':user.last_name}
    bot_id=os.getenv("BOT_ID","1")
    save_detailed_statistics(int(bot_id),user_data,'order',service_path)
    await send_new_request_telegram(bot,user.first_name,phone,service_path,user.id)
    send_new_request_email(user.first_name,phone,service_path,user.id)
    await callback.answer("✅ Заявка отправлена!")
    await callback.message.answer("✅ Спасибо за заявку! С вами скоро свяжется менеджер.")
@router.callback_query(F.data=="back_to_main")
async def back_to_main(callback:CallbackQuery,state:FSMContext):
    if REQUIRE_PHONE and not get_user_phone(callback.from_user.id):
        await callback.answer("Требуется авторизация.",show_alert=True)
        return
    await state.clear()
    await callback.message.answer("Выберите раздел:",reply_markup=build_reply_keyboard())
    await callback.answer()
@router.callback_query(F.data=="back_to_services")
async def back_to_services(callback:CallbackQuery,state:FSMContext):
    if REQUIRE_PHONE and not get_user_phone(callback.from_user.id):
        await callback.answer("Требуется авторизация.",show_alert=True)
        return
    await state.clear()
    keyboard=build_menu_keyboard()
    await callback.message.edit_text("Что вас интересует?",reply_markup=keyboard)
    await callback.answer()
@router.message(F.text,notification_chat_filter)
async def dynamic_text_handler(message:Message):
    if REQUIRE_PHONE and not get_user_phone(message.from_user.id):
        await message.answer("🔒 Пожалуйста, сначала отправьте номер телефона.",reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Оставить номер телефона",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True))
        return
    conn=sqlite3.connect(BOT_CONTENT_DB)
    conn.row_factory=sqlite3.Row
    cur=conn.cursor()
    cur.execute("SELECT * FROM menu_items WHERE title = ?",(message.text,))
    item=cur.fetchone()
    conn.close()
    if item:
        children=get_menu_items(item['key'])
        if children:
            keyboard=build_menu_keyboard(item['key'])
            text=item["description"]or f"Выберите опцию:"
            await message.answer(text,parse_mode="HTML",reply_markup=keyboard)
        else:
            text=item["description"]or item["title"]
            await message.answer(text,parse_mode="HTML",reply_markup=build_reply_keyboard())
    else:
        await message.answer("Пожалуйста, воспользуйтесь меню:",reply_markup=build_reply_keyboard())