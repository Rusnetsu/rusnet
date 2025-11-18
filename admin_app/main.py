from fastapi import FastAPI,Request,Query,HTTPException,Form,Depends,UploadFile,File
from fastapi.responses import HTMLResponse,RedirectResponse,FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter,_rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import time
import secrets
import sqlite3
import os
import logging
from urllib.parse import unquote
from typing import Optional,List
import subprocess
from pathlib import Path
import hashlib
import hmac
import jwt
from datetime import datetime,timedelta
from pydantic import BaseModel
import schedule
import threading
from utils.payment_real import create_yookassa_payment,check_yookassa_payment,validate_ipn_request
import psutil
import shutil
from dotenv import load_dotenv
env_path=Path(__file__).parent/'.env'
load_dotenv(env_path)
SECRET_KEY=os.getenv("ADMIN_SECRET_KEY","your-super-secure-secret-key-change-in-production")
JWT_ALGORITHM="HS256"
JWT_EXPIRATION_HOURS=24
ALLOWED_HOSTS=["myclienty.ru","www.myclienty.ru"]
BOT_TOKEN="8217521937:AAE5NUgTpPLesABRIsW9JjtYmJSZCc2h4Tk"
SESSION_COOKIE="tg_user_session"
JWT_COOKIE="admin_jwt"
RATE_LIMITS={"default":"100/minute","auth":"10/minute","admin":"200/minute"}
ADMIN_USER_IDS=[1257461645,1]
BASE_DIR=Path(__file__).parent
PROJECT_ROOT=BASE_DIR.parent
templates=Jinja2Templates(directory=BASE_DIR/"templates")
class TelegramAuthData(BaseModel):
    id:str
    first_name:str
    last_name:Optional[str]=None
    username:Optional[str]=None
    photo_url:Optional[str]=None
    auth_date:str
    hash:str
class UserSession(BaseModel):
    user_id:str
    first_name:str
    last_name:Optional[str]=None
    username:Optional[str]=None
    photo_url:Optional[str]=None
    session_id:str
    exp:datetime
