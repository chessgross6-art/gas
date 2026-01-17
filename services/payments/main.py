import os
import hashlib
import json
import time
import uvicorn
import httpx
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query

load_dotenv(dotenv_path='.env.bot')

app = FastAPI()

APPWRITE_ENDPOINT = os.getenv('APPWRITE_ENDPOINT')
PROJECT_ID = os.getenv('PROJECT_ID')
API_KEY = os.getenv('API_KEY')
DATABASE_ID = os.getenv('DATABASE_ID')
COLLECTION_PROFILES = os.getenv('COLLECTION_PROFILES', 'profiles')

VEPAY_MCH_ID = os.getenv('VEPAY_MCH_ID') 
VEPAY_SECRET = os.getenv('VEPAY_SECRET_KEY')
VEPAY_API_URL = os.getenv('VEPAY_API_URL', 'https://api.vepay.online/merchant/pay')
MY_WEBHOOK_URL = os.getenv('MY_WEBHOOK_URL', 'http://localhost:8000/webhook/vepay') 

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(PROJECT_ID)
client.set_key(API_KEY)
db = Databases(client)

class PaymentRequest(BaseModel):
    user_id: str
    amount: float = 199.00

def generate_x_token(secret_key: str, json_body: str) -> str:
    if not secret_key: return "NO_SECRET_KEY"
    sha1_key = hashlib.sha1(secret_key.encode('utf-8')).hexdigest()
    sha1_body = hashlib.sha1(json_body.encode('utf-8')).hexdigest()
    token_str = sha1_key + sha1_body
    return hashlib.sha1(token_str.encode('utf-8')).hexdigest()

@app.get("/")
def home():
    return {"status": "Интеграция Vepay работает"}

@app.post("/pay/create")
async def create_payment_link(pay_req: PaymentRequest):
    extid = f"SUB-{pay_req.user_id}-{int(time.time())}"
    
    payload = {
        "amount": int(pay_req.amount), 
        "extid": extid,       
        "descript": "Подписка Voice AI",
        "timeout": 1800,         
        "successurl": "https://google.com", 
        "failurl": "https://google.com",
        "cancelurl": "https://google.com",
        "postbackurl": MY_WEBHOOK_URL, 
    }
    
    json_body = json.dumps(payload)
    token = generate_x_token(VEPAY_SECRET, json_body)
    
    headers = {
        "Content-Type": "application/json",
        "X-Login": VEPAY_MCH_ID,
        "X-Token": token
    }
    
    try:
        print(f"Отправка запроса в Vepay: {VEPAY_API_URL}")
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(VEPAY_API_URL, content=json_body, headers=headers)
            
        print(f"Ответ Vepay (код {response.status_code}): {response.text[:200]}")

        try:
            resp_data = response.json()
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=502, 
                detail=f"Ошибка Vepay (вернул не JSON, код {response.status_code}). Проверьте URL и ID мерчанта."
            )
            
        if response.status_code != 200 or "url" not in resp_data:
            error_msg = resp_data.get('message') or resp_data.get('name') or "Неизвестная ошибка"
            raise HTTPException(status_code=400, detail=f"Vepay Error: {error_msg}")
            
        return {
            "payment_url": resp_data["url"],
            "order_id": extid,
            "vepay_id": resp_data.get("id")
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@app.api_route("/webhook/vepay", methods=["GET", "POST"])
async def vepay_webhook(request: Request):
    data = {}
    if request.method == "GET":
        data = dict(request.query_params)
    else:
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

    print(f"Вебхук получен: {data}")

    order_id = data.get('extid') or data.get('order_id')
    status = data.get('status')

    if str(status) not in ['1', 'success', 'paid']:
        return "OK" 

    user_id = None
    if order_id and order_id.startswith('SUB-'):
        parts = order_id.split('-')
        if len(parts) >= 2:
            user_id = parts[1]

    if not user_id:
        print("user_id не найден в номере заказа (order_id)")
        return "OK"
    
    try:
        profiles = db.list_documents(
            DATABASE_ID, COLLECTION_PROFILES, 
            queries=[Query.equal('user_id', user_id)]
        )
        
        if profiles['total'] > 0:
            doc_id = profiles['documents'][0]['$id']
            if not profiles['documents'][0].get('is_pro'):
                db.update_document(DATABASE_ID, COLLECTION_PROFILES, doc_id, {'is_pro': True})
                print(f"Пользователь {user_id} обновлен до PRO")
        else:
            db.create_document(DATABASE_ID, COLLECTION_PROFILES, 'unique()', 
                {'user_id': user_id, 'is_pro': True, 'username': 'Неизвестный'})
            print(f"Пользователь {user_id} создан и обновлен до PRO")
                
        return "OK" 
        
    except Exception as e:
        print(f"Ошибка БД: {e}")
        return "OK"

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)