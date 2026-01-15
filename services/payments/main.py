import os
import hashlib
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query
import time

load_dotenv(dotenv_path='.env.bot')

app = FastAPI()

APPWRITE_ENDPOINT = os.getenv('APPWRITE_ENDPOINT')
PROJECT_ID = os.getenv('PROJECT_ID')
API_KEY = os.getenv('API_KEY')
DATABASE_ID = os.getenv('DATABASE_ID')
COLLECTION_PROFILES = os.getenv('COLLECTION_PROFILES', 'profiles')

VEPAY_MCH_ID = os.getenv('VEPAY_MCH_ID')
VEPAY_SECRET = os.getenv('VEPAY_SECRET_KEY')
VEPAY_URL = os.getenv('VEPAY_API_URL', 'https://api.vepay.online/payment/create')

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(PROJECT_ID)
client.set_key(API_KEY)
db = Databases(client)

class PaymentRequest(BaseModel):
    user_id: str
    amount: float = 199.00

def generate_sign(mch_id, order_id, amount, currency, secret):
    raw_str = f"{mch_id}{order_id}{amount}{currency}{secret}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

@app.get("/")
def home():
    return {"status": "Vepay Integration Running"}

@app.post("/pay/create")
async def create_payment_link(pay_req: PaymentRequest):
    order_id = f"SUB-{pay_req.user_id}-{int(time.time())}"
    currency = "RUB"
    
    sign = generate_sign(VEPAY_MCH_ID, order_id, pay_req.amount, currency, VEPAY_SECRET)
    
    params = {
        "mch_id": VEPAY_MCH_ID,
        "order_id": order_id,
        "amount": pay_req.amount,
        "currency": currency,
        "sign": sign,
        "custom_field": pay_req.user_id,
        "desc": "Voice AI Pro Subscription"
    }
    
    try:
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        payment_url = f"{VEPAY_URL}?{query_string}"
        
        return {
            "payment_url": payment_url, 
            "order_id": order_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.all("/webhook/vepay") 
async def vepay_webhook(request: Request):
    if request.method == "GET":
        data = dict(request.query_params)
    else:
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

    req_sign = data.get('sign')
    
    mch_id = data.get('mch_id', VEPAY_MCH_ID)
    order_id = data.get('order_id')
    amount = data.get('amount')
    currency = data.get('currency', 'RUB')
    
    my_sign = generate_sign(mch_id, order_id, amount, currency, VEPAY_SECRET)
    
    if req_sign != my_sign:
        return {"error": "Invalid signature"}

    if 'order_id' not in data or 'status' not in data:
        return {"error": "Invalid params"}

    if data['status'] != 'success':
        return "OK" 

    user_id = data.get('custom_field')
    
    if not user_id and data.get('order_id', '').startswith('SUB-'):
        parts = data['order_id'].split('-')
        if len(parts) >= 2:
            user_id = parts[1]

    if not user_id:
        return "OK"
    
    try:
        profiles = db.list_documents(
            DATABASE_ID, COLLECTION_PROFILES, 
            queries=[Query.equal('user_id', user_id)]
        )
        
        if profiles['total'] > 0:
            doc_id = profiles['documents'][0]['$id']
            db.update_document(DATABASE_ID, COLLECTION_PROFILES, doc_id, {'is_pro': True})
        else:
            db.create_document(DATABASE_ID, COLLECTION_PROFILES, 'unique()', 
                {'user_id': user_id, 'is_pro': True})
                
        return "OK" 
        
    except Exception as e:
        print(f"DB Error: {e}")
        raise HTTPException(status_code=500, detail="DB Error")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)