app=FastAPI()
logging.basicConfig(level=logging.INFO,format='%(asctime)s - %(levelname)s - %(message)s',handlers=[logging.StreamHandler()])
app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(GZipMiddleware,minimum_size=1000)
limiter=Limiter(key_func=get_remote_address,default_limits=[RATE_LIMITS["default"]])
app.state.limiter=limiter
app.add_exception_handler(RateLimitExceeded,_rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.mount("/static",StaticFiles(directory=BASE_DIR/"static"),name="static")
app.mount("/admin_app/static/bots",StaticFiles(directory=PROJECT_ROOT/"bots"),name="bot_media")
def start_bot_process(bot_id:int,token:str,env_path:str):
    try:
        stop_bot_process(bot_id)
        bot_core_path=PROJECT_ROOT/"bot_core"/"main.py"
        env_vars={}
        if env_path.exists():
            with open(env_path,'r',encoding='utf-8')as f:
                for line in f:
                    line=line.strip()
                    if line and not line.startswith('#')and'='in line:
                        key,value=line.split('=',1)
                        env_vars[key]=value
        process_env=os.environ.copy()
        process_env.update(env_vars)
        process_env['PYTHONUNBUFFERED']='1'
        process=subprocess.Popen([str(PROJECT_ROOT/"shared_venv"/"bin"/"python"),str(bot_core_path)],cwd=str(PROJECT_ROOT/"bot_core"),env=process_env)
        bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
        bot_dir.mkdir(parents=True,exist_ok=True)
        pid_file=bot_dir/"bot.pid"
        with open(pid_file,'w')as f:
            f.write(str(process.pid))
        logging.info(f"Запуск бота {bot_id}")
        return True
    except Exception as e:
        logging.error(f"Ошибка запуска бота {bot_id}: {e}")
        return False
def stop_bot_process(bot_id:int):
    try:
        bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
        pid_file=bot_dir/"bot.pid"
        if pid_file.exists():
            with open(pid_file,'r')as f:
                pid=int(f.read().strip())
            try:
                process=psutil.Process(pid)
                process.terminate()
                process.wait(timeout=5)
                logging.info(f"Остановлен бот {bot_id} (PID: {pid})")
            except psutil.NoSuchProcess:
                logging.info(f"Процесс бота {bot_id} уже остановлен")
            except psutil.TimeoutExpired:
                try:
                    process.kill()
                    logging.warning(f"Бот {bot_id} принудительно остановлен")
                except:pass
            try:
                pid_file.unlink(missing_ok=True)
            except:pass
            return True
        for proc in psutil.process_iter(['pid','name','cmdline']):
            try:
                cmdline=proc.info['cmdline']or[]
                cmdline_str=' '.join(cmdline)
                if f"bot_{bot_id}"in cmdline_str and"main.py"in cmdline_str:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    logging.info(f"Запрос на остановку бота {bot_id}")
                    return True
            except(psutil.NoSuchProcess,psutil.AccessDenied,psutil.TimeoutExpired):continue
        logging.warning(f"Бот {bot_id} не найден среди процессов")
        return True
    except Exception as e:
        logging.error(f"Ошибка остановки бота {bot_id}: {e}")
        return False
def check_bot_process_status(bot_id:int)->str:
    try:
        bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
        pid_file=bot_dir/"bot.pid"
        if pid_file.exists():
            with open(pid_file,'r')as f:
                pid=int(f.read().strip())
            if psutil.pid_exists(pid):
                return"online"
        return"offline"
    except Exception:
        return"unknown"
@app.on_event("startup")
async def startup_event():
    logging.info("Запуск админ-панели")
    try:
        updated_count=update_existing_bots_with_base_url()
        if updated_count>0:
            logging.info(f"Автоматически обновлено {updated_count} ботов с BASE_URL")
    except Exception as e:
        logging.error(f"Ошибка автоматического обновления ботов: {e}")
@app.get("/admin/bot/{bot_id}/analytics",response_class=HTMLResponse)
async def bot_analytics_page(request:Request,bot_id:int):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    conn=get_main_db_connection()
    cur=conn.cursor()
    cur.execute("SELECT id,name FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
    bot_row=cur.fetchone()
    if not bot_row:
        conn.close()
        raise HTTPException(status_code=404,detail="Бот не найден")
    bot=dict(bot_row)
    cur.execute("SELECT COUNT(*) as total_interactions,COUNT(DISTINCT user_id) as unique_users,MAX(interaction_date) as last_activity,MIN(interaction_date) as first_activity FROM bot_statistics WHERE bot_id=?",(bot_id,))
    stats_row=cur.fetchone()
    bot_stats=dict(stats_row)if stats_row else{}
    cur.execute("SELECT user_id,phone,username,first_name,last_name,interaction_type,interaction_data,interaction_date FROM bot_statistics WHERE bot_id=? ORDER BY interaction_date DESC LIMIT 50",(bot_id,))
    recent_activities=[dict(row)for row in cur.fetchall()]
    conn.close()
    bots_list=await get_user_bots(int(user.user_id))
    subscription_info=get_user_subscription_info(int(user.user_id))
    return templates.TemplateResponse("bot_analytics.html",{"request":request,"user":user,"bot":bot,"bot_stats":bot_stats,"recent_activities":recent_activities,"bots":bots_list,"current_bot_id":bot_id,"subscription_info":subscription_info})
@app.get("/file/{bot_id}/{filename}")
async def download_file(bot_id:int,filename:str):
    try:
        conn=get_bot_db_connection(bot_id)
        cursor=conn.cursor()
        cursor.execute("SELECT key,file_path FROM menu_items WHERE file_path LIKE ?",(f'%{filename}%',))
        item=cursor.fetchone()
        conn.close()
        if not item:
            raise HTTPException(status_code=404,detail="Файл не найден")
        file_path=Path(item['file_path'])
        full_path=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/file_path
        if not full_path.exists():
            raise HTTPException(status_code=404,detail="Файл не найден на сервере")
        mime_types={'pdf':'application/pdf','doc':'application/msword','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','xls':'application/vnd.ms-excel','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','zip':'application/zip','rar':'application/x-rar-compressed','txt':'text/plain'}
        file_extension=filename.split('.')[-1].lower()
        media_type=mime_types.get(file_extension,'application/octet-stream')
        return FileResponse(full_path,filename=filename,media_type=media_type)
    except Exception as e:
        logging.error(f"Ошибка скачивания файла {filename} для бота {bot_id}: {e}")
        raise HTTPException(status_code=500,detail="Внутренняя ошибка сервера")
@app.post("/admin/platform/update_subscription")
async def update_user_subscription(request:Request,user_id:int=Form(...),plan_type:str=Form(...)):
    user=await get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403,detail="Доступ запрещен")
    if not plan_type:
        return RedirectResponse("/admin/platform/statistics?message=Выберите+тариф",status_code=303)
    plans=get_subscription_plans()
    selected_plan=next((p for p in plans if p['id']==plan_type),None)
    if not selected_plan:
        return RedirectResponse("/admin/platform/statistics?message=Тариф+не+найден",status_code=303)
    try:
        success=create_subscription(user_id=user_id,plan_type=plan_type,amount=selected_plan['price'],payment_id=f"admin_{int(time.time())}",payment_system="admin",is_renewal=False)
        if success:
            logging.info(f"Админ {user.user_id} изменил тариф пользователя {user_id} на {plan_type}")
            return RedirectResponse("/admin/platform/statistics?message=Тариф+успешно+обновлен",status_code=303)
        else:
            return RedirectResponse("/admin/platform/statistics?message=Ошибка+обновления+тарифа",status_code=303)
    except Exception as e:
        logging.error(f"Ошибка изменения тарифа пользователя {user_id}: {e}")
        return RedirectResponse("/admin/platform/statistics?message=Ошибка+сервера",status_code=303)
def get_user_notifications(user_id:int)->list:
    notifications=[]
    subscription_info=get_user_subscription_info(user_id)
    if subscription_info.get('is_active')and subscription_info.get('days_remaining',0)<=3:
        notifications.append({'type':'warning','message':f"Ваша подписка заканчивается через {subscription_info['days_remaining']} дня(ей).",'action_url':'/admin/subscription'})
    if not subscription_info.get('is_active'):
        notifications.append({'type':'error','message':"У вас нет активной подписки. Боты остановлены.",'action_url':'/admin/subscription'})
    return notifications
@app.get("/admin/dashboard",response_class=HTMLResponse)
async def dashboard_page(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    bots_list=await get_user_bots(int(user.user_id))
    subscription_info=get_user_subscription_info(int(user.user_id))
    user_stats=get_user_statistics(int(user.user_id))
    notifications=get_user_notifications(int(user.user_id))
    active_bots_count=0
    for bot in bots_list:
        bot['status']=check_bot_status(bot['id'])
        if bot['status']=='online':
            active_bots_count+=1
    user_stats['active_bots']=active_bots_count
    user_stats['total_bots']=len(bots_list)
    return templates.TemplateResponse("dashboard.html",{"request":request,"user":user,"bots":bots_list,"subscription_info":subscription_info,"stats":user_stats,"notifications":notifications,"current_bot_id":bot_id,"is_admin":is_admin(user)})
@app.get("/admin/bot/{bot_id}/settings",response_class=HTMLResponse)
async def bot_settings_page(request:Request,bot_id:int):
    try:
        user=await get_current_user(request)
        if not user:
            return RedirectResponse("/")
        conn=get_main_db_connection()
        cur=conn.cursor()
        cur.execute("SELECT id,name,token,require_phone FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
        bot_row=cur.fetchone()
        if not bot_row:
            conn.close()
            raise HTTPException(status_code=404,detail="Бот не найден")
        bot=dict(bot_row)
        env_path=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/".env"
        if env_path.exists():
            with open(env_path,'r',encoding='utf-8')as f:
                for line in f:
                    line=line.strip()
                    if line.startswith('REQUIRE_PHONE='):
                        require_phone_value=line.split('=')[1]
                        bot['require_phone']=1 if require_phone_value=='1'else 0
                        break
        bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
        env_path=bot_dir/".env"
        settings={}
        if env_path.exists():
            with open(env_path,'r',encoding='utf-8')as f:
                for line in f:
                    line=line.strip()
                    if line and not line.startswith('#')and'='in line:
                        key,value=line.split('=',1)
                        settings[key]=value
        bots_list=await get_user_bots(int(user.user_id))
        subscription_info=get_user_subscription_info(int(user.user_id))
        user_stats=get_user_statistics(int(user.user_id))
        plans=get_subscription_plans()
        conn.close()
        return templates.TemplateResponse("bot_settings.html",{"request":request,"user":user,"bot":bot,"settings":settings,"bots":bots_list,"current_bot_id":bot_id,"subscription_info":subscription_info,"stats":user_stats,"plans":plans})
    except Exception as e:
        logging.error(f"Ошибка в bot_settings_page: {e}")
        raise HTTPException(status_code=500,detail="Внутренняя ошибка сервера")
@app.post("/admin/bot/{bot_id}/settings/update")
async def update_bot_settings(request:Request,bot_id:int,bot_token:str=Form(None),require_phone:str=Form(None),notify_telegram_enabled:str=Form(None),notify_chat_id:str=Form(None),notify_email_enabled:str=Form(None),email_login:str=Form(None),email_password:str=Form(None),notify_emails:str=Form(None),smtp_server:str=Form(None),smtp_port:str=Form(None),notify_whatsapp_enabled:str=Form(None),whatsapp_number:str=Form(None)):
    try:
        user=await get_current_user(request)
        if not user:
            return RedirectResponse("/")
        conn=get_main_db_connection()
        cur=conn.cursor()
        cur.execute("SELECT id,env_path FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
        bot_row=cur.fetchone()
        if not bot_row:
            conn.close()
            raise HTTPException(status_code=404,detail="Бот не найден")
        bot=dict(bot_row)
        env_path=Path(bot["env_path"])
        settings={}
        if env_path.exists():
            with open(env_path,'r',encoding='utf-8')as f:
                for line in f:
                    line=line.strip()
                    if line and not line.startswith('#')and'='in line:
                        key,value=line.split('=',1)
                        settings[key]=value
        if bot_token and bot_token.strip():
            settings['BOT_TOKEN']=bot_token.strip()
            conn.execute("UPDATE bots SET token=? WHERE id=?",(bot_token.strip(),bot_id))
        if require_phone=="on":
            settings['REQUIRE_PHONE']='1'
        else:
            settings['REQUIRE_PHONE']='0'
        if notify_telegram_enabled is not None:
            settings['NOTIFY_TELEGRAM_ENABLED']='1' if notify_telegram_enabled=='on'else'0'
        if notify_chat_id is not None:
            settings['NOTIFY_CHAT_ID']=notify_chat_id
        if notify_email_enabled is not None:
            settings['NOTIFY_EMAIL_ENABLED']='1' if notify_email_enabled=='on'else'0'
        if email_login is not None:
            settings['EMAIL_LOGIN']=email_login
        if email_password and email_password!="********":
            settings['EMAIL_PASSWORD']=email_password
        if notify_emails is not None:
            settings['NOTIFY_EMAILS']=notify_emails
        if smtp_server is not None:
            settings['SMTP_SERVER']=smtp_server
        if smtp_port is not None:
            settings['SMTP_PORT']=smtp_port
        if notify_whatsapp_enabled is not None:
            settings['NOTIFY_WHATSAPP_ENABLED']='1' if notify_whatsapp_enabled=='on'else'0'
        if whatsapp_number is not None:
            settings['WHATSAPP_NUMBER']=whatsapp_number
        conn.commit()
        conn.close()
        with open(env_path,'w',encoding='utf-8')as f:
            for key,value in settings.items():
                f.write(f"{key}={value}\n")
        try:
            stop_bot_process(bot_id)
            token_to_use=settings.get('BOT_TOKEN')
            if token_to_use:
                success=start_bot_process(bot_id,token_to_use,env_path)
        except Exception as e:
            logging.error(f"Ошибка перезапуска бота {bot_id}: {e}")
        return RedirectResponse(f"/admin/bot/{bot_id}/settings?message=Настройки+успешно+сохранены",status_code=303)
    except Exception as e:
        logging.error(f"Ошибка в update_bot_settings: {e}")
        return RedirectResponse(f"/admin/bot/{bot_id}/settings?message=Ошибка+сохранения+настроек:+{str(e)}",status_code=303)
@app.get("/admin/debug/file/{bot_id}/{file_path:path}")
async def debug_file_access(request:Request,bot_id:int,file_path:str):
    file_full_path=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/file_path
    if file_full_path.exists():
        return FileResponse(file_full_path)
    else:
        return{"error":"File not found","path":str(file_full_path),"exists":file_full_path.exists()}
@app.post("/admin/bot/{bot_id}/settings/test_notifications")
async def test_notifications(request:Request,bot_id:int):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    return RedirectResponse(f"/admin/bot/{bot_id}/settings?test_result=✅ Тестовые уведомления отправлены (функционал в разработке)",status_code=303)
def is_admin(user:UserSession)->bool:
    return int(user.user_id)in ADMIN_USER_IDS
def get_remote_address(request:Request)->str:
    return request.client.host if request.client else"127.0.0.1"
def create_jwt_session(user_data:dict)->str:
    payload={'user_id':user_data['id'],'first_name':user_data['first_name'],'last_name':user_data.get('last_name'),'username':user_data.get('username'),'photo_url':user_data.get('photo_url'),'session_id':secrets.token_urlsafe(32),'exp':datetime.utcnow()+timedelta(hours=JWT_EXPIRATION_HOURS),'iat':datetime.utcnow()}
    return jwt.encode(payload,SECRET_KEY,algorithm=JWT_ALGORITHM)
def verify_jwt_session(token:str)->Optional[UserSession]:
    try:
        payload=jwt.decode(token,SECRET_KEY,algorithms=[JWT_ALGORITHM])
        return UserSession(**payload)
    except(jwt.ExpiredSignatureError,jwt.InvalidTokenError,jwt.DecodeError):
        return None
async def get_current_user(request:Request)->Optional[UserSession]:
    token=request.cookies.get(JWT_COOKIE)
    if not token:
        return None
    return verify_jwt_session(token)
async def get_active_bot_id(request:Request)->Optional[int]:
    user=await get_current_user(request)
    if user:
        token=request.cookies.get(JWT_COOKIE)
        if token:
            try:
                payload=jwt.decode(token,SECRET_KEY,algorithms=[JWT_ALGORITHM])
                return payload.get('active_bot_id')
            except jwt.InvalidTokenError:
                return None
    return None
def slugify(text:str)->str:
    if not text:
        return""
    text=text.lower().strip()
    replacements=[(' ','_'),('-','_'),('(',''),(')',''),('&','and'),('@','at'),('#','sharp'),('+','plus')]
    for old,new in replacements:
        text=text.replace(old,new)
    import re
    text=re.sub(r'[^a-z0-9_]','',text)
    return text
def check_telegram_auth(auth_data:dict)->bool:
    check_hash=auth_data.get("hash")
    if not check_hash:
        logging.error("Telegram auth: отсутствует хеш")
        return False
    data=auth_data.copy()
    data.pop("hash",None)
    for key in data:
        if data[key]is not None and isinstance(data[key],str):
            data[key]=unquote(data[key])
    data_check_arr=[]
    for key in sorted(data.keys()):
        value=data[key]
        if value is not None:
            data_check_arr.append(f"{key}={value}")
    data_check_string="\n".join(data_check_arr)
    secret_key=hashlib.sha256(BOT_TOKEN.encode()).digest()
    calculated_hash=hmac.new(secret_key,data_check_string.encode(),hashlib.sha256).hexdigest()
    result=hmac.compare_digest(calculated_hash,check_hash)
    if not result:
        logging.error(f"Telegram auth: неверный хеш")
    return result
def get_main_db_connection():
    db_path=PROJECT_ROOT/"admin_app"/"data"/"admin.db"
    db_path.parent.mkdir(parents=True,exist_ok=True)
    if not db_path.exists():
        init_admin_db(db_path)
    conn=sqlite3.connect(db_path)
    conn.row_factory=sqlite3.Row
    try:
        try:
            conn.execute("UPDATE bots SET require_phone=0 WHERE require_phone IS NULL OR require_phone=1")
            conn.commit()
        except Exception as e:
            logging.info(f"Миграция require_phone: {e}")
        conn.execute("CREATE TABLE IF NOT EXISTS bots (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,token TEXT NOT NULL,env_path TEXT,service_path TEXT,user_id INTEGER,data_path TEXT,require_phone BOOLEAN DEFAULT 1,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,plan_type TEXT NOT NULL,start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,end_date TIMESTAMP NOT NULL,is_active BOOLEAN DEFAULT 1,payment_status TEXT DEFAULT 'pending',payment_id TEXT,yookassa_payment_id TEXT,payment_system TEXT DEFAULT 'manual',amount DECIMAL(10,2),created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY,first_name TEXT NOT NULL,last_name TEXT,username TEXT,phone TEXT,email TEXT,registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,status TEXT DEFAULT 'active')")
        conn.execute("CREATE TABLE IF NOT EXISTS bot_statistics (id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,user_id INTEGER,phone TEXT,username TEXT,first_name TEXT,last_name TEXT,interaction_type TEXT NOT NULL,interaction_data TEXT,interaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        try:
            cursor=conn.cursor()
            cursor.execute("PRAGMA table_info(subscriptions)")
            existing_columns=[column[1]for column in cursor.fetchall()]
            if'payment_system'not in existing_columns:
                cursor.execute("ALTER TABLE subscriptions ADD COLUMN payment_system TEXT DEFAULT 'manual'")
            if'yookassa_payment_id'not in existing_columns:
                cursor.execute("ALTER TABLE subscriptions ADD COLUMN yookassa_payment_id TEXT")
            if'updated_at'not in existing_columns:
                cursor.execute("ALTER TABLE subscriptions ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            conn.commit()
        except Exception as e:
            logging.info(f"Миграция subscriptions: {e}")
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
    return conn
def init_admin_db(db_path:Path):
    conn=sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS bots (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,token TEXT NOT NULL,env_path TEXT,service_path TEXT,user_id INTEGER,data_path TEXT,require_phone BOOLEAN DEFAULT 1,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()
def init_bot_db(db_path:Path):
    db_path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(db_path)
    cur=conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS menu_items (id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE NOT NULL,parent_key TEXT,title TEXT NOT NULL,description TEXT,price TEXT,sort_order INTEGER DEFAULT 0,button_type TEXT DEFAULT 'reply',action TEXT DEFAULT 'none',image_path TEXT,file_path TEXT)")
    conn.commit()
    conn.close()
    logging.info(f"База бота инициализирована: {db_path}")
def get_bot_db_connection(bot_id:int):
    conn_main=get_main_db_connection()
    cur=conn_main.cursor()
    cur.execute("SELECT data_path FROM bots WHERE id=?",(bot_id,))
    row=cur.fetchone()
    conn_main.close()
    if not row or not row["data_path"]:
        raise HTTPException(status_code=404,detail="Бот не настроен")
    data_path=Path(row["data_path"])
    bot_db_path=data_path/"bot_content.db"
    if not bot_db_path.exists():
        logging.info(f"База данных бота {bot_id} не найдена, создаем...")
        init_bot_db(bot_db_path)
    if not os.access(bot_db_path,os.W_OK):
        stat_info=os.stat(bot_db_path)
        logging.error(f"Нет прав на запись в базу бота {bot_id}")
        logging.error(f"Владелец: {stat_info.st_uid}:{stat_info.st_gid}, Права: {oct(stat_info.st_mode)}")
        logging.error(f"Текущий пользователь процесса: {os.getuid()}:{os.getgid()}")
        raise HTTPException(status_code=500,detail="Нет прав на запись в базу данных бота")
    conn=sqlite3.connect(bot_db_path)
    conn.row_factory=sqlite3.Row
    try:
        cursor=conn.cursor()
        cursor.execute("PRAGMA table_info(menu_items)")
        columns=[column[1]for column in cursor.fetchall()]
        if'image_path'not in columns:
            cursor.execute("ALTER TABLE menu_items ADD COLUMN image_path TEXT")
            logging.info(f"Добавлена колонка image_path в бот {bot_id}")
        if'file_path'not in columns:
            cursor.execute("ALTER TABLE menu_items ADD COLUMN file_path TEXT")
            logging.info(f"Добавлена колонка file_path в бот {bot_id}")
        conn.commit()
    except Exception as e:
        logging.info(f"Миграция бота {bot_id}: {e}")
    return conn
def has_user_used_trial(user_id:int)->bool:
    conn=get_main_db_connection()
    try:
        cursor=conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id=? AND plan_type='trial'",(user_id,))
        trial_count=cursor.fetchone()[0]
        return trial_count>0
    except Exception as e:
        logging.error(f"Ошибка проверки тестового периода для пользователя {user_id}: {e}")
        return True
    finally:
        conn.close()
def get_subscription_plans():
    return[{'id':'trial','name':'🔰 Тестовый','price':0,'period':'trial','bot_limit':1,'features':['1 бот','Все функции на 3 дня','Базовая аналитика','Техническая поддержка','После теста - переход на платный тариф']},{'id':'month','name':'📅 Месячный','price':1490,'period':'month','bot_limit':10,'features':['Все функции ботов','До 10 ботов','Расширенная статистика','Приоритетная поддержка','Резервные копии']},{'id':'3months','name':'🗓️ 3 месяца','price':3900,'period':'3months','bot_limit':15,'features':['Все функции ботов','До 15 ботов','Полная статистика','Приоритетная поддержка','Авто-бэкапы','Экономия ≈ 13%']},{'id':'6months','name':'⏱️ 6 месяцев','price':6600,'period':'6months','bot_limit':25,'features':['Все функции ботов','До 25 ботов','Расширенная аналитика','VIP поддержка','Авто-бэкапы','Экономия ≈ 26%']},{'id':'year','name':'🎉 Годовой','price':12000,'period':'year','bot_limit':0,'features':['Все функции ботов','Неограниченно ботов','Полная аналитика','VIP поддержка','Авто-бэкапы','Личный менеджер','Экономия ≈ 33%']}]
def get_user_subscription_info(user_id:int)->dict:
    conn=None
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT s.id,s.plan_type,s.start_date,s.end_date,s.is_active,s.payment_status,s.amount FROM subscriptions s WHERE s.user_id=? AND s.is_active=1 ORDER BY s.end_date DESC LIMIT 1",(user_id,))
        subscription=cursor.fetchone()
        if not subscription:
            return{'has_subscription':False,'is_active':False,'days_remaining':0,'plan_type':'none','message':'Подписка не найдена'}
        end_date=subscription['end_date']
        days_remaining=0
        if isinstance(end_date,str):
            try:
                end_date=datetime.fromisoformat(end_date.replace('Z','+00:00'))
            except:pass
        if isinstance(end_date,datetime):
            days_remaining=(end_date-datetime.now()).days
        elif isinstance(end_date,str):
            try:
                parsed_date=datetime.strptime(end_date.split('.')[0],'%Y-%m-%d %H:%M:%S')
                days_remaining=(parsed_date-datetime.now()).days
            except:pass
        return{'has_subscription':True,'is_active':subscription['is_active']==1 and days_remaining>0,'plan_type':subscription['plan_type'],'start_date':subscription['start_date'],'end_date':subscription['end_date'],'days_remaining':max(0,days_remaining),'payment_status':subscription['payment_status'],'amount':subscription['amount'],'subscription_id':subscription['id']}
    except Exception as e:
        logging.error(f"Ошибка получения информации о подписке пользователя {user_id}: {e}")
        return{'has_subscription':False,'is_active':False,'days_remaining':0,'plan_type':'none','message':f'Ошибка: {str(e)}'}
    finally:
        if conn:
            conn.close()
def create_subscription(user_id:int,plan_type:str,amount:float,payment_id:str=None,payment_system:str="manual",is_renewal:bool=False)->bool:
    conn=None
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        from datetime import datetime,timedelta
        period_days={'trial':3,'month':30,'3months':90,'6months':180,'year':365}
        days=period_days.get(plan_type,30)
        if payment_system=="admin":
            cursor.execute("UPDATE subscriptions SET is_active=0 WHERE user_id=?",(user_id,))
            start_date=datetime.now()
            end_date=start_date+timedelta(days=days)
            cursor.execute("INSERT INTO subscriptions (user_id,plan_type,start_date,end_date,is_active,payment_status,payment_id,payment_system,amount) VALUES (?,?,?,?,1,'paid',?,?,?)",(user_id,plan_type,start_date,end_date,payment_id,payment_system,amount))
        elif is_renewal:
            cursor.execute("SELECT id,end_date,plan_type FROM subscriptions WHERE user_id=? AND is_active=1 ORDER BY end_date DESC LIMIT 1",(user_id,))
            current_sub=cursor.fetchone()
            if current_sub:
                sub_id,current_end,current_plan=current_sub
                if isinstance(current_end,str):
                    try:
                        current_end=datetime.fromisoformat(current_end.replace('Z','+00:00'))
                    except:
                        current_end=datetime.now()
                elif current_end is None:
                    current_end=datetime.now()
                new_end_date=current_end+timedelta(days=days)
                cursor.execute("UPDATE subscriptions SET plan_type=?,end_date=?,payment_status='paid',amount=?,payment_id=?,payment_system=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(plan_type,new_end_date,amount,payment_id,payment_system,sub_id))
                logging.info(f"Подписка {sub_id} продлена с {current_end} до {new_end_date}")
            else:
                start_date=datetime.now()
                end_date=start_date+timedelta(days=days)
                cursor.execute("INSERT INTO subscriptions (user_id,plan_type,start_date,end_date,is_active,payment_status,payment_id,payment_system,amount) VALUES (?,?,?,?,1,'paid',?,?,?)",(user_id,plan_type,start_date,end_date,payment_id,payment_system,amount))
        else:
            cursor.execute("UPDATE subscriptions SET is_active=0 WHERE user_id=?",(user_id,))
            start_date=datetime.now()
            end_date=start_date+timedelta(days=days)
            cursor.execute("INSERT INTO subscriptions (user_id,plan_type,start_date,end_date,is_active,payment_status,payment_id,payment_system,amount) VALUES (?,?,?,?,1,'pending',?,?,?)",(user_id,plan_type,start_date,end_date,payment_id,payment_system,amount))
        if payment_system=="admin" or payment_system=="manual":
            start_user_bots(user_id)
        conn.commit()
        action="изменена" if payment_system=="admin" else("продлена" if is_renewal else"создана")
        logging.info(f"Подписка {plan_type} для пользователя {user_id} {action} (система: {payment_system})")
        return True
    except Exception as e:
        logging.error(f"Ошибка создания/продления подписки для пользователя {user_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()
def start_user_bots(user_id:int):
    conn=get_main_db_connection()
    try:
        cur=conn.cursor()
        cur.execute("SELECT id,token,env_path FROM bots WHERE user_id=?",(user_id,))
        bots=cur.fetchall()
        for bot in bots:
            bot_id=bot[0]
            token=bot[1]
            env_path=Path(bot[2])
            success=start_bot_process(bot_id,token,env_path)
            if success:
                logging.info(f"Запущен бот {bot_id} пользователя {user_id}")
            else:
                logging.error(f"Ошибка запуска бота {bot_id} пользователя {user_id}")
    except Exception as e:
        logging.error(f"Ошибка запуска ботов пользователя {user_id}: {e}")
    finally:
        conn.close()
def get_user_statistics(user_id:int)->dict:
    conn=None
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bots WHERE user_id=?",(user_id,))
        total_bots=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM bot_statistics WHERE bot_id IN (SELECT id FROM bots WHERE user_id=?)",(user_id,))
        total_interactions=cursor.fetchone()[0]
        cursor.execute("SELECT julianday('now')-julianday(registration_date) FROM users WHERE user_id=?",(user_id,))
        days_result=cursor.fetchone()
        active_days=int(days_result[0]) if days_result and days_result[0] else 0
        cursor.execute("SELECT MAX(interaction_date) FROM bot_statistics WHERE bot_id IN (SELECT id FROM bots WHERE user_id=?)",(user_id,))
        last_activity_result=cursor.fetchone()
        last_activity=last_activity_result[0] if last_activity_result and last_activity_result[0] else None
        return{'total_bots':total_bots,'total_interactions':total_interactions,'active_days':active_days,'last_activity':last_activity}
    except Exception as e:
        logging.error(f"Ошибка получения статистики пользователя {user_id}: {e}")
        return{'total_bots':0,'total_interactions':0,'active_days':0,'last_activity':None}
    finally:
        if conn:
            conn.close()
def create_or_update_user(user_data:dict):
    conn=None
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (user_id,first_name,last_name,username,last_login) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",(int(user_data['id']),user_data['first_name'],user_data.get('last_name'),user_data.get('username')))
        conn.commit()
        create_trial_subscription(int(user_data['id']),conn)
    except Exception as e:
        logging.error(f"Ошибка создания пользователя: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
def create_trial_subscription(user_id:int,conn=None):
    should_close=False
    try:
        from datetime import datetime,timedelta
        if conn is None:
            conn=get_main_db_connection()
            should_close=True
        cursor=conn.cursor()
        cursor.execute("SELECT id FROM subscriptions WHERE user_id=?",(user_id,))
        existing=cursor.fetchone()
        if not existing:
            end_date=datetime.now()+timedelta(days=3)
            cursor.execute("INSERT INTO subscriptions (user_id,plan_type,end_date,is_active,payment_status,amount) VALUES (?,'trial',?,1,'paid',0)",(user_id,end_date))
            conn.commit()
            logging.info(f"Создана пробная подписка для пользователя {user_id}")
        else:
            logging.info(f"Пользователь {user_id} уже имеет подписку, пробная не создана")
    except Exception as e:
        logging.error(f"Ошибка создания пробной подписки: {e}")
        if conn and should_close:
            conn.rollback()
        raise
    finally:
        if conn and should_close:
            conn.close()
def check_user_subscription(user_id:int)->bool:
    conn=None
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND is_active=1 AND end_date>CURRENT_TIMESTAMP AND payment_status='paid'",(user_id,))
        result=cursor.fetchone()
        return result is not None
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False
    finally:
        if conn:
            conn.close()
def stop_user_bots(user_id:int):
    conn=get_main_db_connection()
    try:
        cur=conn.cursor()
        cur.execute("SELECT id FROM bots WHERE user_id=?",(user_id,))
        bots=cur.fetchall()
        for bot in bots:
            bot_id=bot[0]
            success=stop_bot_process(bot_id)
            if success:
                logging.info(f"Остановлен бот {bot_id} пользователя {user_id}")
            else:
                logging.error(f"Ошибка остановки бота {bot_id} пользователя {user_id}")
    except Exception as e:
        logging.error(f"Ошибка остановки ботов пользователя {user_id}: {e}")
    finally:
        conn.close()
@app.post("/admin/payment/process")
async def process_payment(request:Request,plan_type:str=Form(...),payment_method:str=Form("manual")):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    user_id=int(user.user_id)
    plans=get_subscription_plans()
    selected_plan=next((p for p in plans if p['id']==plan_type),None)
    if not selected_plan:
        raise HTTPException(status_code=400,detail="Тарифный план не найден")
    subscription_info=get_user_subscription_info(user_id)
    is_renewal=subscription_info.get('is_active',False) and subscription_info.get('has_subscription',False)
    if plan_type=='trial' and has_user_used_trial(user_id):
        logging.warning(f"Пользователь {user_id} пытается повторно активировать тестовый период")
        return RedirectResponse("/admin/subscription?message=Тестовый+период+можно+использовать+только+один+раз",status_code=303)
    if payment_method=="manual":
        if plan_type=='trial':
            success=create_subscription(user_id=user_id,plan_type=plan_type,amount=selected_plan['price'],payment_id=f"trial_{int(time.time())}",payment_system="manual",is_renewal=False)
            if success:
                logging.info(f"Пользователь {user_id} активировал тестовый период")
                return RedirectResponse("/admin/subscription?message=Тестовый+период+успешно+активирован",status_code=303)
            else:
                raise HTTPException(status_code=500,detail="Ошибка активации тестового периода")
        else:
            success=create_subscription(user_id=user_id,plan_type=plan_type,amount=selected_plan['price'],payment_id=f"manual_{int(time.time())}",payment_system="manual",is_renewal=is_renewal)
            if success:
                action="продлена" if is_renewal else"активирована"
                logging.info(f"Пользователь {user_id} {action} подписку {plan_type} (ручная оплата)")
                return RedirectResponse(f"/admin/subscription?message=Подписка+успешно+{action}",status_code=303)
            else:
                raise HTTPException(status_code=500,detail="Ошибка активации подписки")
    elif payment_method=="yookassa":
        if plan_type=='trial':
            success=create_subscription(user_id=user_id,plan_type=plan_type,amount=selected_plan['price'],payment_id=f"trial_{int(time.time())}",payment_system="manual",is_renewal=False)
            if success:
                logging.info(f"Пользователь {user_id} активировал тестовый период")
                return RedirectResponse("/admin/subscription?message=Тестовый+период+успешно+активирован",status_code=303)
            else:
                raise HTTPException(status_code=500,detail="Ошибка активации тестового периода")
        payment_result=await create_yookassa_payment(amount=selected_plan['price'],description=f"Подписка {selected_plan['name']} - MyClienty",user_id=user_id,plan_type=plan_type)
        if payment_result["success"]:
            create_subscription(user_id=user_id,plan_type=plan_type,amount=selected_plan['price'],payment_id=payment_result["yookassa_payment_id"],payment_system="yookassa",is_renewal=is_renewal)
            return RedirectResponse(payment_result["confirmation_url"],status_code=303)
        else:
            logging.error(f"Ошибка создания платежа: {payment_result['error']}")
            raise HTTPException(status_code=500,detail=f"Ошибка создания платежа: {payment_result['error']}")
    else:
        raise HTTPException(status_code=400,detail="Неизвестный способ оплаты")
@app.get("/admin/payment/success")
async def payment_success(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    payment_id=request.query_params.get("payment_id")
    if not payment_id:
        return RedirectResponse("/admin/subscription?message=Платёж не найден",status_code=303)
    payment_status=await check_yookassa_payment(payment_id)
    if payment_status.get("success") and payment_status.get("paid"):
        conn=get_main_db_connection()
        try:
            conn.execute("UPDATE subscriptions SET payment_status='paid',is_active=1 WHERE payment_id=? AND user_id=?",(payment_id,user.user_id))
            conn.commit()
            start_user_bots(int(user.user_id))
            logging.info(f"Подписка активирована по редиректу: {payment_id}")
        except Exception as e:
            logging.error(f"Ошибка активации по редиректу: {e}")
        finally:
            conn.close()
        return templates.TemplateResponse("payment_success.html",{"request":request,"user":user,"payment_id":payment_id})
    else:
        return RedirectResponse("/admin/subscription?message=Платёж не подтверждён",status_code=303)
@app.post("/admin/payment/webhook/yookassa")
async def yookassa_webhook(request:Request):
    try:
        body=await request.json()
        if not validate_ipn_request(request):
            raise HTTPException(status_code=400,detail="Invalid IPN request")
        payment_id=body.get("object",{}).get("id")
        event=body.get("event")
        if event=="payment.succeeded" and payment_id:
            conn=get_main_db_connection()
            try:
                cursor=conn.cursor()
                cursor.execute("SELECT user_id,plan_type,amount FROM subscriptions WHERE payment_id=? OR yookassa_payment_id=?",(payment_id,payment_id))
                subscription=cursor.fetchone()
                if subscription:
                    user_id,plan_type,amount=subscription
                    subscription_info=get_user_subscription_info(user_id)
                    is_renewal=subscription_info.get('is_active',False) and subscription_info.get('has_subscription',False)
                    success=create_subscription(user_id=user_id,plan_type=plan_type,amount=amount,payment_id=payment_id,payment_system="yookassa",is_renewal=is_renewal)
                    if success:
                        cursor.execute("UPDATE payments SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE payment_id=? OR yookassa_payment_id=?",(payment_id,payment_id))
                        conn.commit()
                        action="продлена" if is_renewal else"активирована"
                        logging.info(f"Webhook: Платеж {payment_id} подтвержден, подписка {action}")
                    else:
                        logging.error(f"Webhook: Ошибка активации подписки для платежа {payment_id}")
                else:
                    logging.error(f"Webhook: Подписка для платежа {payment_id} не найдена")
            except Exception as e:
                logging.error(f"Webhook: Ошибка обновления статуса платежа {payment_id}: {e}")
                if conn:
                    conn.rollback()
            finally:
                conn.close()
        return{"status":"ok"}
    except Exception as e:
        logging.error(f"Ошибка обработки webhook: {e}")
        raise HTTPException(status_code=500,detail="Internal server error")
@app.get("/admin/subscription",response_class=HTMLResponse)
async def subscription_page(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    subscription_info=get_user_subscription_info(int(user.user_id))
    user_stats=get_user_statistics(int(user.user_id))
    plans=get_subscription_plans()
    return templates.TemplateResponse("subscription.html",{"request":request,"user":user,"subscription_info":subscription_info,"stats":user_stats,"plans":plans,"has_user_used_trial":has_user_used_trial})
@app.get("/admin/payment",response_class=HTMLResponse)
async def payment_page(request:Request,plan:str=Query(...),renew:bool=Query(False)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    plans=get_subscription_plans()
    selected_plan=next((p for p in plans if p['id']==plan),None)
    if not selected_plan:
        return HTMLResponse("Тарифный план не найден",status_code=404)
    subscription_info=get_user_subscription_info(int(user.user_id))
    return templates.TemplateResponse("payment.html",{"request":request,"user":user,"plan":selected_plan,"subscription_info":subscription_info,"renew":renew})
@app.middleware("http")
async def subscription_middleware(request:Request,call_next):
    start_time=time.time()
    public_paths=["/","/auth/telegram","/static/","/debug/","/admin/subscription","/admin/payment"]
    if not any(request.url.path.startswith(path) for path in public_paths):
        user=await get_current_user(request)
        if user:
            user_id=int(user.user_id)
            if not check_user_subscription(user_id):
                logging.warning(f"Пользователь {user_id} без активной подписки пытается получить доступ к {request.url.path}")
                stop_user_bots(user_id)
                response=RedirectResponse("/admin/subscription")
                return response
    response=await call_next(request)
    processing_time=time.time()-start_time
    user=await get_current_user(request)
    if user:
        user_id=user.user_id
        logging.info(f"SUBSCRIPTION_CHECK: {user_id} | {request.method} {request.url.path} | {processing_time:.2f}s")
    return response
@app.middleware("http")
async def auth_and_audit_middleware(request:Request,call_next):
    start_time=time.time()
    public_paths=["/","/auth/telegram","/static/","/debug/"]
    if not any(request.url.path.startswith(path) for path in public_paths):
        user=await get_current_user(request)
        if not user:
            logging.warning(f"Неавторизованный доступ к {request.url.path}")
            return RedirectResponse("/")
    response=await call_next(request)
    user=await get_current_user(request)
    user_id=user.user_id if user else"anonymous"
    processing_time=time.time()-start_time
    logging.info(f"AUDIT: {user_id} | {request.method} {request.url.path} | {response.status_code} | {processing_time:.2f}s")
    return response
def build_hierarchy(items,parent_key=None,level=0):
    hierarchy=[]
    items_dict=[dict(item) for item in items]
    for item in items_dict:
        current_parent=item.get('parent_key')
        if current_parent=='':
            current_parent=None
        item_title=item.get('title','').strip()
        if not item_title:
            continue
        if(current_parent is None and parent_key is None)or(str(current_parent)==str(parent_key)):
            indent="&nbsp;&nbsp;&nbsp;&nbsp;"*level
            prefix="↳ " if level>0 else""
            display_title=f"{indent}{prefix}{item_title}"
            hierarchy.append({'key':item['key'],'title':item_title,'display_title':display_title})
            hierarchy.extend(build_hierarchy(items_dict,item['key'],level+1))
    return hierarchy
def check_bot_status(bot_id:int)->str:
    return check_bot_process_status(bot_id)
async def get_user_bots(user_id:int):
    conn=get_main_db_connection()
    try:
        bots=conn.execute("SELECT id,name,token,require_phone FROM bots WHERE user_id=? ORDER BY id",(user_id,)).fetchall()
        result_bots=[]
        for bot in bots:
            bot_dict=dict(bot)
            env_path=PROJECT_ROOT/"bots"/f"bot_{bot_dict['id']}"/".env"
            if env_path.exists():
                with open(env_path,'r',encoding='utf-8')as f:
                    for line in f:
                        line=line.strip()
                        if line.startswith('REQUIRE_PHONE='):
                            require_phone_value=line.split('=')[1]
                            bot_dict['require_phone']=1 if require_phone_value=='1'else 0
                            break
            result_bots.append(bot_dict)
        return result_bots
    except Exception as e:
        logging.error(f"Ошибка получения ботов пользователя {user_id}: {e}")
        return[]
    finally:
        conn.close()
async def ensure_active_bot(request:Request,user:UserSession):
    bot_id=await get_active_bot_id(request)
    if bot_id is None:
        bots_list=await get_user_bots(int(user.user_id))
        if bots_list:
            bot_id=bots_list[0]['id']
            return bot_id
        else:
            return None
    return bot_id
@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    return templates.TemplateResponse("index.html",{"request":request})
@app.get("/auth/telegram")
async def auth_telegram(request:Request,id:str=Query(...),first_name:str=Query(...),last_name:str=Query(None),username:str=Query(None),photo_url:str=Query(None),auth_date:str=Query(...),hash:str=Query(...),force:str=Query(None),ts:str=Query(None)):
    logging.info(f"Попытка аутентификации Telegram: ID={id}, Имя={first_name}, force={force}, ts={ts}")
    auth_data={"id":id,"first_name":first_name,"last_name":last_name,"username":username,"photo_url":photo_url,"auth_date":auth_date,"hash":hash}
    if not check_telegram_auth(auth_data):
        logging.error("Telegram auth: проверка хеша не пройдена")
        raise HTTPException(status_code=403,detail="Данные не от Telegram")
    current_time=time.time()
    auth_time=int(auth_date)
    if current_time-auth_time>3600:
        logging.warning(f"Устаревшие данные авторизации ({(current_time - auth_time)/60:.1f} минут)")
        return RedirectResponse("/auth/expired")
    logging.info("Telegram auth: аутентификация успешна")
    auth_data={"id":id,"first_name":first_name,"last_name":last_name,"username":username}
    create_or_update_user(auth_data)
    if not check_user_subscription(int(id)):
        logging.warning(f"Пользователь {id} без активной подписки")
    jwt_token=create_jwt_session(auth_data)
    response=RedirectResponse(url="/admin/dashboard",status_code=302)
    response.set_cookie(key=JWT_COOKIE,value=jwt_token,httponly=True,secure=True,samesite="strict",max_age=86400,path="/")
    logging.info(f"Пользователь {id} успешно авторизован")
    return response
@app.get("/auth/hard_reset")
async def hard_reset(request:Request):
    response=templates.TemplateResponse("hard_reset.html",{"request":request})
    response.delete_cookie(JWT_COOKIE)
    response.delete_cookie(SESSION_COOKIE)
    return response
@app.get("/auth/telegram_clean")
async def telegram_clean_auth(request:Request):
    return templates.TemplateResponse("telegram_clean.html",{"request":request})
@app.get("/auth/expired",response_class=HTMLResponse)
async def auth_expired(request:Request):
    return templates.TemplateResponse("auth_expired.html",{"request":request})
@app.get("/admin/bots",response_class=HTMLResponse)
async def bots_page(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    conn=get_main_db_connection()
    bots=conn.execute("SELECT id,name,token,require_phone,created_at FROM bots WHERE user_id=? ORDER BY id",(int(user.user_id),)).fetchall()
    conn.close()
    bots_with_status=[]
    for bot in bots:
        bot_dict=dict(bot)
        bot_dict['status']=check_bot_status(bot_dict['id'])
        bots_with_status.append(bot_dict)
    subscription_info=get_user_subscription_info(int(user.user_id))
    return templates.TemplateResponse("bots.html",{"request":request,"user":user,"subscription_info":subscription_info,"bots":bots_with_status,"current_bot_id":bot_id,"is_admin":is_admin(user)})
@app.get("/admin/bots/create",response_class=HTMLResponse)
async def create_bot_form(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    subscription_info=get_user_subscription_info(int(user.user_id))
    return templates.TemplateResponse("create_bot.html",{"request":request,"user":user,"subscription_info":subscription_info})
@app.post("/admin/bots/create",response_class=HTMLResponse)
async def create_bot(request:Request,name:str=Form(...),token:str=Form(...),require_phone:str=Form("off")):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    if not name.strip() or not token.strip():
        return HTMLResponse("❌ Название и токен обязательны",status_code=400)
    require_phone_bool=require_phone=="on"
    conn=get_main_db_connection()
    result=conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM bots").fetchone()
    bot_id=result[0]
    bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
    data_dir=bot_dir/"data"
    data_dir.mkdir(parents=True,exist_ok=True)
    bot_db_path=data_dir/"bot_content.db"
    init_bot_db(bot_db_path)
    env_path=bot_dir/".env"
    base_url="https://myclienty.ru"
    with open(env_path,"w",encoding="utf-8")as f:
        f.write(f"BOT_TOKEN={token}\n")
        f.write(f"BOT_DATA_PATH={data_dir}\n")
        f.write(f"REQUIRE_PHONE={1 if require_phone_bool else 0}\n")
        f.write("NOTIFY_TELEGRAM_ENABLED=0\n")
        f.write("NOTIFY_CHAT_ID=-1003182416961\n")
        f.write("NOTIFY_EMAIL_ENABLED=0\n")
        f.write("EMAIL_LOGIN=info@rusnet.su\n")
        f.write("EMAIL_PASSWORD=your_password_here\n")
        f.write("NOTIFY_EMAILS=info@rusnet.su\n")
        f.write("SMTP_SERVER=smtp.mail.ru\n")
        f.write("SMTP_PORT=465\n")
        f.write("NOTIFY_WHATSAPP_ENABLED=0\n")
        f.write("WHATSAPP_NUMBER=\n")
        f.write("TWILIO_ACCOUNT_SID=your_account_sid_here\n")
        f.write("TWILIO_AUTH_TOKEN=your_auth_token_here\n")
        f.write("TWILIO_WHATSAPP_NUMBER=+14155238886\n")
        f.write("WHATSAPP_ADMIN_PHONE=+79123456789\n")
        f.write(f"BOT_ID={bot_id}\n")
        f.write(f"BASE_URL={base_url}\n")
    env_path.chmod(0o600)
    conn.execute("INSERT INTO bots (id,name,token,env_path,user_id,data_path,require_phone) VALUES (?,?,?,?,?,?,?)",(bot_id,name,token,str(env_path),int(user.user_id),str(data_dir),1 if require_phone_bool else 0))
    conn.commit()
    conn.close()
    start_bot_process(bot_id,token,env_path)
    token_cookie=request.cookies.get(JWT_COOKIE)
    if token_cookie:
        try:
            payload=jwt.decode(token_cookie,SECRET_KEY,algorithms=[JWT_ALGORITHM])
            payload['active_bot_id']=bot_id
            new_token=jwt.encode(payload,SECRET_KEY,algorithm=JWT_ALGORITHM)
            response=RedirectResponse(f"/admin/",status_code=303)
            response.set_cookie(key=JWT_COOKIE,value=new_token,httponly=True,secure=True,samesite="strict",max_age=86400,path="/")
            logging.info(f"Пользователь {user.user_id} создал бота {bot_id}: {name}")
            return response
        except jwt.InvalidTokenError:
            pass
    return RedirectResponse(f"/admin/",status_code=303)
def update_existing_bots_with_base_url():
    try:
        conn=get_main_db_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT id,env_path FROM bots")
        bots=cursor.fetchall()
        conn.close()
        base_url="https://myclienty.ru"
        updated_count=0
        for bot in bots:
            bot_id=bot['id']
            env_path=Path(bot['env_path'])
            if env_path.exists():
                with open(env_path,'r',encoding='utf-8')as f:
                    content=f.read()
                if'BASE_URL='not in content:
                    with open(env_path,'a',encoding='utf-8')as f:
                        f.write(f"\nBASE_URL={base_url}\n")
                    logging.info(f"Добавлен BASE_URL в бот {bot_id}")
                    updated_count+=1
                else:
                    logging.info(f"Бот {bot_id} уже имеет BASE_URL")
        logging.info(f"Обновлено {updated_count} ботов с BASE_URL")
        return updated_count
    except Exception as e:
        logging.error(f"Ошибка обновления ботов: {e}")
        return 0
@app.post("/admin/bots/update_base_url")
async def update_all_bots_base_url(request:Request):
    user=await get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403,detail="Доступ запрещен")
    updated_count=update_existing_bots_with_base_url()
    return{"message":f"Обновлено {updated_count} ботов с BASE_URL","updated_count":updated_count}
@app.get("/admin/api/bots/status")
async def get_bots_status(request:Request):
    user=await get_current_user(request)
    if not user:
        return{"error":"Не авторизован"}
    bots_list=await get_user_bots(int(user.user_id))
    for bot in bots_list:
        bot['status']=check_bot_status(bot['id'])
    return{"bots":bots_list,"timestamp":datetime.now().isoformat()}
@app.get("/admin/logs",response_class=HTMLResponse)
async def logs_page(request:Request,bot_id:int=Query(None)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    logs=[]
    selected_bot=None
    if bot_id:
        conn=get_main_db_connection()
        cur=conn.cursor()
        cur.execute("SELECT id,name FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
        bot_row=cur.fetchone()
        conn.close()
        if bot_row:
            selected_bot=dict(bot_row)
            bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/"data"
            log_path=bot_dir/"bot.log"
            if log_path.exists():
                try:
                    with open(log_path,'r',encoding='utf-8')as f:
                        logs=f.readlines()[-100:]
                        logs.reverse()
                except Exception as e:
                    logging.error(f"Ошибка чтения логов бота {bot_id}: {e}")
            else:
                logging.warning(f"Файл логов не найден: {log_path}")
    bots_list=await get_user_bots(int(user.user_id))
    subscription_info=get_user_subscription_info(int(user.user_id))
    return templates.TemplateResponse("logs.html",{"request":request,"user":user,"logs":logs,"selected_bot":selected_bot,"bots":bots_list,"subscription_info":subscription_info})
@app.get("/admin/",response_class=HTMLResponse)
async def admin_panel(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return templates.TemplateResponse("no_bot.html",{"request":request,"user":user})
    try:
        conn=get_bot_db_connection(bot_id)
        items=conn.execute("SELECT id,key,parent_key,title,description,price,sort_order,COALESCE(action,'none') as action,COALESCE(button_type,'reply') as button_type,image_path,file_path FROM menu_items ORDER BY parent_key,sort_order").fetchall()
        conn.close()
        items_list=[dict(item) for item in items]
        hierarchy_items=build_hierarchy(items_list)
        bots_list=await get_user_bots(int(user.user_id))
        subscription_info=get_user_subscription_info(int(user.user_id))
        parent_filters=[{'key':'','title':'Все элементы'}]
        for item in hierarchy_items:
            if item.get('title'):
                parent_filters.append({'key':item['key'],'display_title':item.get('display_title',item['title'])})
        return templates.TemplateResponse("index.html",{"request":request,"user":user,"items":items_list,"hierarchy_items":hierarchy_items,"parent_filters":parent_filters,"bots":bots_list,"current_bot_id":bot_id,"subscription_info":subscription_info,"is_admin":is_admin(user) if user else False})
    except HTTPException as e:
        logging.error(f"Ошибка подключения к базе бота {bot_id}: {e}")
        return templates.TemplateResponse("index.html",{"request":request,"user":user,"subscription_info":get_user_subscription_info(int(user.user_id)),"items":[],"hierarchy_items":[],"parent_filters":[{'key':'','title':'Все элементы'}],"bots":await get_user_bots(int(user.user_id)),"current_bot_id":bot_id,"is_admin":is_admin(user) if user else False,"error":f"База данных бота не инициализирована. Возможно, бот еще не запускался."})
@app.get("/auth/complete_logout")
async def complete_logout(request:Request):
    response=RedirectResponse(url="/auth/cleared")
    response.delete_cookie(JWT_COOKIE)
    response.delete_cookie(SESSION_COOKIE)
    response.headers["Cache-Control"]="no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"]="no-cache"
    response.headers["Expires"]="0"
    logging.info("Полный выход из системы")
    return response
@app.get("/auth/force_logout")
async def force_logout(request:Request):
    token=request.cookies.get(JWT_COOKIE)
    if token:
        try:
            payload=jwt.decode(token,SECRET_KEY,algorithms=[JWT_ALGORITHM])
            user_id=payload.get('user_id')
            logging.info(f"Пользователь {user_id} вышел из системы")
        except:
            pass
    return RedirectResponse(url="/auth/complete_logout")
@app.get("/admin/logout")
async def logout(request:Request):
    return RedirectResponse(url="/auth/force_logout")
@app.get("/pricing",response_class=HTMLResponse)
async def public_pricing(request:Request):
    return templates.TemplateResponse("public_pricing.html",{"request":request})
@app.get("/auth",response_class=HTMLResponse)
async def public_auth(request:Request):
    return templates.TemplateResponse("public_auth.html",{"request":request})
@app.get("/admin/table",response_class=HTMLResponse)
async def table_partial(request:Request,parent_filter:str=Query(None),search_query:str=Query(None)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return HTMLResponse("Нет доступных ботов",status_code=400)
    conn=get_bot_db_connection(bot_id)
    query="SELECT id,key,parent_key,title,description,price,sort_order,COALESCE(action,'none') as action,COALESCE(button_type,'reply') as button_type,image_path,file_path FROM menu_items WHERE 1=1"
    params=[]
    if parent_filter:
        query+=" AND parent_key=?"
        params.append(parent_filter)
    if search_query:
        query+=" AND title LIKE ?"
        params.append(f"%{search_query}%")
    query+=" ORDER BY parent_key,sort_order"
    items=conn.execute(query,params).fetchall()
    conn.close()
    items_list=[dict(item) for item in items]
    conn=get_bot_db_connection(bot_id)
    all_items=conn.execute("SELECT id,key,parent_key,title FROM menu_items ORDER BY parent_key,sort_order").fetchall()
    conn.close()
    all_items_list=[dict(item) for item in all_items]
    hierarchy_items=build_hierarchy(all_items_list)
    parent_filters=[{'key':'','title':'Все элементы'}]
    for item in hierarchy_items:
        if item.get('title') and item.get('title').strip():
            parent_filters.append({'key':item['key'],'display_title':item.get('display_title',item['title'])})
    conn=get_bot_db_connection(bot_id)
    all_items_for_titles=conn.execute("SELECT key,title FROM menu_items").fetchall()
    conn.close()
    title_by_key={item['key']:item['title'] for item in all_items_for_titles}
    for item in items_list:
        item['parent_title']=title_by_key.get(item['parent_key'],'—') if item['parent_key'] else'—'
    return templates.TemplateResponse("table.html",{"request":request,"items":items_list,"hierarchy_items":hierarchy_items,"parent_filters":parent_filters})
@app.post("/admin/save")
async def save_item(request:Request,id:int=Form(...),title:str=Form(...),description:str=Form(""),price:str=Form(""),sort_order:int=Form(0),action:str=Form("none"),button_type:str=Form("reply"),parent_key:str=Form(None),image:UploadFile=File(None),file:UploadFile=File(None),remove_image:str=Form(None),remove_file:str=Form(None)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    if image and image.size>5*1024*1024:
        return HTMLResponse("ERROR: Размер изображения не должен превышать 5MB",status_code=400)
    if not title.strip():
        return HTMLResponse("ERROR: Заголовок не может быть пустым",status_code=400)
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return HTMLResponse("Нет активного бота",status_code=400)
    try:
        conn=get_bot_db_connection(bot_id)
        cur=conn.cursor()
        cur.execute("SELECT image_path,file_path FROM menu_items WHERE id=?",(id,))
        current_data=cur.fetchone()
        current_image_path=current_data[0] if current_data else None
        current_file_path=current_data[1] if current_data else None
        new_image_path=current_image_path
        if remove_image=="1" and current_image_path:
            delete_file(bot_id,current_image_path)
            new_image_path=None
        elif image and image.filename:
            if current_image_path:
                delete_file(bot_id,current_image_path)
            new_image_path=await save_uploaded_file(image,bot_id,"images")
        new_file_path=current_file_path
        if remove_file=="1" and current_file_path:
            delete_file(bot_id,current_file_path)
            new_file_path=None
        elif file and file.filename:
            if current_file_path:
                delete_file(bot_id,current_file_path)
            new_file_path=await save_uploaded_file(file,bot_id,"files")
        cur.execute("UPDATE menu_items SET title=?,description=?,price=?,sort_order=?,action=?,button_type=?,image_path=?,file_path=?,parent_key=? WHERE id=?",(title,description,price,sort_order,action,button_type,new_image_path,new_file_path,parent_key,id))
        conn.commit()
        conn.close()
        logging.info(f"Пользователь {user.user_id} сохранил элемент {id} в боте {bot_id}")
        return HTMLResponse("OK")
    except sqlite3.OperationalError as e:
        logging.error(f"Ошибка записи в базу бота {bot_id}: {e}")
        return HTMLResponse(f"ERROR: Ошибка базы данных - нет прав на запись",status_code=500)
    except Exception as e:
        logging.error(f"Ошибка сохранения элемента {id} в боте {bot_id}: {e}")
        return HTMLResponse(f"ERROR: {e}",status_code=500)
@app.post("/admin/add")
async def add_item(request:Request,title:str=Form(...),parent_key:str=Form(None),description:str=Form(""),price:str=Form(""),sort_order:int=Form(0),action:str=Form("none"),button_type:str=Form("reply"),image:UploadFile=File(None),file:UploadFile=File(None)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return HTMLResponse("Нет активного бота",status_code=400)
    key=slugify(title) or"item"
    if len(key)>64:
        key=key[:64]
    conn=get_bot_db_connection(bot_id)
    cur=conn.cursor()
    orig=key
    i=1
    while cur.execute("SELECT 1 FROM menu_items WHERE key=?",(key,)).fetchone():
        key=f"{orig}_{i}"
        i+=1
        if i>100:
            conn.close()
            return HTMLResponse("ERROR: Не удалось сгенерировать уникальный ключ",status_code=500)
    image_path=None
    file_path=None
    if image and image.filename:
        image_path=await save_uploaded_file(image,bot_id,"images")
    if file and file.filename:
        file_path=await save_uploaded_file(file,bot_id,"files")
    cur.execute("INSERT INTO menu_items (key,parent_key,title,description,price,sort_order,action,button_type,image_path,file_path) VALUES (?,?,?,?,?,?,?,?,?,?)",(key,parent_key,title,description,price,sort_order,action,button_type,image_path,file_path))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user.user_id} добавил элемент '{title}' в боте {bot_id}")
    return HTMLResponse("OK")
async def save_uploaded_file(upload_file:UploadFile,bot_id:int,file_type:str)->str:
    try:
        bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/"media"/file_type
        bot_dir.mkdir(parents=True,exist_ok=True)
        file_extension=upload_file.filename.split('.')[-1]
        filename=f"{int(time.time())}_{secrets.token_hex(8)}.{file_extension}"
        file_path=bot_dir/filename
        with open(file_path,"wb")as buffer:
            shutil.copyfileobj(upload_file.file,buffer)
        return f"media/{file_type}/{filename}"
    except Exception as e:
        logging.error(f"Ошибка сохранения файла: {e}")
        return None
def delete_file(bot_id:int,file_path:str):
    try:
        full_path=PROJECT_ROOT/"bots"/f"bot_{bot_id}"/file_path
        if full_path.exists():
            full_path.unlink()
            logging.info(f"Удален файл: {file_path}")
    except Exception as e:
        logging.error(f"Ошибка удаления файла {file_path}: {e}")
@app.post("/admin/delete")
async def delete_item(request:Request,id:int=Form(...)):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return HTMLResponse("Нет активного бота",status_code=400)
    conn=None
    try:
        conn=get_bot_db_connection(bot_id)
        conn.execute("BEGIN TRANSACTION")
        cursor=conn.cursor()
        cursor.execute("SELECT image_path,file_path FROM menu_items WHERE id=?",(id,))
        item_data=cursor.fetchone()
        if item_data:
            if item_data['image_path']:
                delete_file(bot_id,item_data['image_path'])
            if item_data['file_path']:
                delete_file(bot_id,item_data['file_path'])
        conn.execute("DELETE FROM menu_items WHERE id=?",(id,))
        conn.commit()
        logging.info(f"Пользователь {user.user_id} удалил элемент {id} в боте {bot_id}")
        return HTMLResponse("OK")
    except sqlite3.OperationalError as e:
        if conn:
            conn.rollback()
        logging.error(f"Ошибка записи в базу бота {bot_id}: {e}")
        return HTMLResponse(f"ERROR: Ошибка базы данных - нет прав на запись",status_code=500)
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Ошибка удаления элемента {id} в боте {bot_id}: {e}")
        return HTMLResponse(f"ERROR: {e}",status_code=500)
    finally:
        if conn:
            conn.close()
@app.get("/admin/preview",response_class=HTMLResponse)
async def bot_preview(request:Request):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    bot_id=await ensure_active_bot(request,user)
    if bot_id is None:
        return RedirectResponse("/admin/bots")
    bots_list=await get_user_bots(int(user.user_id))
    subscription_info=get_user_subscription_info(int(user.user_id))
    try:
        conn=get_bot_db_connection(bot_id)
        items=conn.execute("SELECT id,key,parent_key,title,description,price,sort_order,COALESCE(action,'none') as action,COALESCE(button_type,'reply') as button_type,image_path,file_path FROM menu_items ORDER BY parent_key,sort_order").fetchall()
        conn.close()
        menu_tree=[]
        for item in items:
            item_dict=dict(item)
            if item_dict.get('image_path'):
                item_dict['image_url']=f"/admin_app/static/bots/bot_{bot_id}/{item_dict['image_path']}"
            menu_tree.append(item_dict)
        current_bot_name=next((bot['name'] for bot in bots_list if bot['id']==bot_id),f"Бот {bot_id}")
        return templates.TemplateResponse("preview.html",{"request":request,"user":user,"subscription_info":subscription_info,"menu_tree":menu_tree,"bots":bots_list,"current_bot_id":bot_id,"current_bot_name":current_bot_name})
    except Exception as e:
        logging.error(f"Ошибка подключения к базе бота {bot_id}: {e}")
        return templates.TemplateResponse("preview.html",{"request":request,"user":user,"subscription_info":subscription_info,"menu_tree":[],"bots":bots_list,"current_bot_id":bot_id,"current_bot_name":f"Бот {bot_id}","error":f"База данных бота не инициализирована: {e}"})
@app.get("/admin/switch_bot/{bot_id}")
async def switch_bot(request:Request,bot_id:int):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    conn=get_main_db_connection()
    cur=conn.cursor()
    cur.execute("SELECT id FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=403,detail="Бот не ваш")
    conn.close()
    token=request.cookies.get(JWT_COOKIE)
    if token:
        try:
            payload=jwt.decode(token,SECRET_KEY,algorithms=[JWT_ALGORITHM])
            payload['active_bot_id']=bot_id
            new_token=jwt.encode(payload,SECRET_KEY,algorithm=JWT_ALGORITHM)
            response=RedirectResponse(request.headers.get("referer","/admin/"))
            response.set_cookie(key=JWT_COOKIE,value=new_token,httponly=True,secure=True,samesite="strict",max_age=86400,path="/")
            return response
        except jwt.InvalidTokenError:
            pass
    return RedirectResponse("/admin/")
@app.post("/admin/bot/{bot_id}/start")
async def start_bot(request:Request,bot_id:int):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    conn=get_main_db_connection()
    cur=conn.cursor()
    cur.execute("SELECT id,token,env_path FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
    bot_row=cur.fetchone()
    if not bot_row:
        conn.close()
        raise HTTPException(status_code=403,detail="Бот не ваш")
    bot=dict(bot_row)
    conn.close()
    success=start_bot_process(bot_id,bot['token'],Path(bot['env_path']))
    if success:
        logging.info(f"Пользователь {user.user_id} запустил бота {bot_id}")
        return RedirectResponse(f"/admin/bots?status=started&bot_id={bot_id}",status_code=303)
    else:
        raise HTTPException(status_code=500,detail="Не удалось запустить бота")
@app.post("/admin/bot/{bot_id}/stop")
async def stop_bot(request:Request,bot_id:int):
    user=await get_current_user(request)
    if not user:
        return RedirectResponse("/")
    conn=get_main_db_connection()
    cur=conn.cursor()
    cur.execute("SELECT id FROM bots WHERE id=? AND user_id=?",(bot_id,user.user_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=403,detail="Бот не ваш")
    conn.close()
    success=stop_bot_process(bot_id)
    if success:
        logging.info(f"Пользователь {user.user_id} остановил бота {bot_id}")
        return RedirectResponse(f"/admin/bots?status=stopped&bot_id={bot_id}",status_code=303)
    else:
        raise HTTPException(status_code=500,detail="Не удалось остановить бота")
@app.get("/admin/logout")
async def logout(request:Request):
    token=request.cookies.get(JWT_COOKIE)
    if token:
        try:
            payload=jwt.decode(token,SECRET_KEY,algorithms=[JWT_ALGORITHM])
            user_id=payload.get('user_id')
            logging.info(f"Пользователь {user_id} вышел из системы")
        except:
            pass
    return RedirectResponse(url="/auth/complete_logout")
@app.get("/auth/cleared")
async def auth_cleared(request:Request):
    return templates.TemplateResponse("auth_cleared.html",{"request":request})
@app.get("/debug/auth")
async def debug_auth(request:Request):
    user=await get_current_user(request)
    return{"authenticated":user is not None,"user_id":user.user_id if user else None,"jwt_cookie_exists":bool(request.cookies.get(JWT_COOKIE)),"current_time":time.time()}
@app.get("/admin/platform/export")
async def export_platform_data(request:Request):
    user=await get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403,detail="Доступ запрещен")
@app.get("/admin/platform/statistics",response_class=HTMLResponse)
async def platform_statistics(request:Request):
    user=await get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403,detail="Доступ запрещен")
    conn=get_main_db_connection()
    try:
        cur=conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT u.user_id) as total_users,COUNT(DISTINCT b.id) as total_bots,(SELECT COUNT(*) FROM bot_statistics) as total_interactions,COUNT(DISTINCT s.user_id) as users_with_subscription,(SELECT COUNT(DISTINCT user_id) FROM bot_statistics WHERE phone IS NOT NULL AND phone != '') as users_with_phone FROM users u LEFT JOIN bots b ON u.user_id=b.user_id LEFT JOIN subscriptions s ON u.user_id=s.user_id AND s.is_active=1")
        platform_stats=cur.fetchone()
        cur.execute("SELECT u.user_id,u.first_name,u.last_name,u.username,u.phone,u.registration_date,u.last_login,COUNT(DISTINCT b.id) as bot_count,(SELECT COUNT(*) FROM bot_statistics bs WHERE bs.user_id=u.user_id) as interaction_count,(SELECT MAX(interaction_date) FROM bot_statistics WHERE user_id=u.user_id) as last_activity,(SELECT plan_type FROM subscriptions WHERE user_id=u.user_id AND is_active=1 ORDER BY end_date DESC LIMIT 1) as current_plan FROM users u LEFT JOIN bots b ON u.user_id=b.user_id GROUP BY u.user_id ORDER BY u.registration_date DESC")
        users_stats=cur.fetchall()
        cur.execute("SELECT b.id,b.name,b.token,u.first_name as owner_name,u.username as owner_username,u.phone as owner_phone,COUNT(DISTINCT bs.user_id) as unique_users,COUNT(bs.id) as total_interactions,MAX(bs.interaction_date) as last_activity,b.created_at FROM bots b JOIN users u ON b.user_id=u.user_id LEFT JOIN bot_statistics bs ON b.id=bs.bot_id GROUP BY b.id ORDER BY b.created_at DESC")
        bots_stats=cur.fetchall()
        cur.execute("SELECT bs.interaction_date,bs.interaction_type,bs.first_name,bs.username,bs.phone,b.name as bot_name,u.first_name as owner_name,u.phone as owner_phone FROM bot_statistics bs JOIN bots b ON bs.bot_id=b.id JOIN users u ON b.user_id=u.user_id ORDER BY bs.interaction_date DESC LIMIT 50")
        recent_activities=cur.fetchall()
    except Exception as e:
        logging.error(f"Ошибка получения статистики платформы: {e}")
        platform_stats=(0,0,0,0,0)
        users_stats=[]
        bots_stats=[]
        recent_activities=[]
    finally:
        conn.close()
    return templates.TemplateResponse("platform_statistics.html",{"request":request,"user":user,"platform_stats":platform_stats,"users_stats":users_stats,"bots_stats":bots_stats,"recent_activities":recent_activities,"plans":get_subscription_plans()})
def check_expiring_subscriptions():
    conn=get_main_db_connection()
    try:
        cur=conn.cursor()
        cur.execute("SELECT DISTINCT u.user_id,u.first_name,u.username,s.end_date,julianday(s.end_date)-julianday('now') as days_remaining FROM subscriptions s JOIN users u ON s.user_id=u.user_id WHERE s.is_active=1 AND s.payment_status='paid' AND julianday(s.end_date)-julianday('now') BETWEEN 1 AND 3")
        expiring_users=cur.fetchall()
        for user in expiring_users:
            user_id=user[0]
            days_remaining=int(user[4])
            logging.info(f"Подписка пользователя {user_id} истекает через {days_remaining} дней")
    except Exception as e:
        logging.error(f"Ошибка проверки истекающих подписок: {e}")
    finally:
        conn.close()
def check_expired_subscriptions():
    conn=get_main_db_connection()
    try:
        cur=conn.cursor()
        cur.execute("SELECT DISTINCT user_id FROM subscriptions WHERE is_active=1 AND end_date<=CURRENT_TIMESTAMP AND payment_status='paid'")
        expired_users=cur.fetchall()
        expired_count=0
        for user_row in expired_users:
            user_id=user_row[0]
            logging.info(f"Подписка пользователя {user_id} истекла, останавливаем боты")
            stop_user_bots(user_id)
            cur.execute("UPDATE subscriptions SET is_active=0 WHERE user_id=? AND is_active=1",(user_id,))
            expired_count+=1
        conn.commit()
        logging.info(f"Проверка подписок завершена. Найдено {expired_count} истекших подписок")
    except Exception as e:
        logging.error(f"Ошибка проверки подписок: {e}")
    finally:
        conn.close()
def run_scheduler():
    def scheduler_thread():
        while True:
            schedule.run_pending()
            time.sleep(60)
    schedule.every(6).hours.do(check_expired_subscriptions)
    schedule.every().day.at("09:00").do(check_expiring_subscriptions)
    thread=threading.Thread(target=scheduler_thread,daemon=True)
    thread.start()
@app.post("/admin/api/bots/restart_all")
async def restart_all_bots(request:Request):
    user=await get_current_user(request)
    if not user:
        return{"error":"Не авторизован"}
    bots_list=await get_user_bots(int(user.user_id))
    restarted_count=0
    for bot in bots_list:
        if check_bot_status(bot['id'])=='online':
            try:
                subprocess.run(["systemctl","restart",f"bot-{bot['id']}"],check=True)
                restarted_count+=1
                logging.info(f"Перезапущен бот {bot['id']} пользователем {user.user_id}")
            except subprocess.CalledProcessError as e:
                logging.error(f"Ошибка перезапуска бота {bot['id']}: {e}")
    return{"message":f"Перезапущено {restarted_count} ботов","restarted_count":restarted_count}
if __name__=="__main__":
    run_scheduler()
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=8001,ssl_keyfile="/etc/certs/rusnet/myclienty.ru_autorenew_letsen.key",ssl_certfile="/etc/certs/rusnet/myclienty.ru_autorenew_letsen.crt_v2")