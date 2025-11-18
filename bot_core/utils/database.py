import sqlite3
import os
from pathlib import Path
from typing import Optional
BOT_DATA_PATH=Path(os.getenv("BOT_DATA_PATH","data"))
DB_PATH=BOT_DATA_PATH/"users.db"
BOT_CONTENT_DB=BOT_DATA_PATH/"bot_content.db"
def init_db():
    conn=sqlite3.connect(DB_PATH)
    cursor=conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,first_name TEXT,last_name TEXT,username TEXT,phone TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,service_path TEXT,requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(user_id) REFERENCES users(user_id))")
    conn.commit()
    conn.close()
    init_content_db()
def init_content_db():
    conn=sqlite3.connect(BOT_CONTENT_DB)
    cur=conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS menu_items(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE NOT NULL,parent_key TEXT,title TEXT NOT NULL,description TEXT,price TEXT,sort_order INTEGER DEFAULT 0,button_type TEXT DEFAULT 'reply',action TEXT DEFAULT 'none')")
    conn.commit()
    conn.close()
def save_user(user_id:int,first_name:str,last_name:Optional[str],username:Optional[str],phone:str):
    conn=sqlite3.connect(DB_PATH)
    cursor=conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users(user_id,first_name,last_name,username,phone)VALUES(?,?,?,?,?)",(user_id,first_name,last_name,username,phone))
    conn.commit()
    conn.close()
def get_user_phone(user_id:int)->Optional[str]:
    conn=sqlite3.connect(DB_PATH)
    cursor=conn.cursor()
    cursor.execute("SELECT phone FROM users WHERE user_id=?",(user_id,))
    result=cursor.fetchone()
    conn.close()
    return result[0]if result else None
def save_request(user_id:int,service_path:str):
    conn=sqlite3.connect(DB_PATH)
    cursor=conn.cursor()
    cursor.execute("INSERT INTO requests(user_id,service_path)VALUES(?,?)",(user_id,service_path))
    conn.commit()
    conn.close()
def save_bot_statistics(bot_id:int,user_data:dict,interaction_type:str,interaction_data:str=None):
    try:
        conn=sqlite3.connect(BOT_CONTENT_DB)
        cursor=conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_statistics(id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,user_id INTEGER NOT NULL,phone TEXT,username TEXT,first_name TEXT,last_name TEXT,interaction_type TEXT NOT NULL,interaction_data TEXT,interaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("INSERT INTO bot_statistics(bot_id,user_id,phone,username,first_name,last_name,interaction_type,interaction_data)VALUES(?,?,?,?,?,?,?,?)",(bot_id,user_data.get('user_id'),user_data.get('phone'),user_data.get('username'),user_data.get('first_name'),user_data.get('last_name'),interaction_type,interaction_data))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка сохранения статистики: {e}")