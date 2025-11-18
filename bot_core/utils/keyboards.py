from aiogram.types import ReplyKeyboardMarkup,KeyboardButton
import sqlite3
import os
from pathlib import Path
BOT_DATA_PATH=Path(os.getenv("BOT_DATA_PATH","data"))
BOT_CONTENT_DB=BOT_DATA_PATH/"bot_content.db"
def get_reply_buttons():
    try:
        conn=sqlite3.connect(BOT_CONTENT_DB)
        conn.row_factory=sqlite3.Row
        cur=conn.cursor()
        cur.execute("SELECT title FROM menu_items WHERE button_type='reply'AND(parent_key IS NULL OR parent_key='')ORDER BY sort_order")
        items=[row["title"]for row in cur.fetchall()]
        conn.close()
        print(f"DEBUG: Found reply buttons: {items}")
        return items
    except Exception as e:
        print(f"DEBUG: Error getting reply buttons: {e}")
        return[]
def build_reply_keyboard():
    buttons=get_reply_buttons()
    if not buttons:
        buttons=["О компании","Контакты","Услуги"]
    keyboard=[[KeyboardButton(text=title)]for title in buttons]
    print(f"DEBUG: Building reply keyboard with: {buttons}")
    return ReplyKeyboardMarkup(keyboard=keyboard,resize_keyboard=True)