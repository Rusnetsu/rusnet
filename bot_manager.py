import time
import logging
import sqlite3
from pathlib import Path
import subprocess
import psutil
import signal
import sys
from threading import Thread
import schedule
logging.basicConfig(level=logging.INFO,format='%(asctime)s - %(levelname)s - %(message)s',handlers=[logging.StreamHandler(sys.stdout)])
PROJECT_ROOT=Path("/home/rusnet/sites/myclienty.ru")
def get_db_connection():
 db_path=PROJECT_ROOT/"admin_app"/"data"/"admin.db"
 conn=sqlite3.connect(db_path)
 conn.row_factory=sqlite3.Row
 return conn
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
  import os
  process_env=os.environ.copy()
  process_env.update(env_vars)
  process_env['PYTHONUNBUFFERED']='1'
  process=subprocess.Popen([str(PROJECT_ROOT/"shared_venv"/"bin"/"python"),str(bot_core_path)],cwd=str(PROJECT_ROOT/"bot_core"),env=process_env)
  bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
  bot_dir.mkdir(parents=True,exist_ok=True)
  pid_file=bot_dir/"bot.pid"
  with open(pid_file,'w')as f:
   f.write(str(process.pid))
  logging.info(f"✅ Менеджер: Запущен бот {bot_id} (PID: {process.pid})")
  return True
 except Exception as e:
  logging.error(f"❌ Менеджер: Ошибка запуска бота {bot_id}: {e}")
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
    logging.info(f"⏹️ Менеджер: Остановлен бот {bot_id} (PID: {pid})")
   except psutil.NoSuchProcess:
    logging.info(f"⚠️ Менеджер: Процесс бота {bot_id} уже остановлен")
   except psutil.TimeoutExpired:
    try:
     process.kill()
     logging.warning(f"⚠️ Менеджер: Бот {bot_id} принудительно остановлен")
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
     logging.info(f"⏹️ Менеджер: Остановлен бот {bot_id} (PID: {proc.info['pid']})")
     return True
   except(psutil.NoSuchProcess,psutil.AccessDenied,psutil.TimeoutExpired):continue
  logging.warning(f"⚠️ Менеджер: Бот {bot_id} не найден среди процессов")
  return True
 except Exception as e:
  logging.error(f"❌ Менеджер: Ошибка остановки бота {bot_id}: {e}")
  return False
def check_bot_status(bot_id:int)->str:
 try:
  bot_dir=PROJECT_ROOT/"bots"/f"bot_{bot_id}"
  pid_file=bot_dir/"bot.pid"
  if pid_file.exists():
   with open(pid_file,'r')as f:
    pid=int(f.read().strip())
   if psutil.pid_exists(pid):
    try:
     process=psutil.Process(pid)
     if process.is_running()and process.status()!=psutil.STATUS_ZOMBIE:
      return"online"
    except psutil.NoSuchProcess:pass
  return"offline"
 except Exception as e:
  logging.error(f"❌ Менеджер: Ошибка проверки статуса бота {bot_id}: {e}")
  return"unknown"
def auto_start_bots():
 conn=get_db_connection()
 try:
  cursor=conn.cursor()
  cursor.execute("SELECT b.id,b.token,b.env_path FROM bots b JOIN subscriptions s ON b.user_id=s.user_id WHERE s.is_active=1 AND s.payment_status='paid' AND s.end_date>CURRENT_TIMESTAMP")
  bots=cursor.fetchall()
  for bot in bots:
   bot_id=bot['id']
   token=bot['token']
   env_path=Path(bot['env_path'])
   current_status=check_bot_status(bot_id)
   if current_status!="online":
    logging.info(f"🔄 Менеджер: Автозапуск бота {bot_id}")
    start_bot_process(bot_id,token,env_path)
   else:
    logging.info(f"✅ Менеджер: Бот {bot_id} уже запущен")
 except Exception as e:
  logging.error(f"❌ Менеджер: Ошибка автозапуска ботов: {e}")
 finally:
  conn.close()
def monitor_bots():
 conn=get_db_connection()
 try:
  cursor=conn.cursor()
  cursor.execute("SELECT id FROM bots")
  bots=cursor.fetchall()
  for bot in bots:
   bot_id=bot['id']
   status=check_bot_status(bot_id)
   if status=="offline":
    cursor.execute("SELECT b.token,b.env_path FROM bots b JOIN subscriptions s ON b.user_id=s.user_id WHERE b.id=? AND s.is_active=1 AND s.payment_status='paid' AND s.end_date>CURRENT_TIMESTAMP",(bot_id,))
    bot_info=cursor.fetchone()
    if bot_info:
     logging.info(f"🔄 Менеджер: Автовосстановление бота {bot_id}")
     start_bot_process(bot_id,bot_info['token'],Path(bot_info['env_path']))
 except Exception as e:
  logging.error(f"❌ Менеджер: Ошибка мониторинга ботов: {e}")
 finally:
  conn.close()
def run_scheduler():
 def scheduler_thread():
  schedule.every(5).minutes.do(monitor_bots)
  while True:
   schedule.run_pending()
   time.sleep(60)
 thread=Thread(target=scheduler_thread,daemon=True)
 thread.start()
if __name__=="__main__":
 logging.info("🚀 Запуск независимого менеджера ботов...")
 auto_start_bots()
 run_scheduler()
 logging.info("✅ Менеджер ботов запущен и работает")
 try:
  while True:
   time.sleep(60)
 except KeyboardInterrupt:
  logging.info("⏹️ Остановка менеджера ботов...")