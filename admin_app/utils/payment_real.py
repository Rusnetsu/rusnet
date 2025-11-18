import os
import logging
import uuid
from typing import Dict
import yookassa
from yookassa import Payment,Configuration
from fastapi import Request
from pathlib import Path
from dotenv import load_dotenv
env_path=Path(__file__).parent.parent/'.env'
load_dotenv(env_path)
Configuration.account_id=os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key=os.getenv("YOOKASSA_SECRET_KEY")
async def create_yookassa_payment(amount:float,description:str,user_id:int,plan_type:str)->Dict:
    try:
        payment_id=str(uuid.uuid4())
        receipt_data={"customer":{"email":f"user{user_id}@myclienty.ru"},"items":[{"description":description,"quantity":"1.00","amount":{"value":f"{amount:.2f}","currency":"RUB"},"vat_code":"1","payment_mode":"full_payment","payment_subject":"service"}]}
        payment=Payment.create({"amount":{"value":f"{amount:.2f}","currency":"RUB"},"payment_method_data":{"type":"bank_card"},"confirmation":{"type":"redirect","return_url":os.getenv("YOOKASSA_RETURN_URL")},"capture":True,"description":description,"receipt":receipt_data,"metadata":{"user_id":user_id,"plan_type":plan_type,"payment_id":payment_id}})
        logging.info(f"✅ Создан РЕАЛЬНЫЙ платеж ЮKassa: {payment.id} для пользователя {user_id}")
        return{"success":True,"yookassa_payment_id":payment.id,"confirmation_url":payment.confirmation.confirmation_url,"payment_id":payment_id,"status":payment.status}
    except Exception as e:
        logging.error(f"❌ Ошибка создания реального платежа ЮKassa: {e}")
        return{"success":False,"error":str(e)}
async def check_yookassa_payment(payment_id:str)->Dict:
    try:
        payment=Payment.find_one(payment_id)
        return{"success":True,"paid":payment.status=="succeeded","status":payment.status,"amount":float(payment.amount.value),"description":payment.description,"metadata":payment.metadata}
    except Exception as e:
        logging.error(f"❌ Ошибка проверки платежа {payment_id}: {e}")
        return{"success":False,"paid":False,"error":str(e)}
def validate_ipn_request(request:Request)->bool:
    try:
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка валидации IPN запроса: {e}")
        return False