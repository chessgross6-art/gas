import time
import os
import re
import asyncio
import requests
import warnings
import edge_tts 
from datetime import datetime, timezone
from dotenv import load_dotenv
from ddgs import DDGS
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.query import Query
from appwrite.input_file import InputFile

warnings.filterwarnings("ignore", category=DeprecationWarning, module="appwrite")
load_dotenv(dotenv_path='.env')

PROJECT_ID = os.getenv('PROJECT_ID')
API_KEY = os.getenv('API_KEY')
DATABASE_ID = os.getenv('DATABASE_ID')
COLLECTION_MESSAGES = os.getenv('COLLECTION_MESSAGES', 'messages')
COLLECTION_PROFILES = os.getenv('COLLECTION_PROFILES', 'profiles')
COLLECTION_CHATS = os.getenv('COLLECTION_CHATS', 'chats')
COLLECTION_SETTINGS = os.getenv('COLLECTION_SETTINGS', 'system_settings')
BUCKET_ID = os.getenv('BUCKET_ID')
APPWRITE_ENDPOINT = os.getenv('APPWRITE_ENDPOINT', 'http://localhost/v1')

OLLAMA_HOST = os.getenv('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
MODEL_NAME = os.getenv('MODEL_NAME', 'gemma2:2b')

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(PROJECT_ID)
client.set_key(API_KEY)

db = Databases(client)
storage = Storage(client)
processed_ids = set()

#Просто переменные, потом подгружаем с бж
PROMPT_LITE = "Ты веселый друг. Шути"
PROMPT_PRO = "Ты профессионал. Отвечай кратко"
LAST_PROMPT_UPDATE = 0

def update_prompts_cache():
    global PROMPT_LITE, PROMPT_PRO, LAST_PROMPT_UPDATE
    
    if time.time() - LAST_PROMPT_UPDATE < 60:
        return

    try:
        docs = db.list_documents(DATABASE_ID, COLLECTION_SETTINGS)
        for doc in docs['documents']:
            if doc['key'] == 'prompt_lite':
                PROMPT_LITE = doc['value']
            elif doc['key'] == 'prompt_pro':
                PROMPT_PRO = doc['value']
        
        LAST_PROMPT_UPDATE = time.time()
    except Exception as e:
        print(f"Ошибка обновления конфига: {e}")

def get_user_status(chat_id):
    try:
        chat_doc = db.get_document(DATABASE_ID, COLLECTION_CHATS, chat_id)
        user_id = chat_doc['user_id']
        profiles = db.list_documents(DATABASE_ID, COLLECTION_PROFILES, queries=[Query.equal('user_id', user_id)])
        
        if profiles['total'] > 0:
            return profiles['documents'][0].get('is_pro', False)
        return False
    except:
        return False

def search_web(query):
    try:
        results_text = ""
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=2))
            if not results: return None
            for res in results:
                results_text += f"- {res['body']}\n"
        return results_text
    except Exception as e:
        print(f"Ошибка поиска: {e}")
        return None

def ai_decide_search(text):
    prompt = f"Вопрос: \"{text}\"\nТребует ли это поиска в интернете (новости, погода, факты)? Ответь SEARCH или TALK."
    try:
        response = requests.post(OLLAMA_GENERATE_URL, json={
            "model": MODEL_NAME, "prompt": prompt, "stream": False, "options": {"temperature": 0}
        })
        return "SEARCH" in response.json()['response'].strip().upper()
    except: return False

def clean_text_for_audio(text):
    text = text.replace('*', '').replace('#', '').replace('`', '')
    clean_text = re.sub(r'[^\w\s,!.?:\-а-яА-ЯёЁa-zA-Z0-9]', '', text)
    return re.sub(r'\s+', ' ', clean_text).strip()

def generate_voice(text, chat_id):
    filename = f"voice_{chat_id}_{int(time.time())}.mp3"
    try:
        text_for_audio = clean_text_for_audio(text)
        if not text_for_audio: return None

        VOICE = "ru-RU-DmitryNeural" 
        
        communicate = edge_tts.Communicate(text_for_audio, VOICE)
        asyncio.run(communicate.save(filename))
        
        result = storage.create_file(bucket_id=BUCKET_ID, file_id='unique()', file=InputFile.from_path(filename))
        file_id = result['$id']
        os.remove(filename)
        return file_id
    except Exception as e:
        print(f"Ошибка TTS (синтеза речи): {e}")
        if os.path.exists(filename): os.remove(filename)
        return None

def ask_ollama(last_message, chat_id, user_forced_search=False):
    update_prompts_cache()
    
    is_pro = get_user_status(chat_id)
    context_data = ""
    system_prompt = ""

    if is_pro:
        system_prompt = PROMPT_PRO 
        if user_forced_search or ai_decide_search(last_message):
            search_result = search_web(last_message)
            if search_result:
                context_data = f"ДАННЫЕ ИЗ ПОИСКА:\n{search_result}\n"
    else:
        system_prompt = PROMPT_LITE
        if user_forced_search or ai_decide_search(last_message):
            context_data = "(Пользователь запросил поиск, но подписка отсутствует. Вежливо откажи в поиске актуальных данных.)"

    try:
        history = db.list_documents(DATABASE_ID, COLLECTION_MESSAGES, queries=[
            Query.equal('chat_id', chat_id), Query.order_desc('$createdAt'), Query.limit(4)
        ])
        past_messages = history['documents'][::-1]
    except: past_messages = []

    messages = [{"role": "system", "content": system_prompt}]
    
    for doc in past_messages:
        if doc['text'] == last_message: continue
        role = "user" if doc['sender'] == 'user' else "assistant"
        messages.append({"role": role, "content": doc['text']})
    
    forced_user_message = (
        f"ИНСТРУКЦИЯ РОЛИ: {system_prompt}\n"
        f"----------------\n"
        f"ВВОД ПОЛЬЗОВАТЕЛЯ: {last_message}"
    )

    if context_data:
        forced_user_message = context_data + "\n" + forced_user_message

    messages.append({"role": "user", "content": forced_user_message})

    try:
        response = requests.post(OLLAMA_CHAT_URL, json={"model": MODEL_NAME, "messages": messages, "stream": False})
        return response.json()['message']['content']
    except Exception:
        return "Ошибка генерации ответа"

def main():
    print("Воркер запущен")
    
    start_time = datetime.now(timezone.utc)
    
    update_prompts_cache()

    try:
        docs = db.list_documents(DATABASE_ID, COLLECTION_MESSAGES, [Query.limit(50)])
        for doc in docs['documents']: 
            processed_ids.add(doc['$id'])
    except: pass

    while True:
        try:
            resp = db.list_documents(DATABASE_ID, COLLECTION_MESSAGES, [
                Query.equal('sender', 'user'), 
                Query.order_desc('$createdAt'), 
                Query.limit(5)
            ])
            for msg in resp['documents']:
                msg_id = msg['$id']

                if msg_id in processed_ids:
                    continue

                try:
                    created_at = datetime.fromisoformat(msg['$createdAt'].replace('Z', '+00:00'))
                    if created_at < start_time:
                        processed_ids.add(msg_id)
                        continue
                except Exception as e:
                    print(f"Ошибка парсинга даты: {e}")
                    processed_ids.add(msg_id)
                    continue

                print(f"Обработка сообщения: {msg['text']}")
                processed_ids.add(msg_id)
                    
                user_forced_search = msg.get('search_enabled', False)
                ai_text = ask_ollama(msg['text'], msg['chat_id'], user_forced_search)
                print(f"Ответ AI: {ai_text}")
                    
                audio_id = generate_voice(ai_text, msg['chat_id'])
                    
                db.create_document(DATABASE_ID, COLLECTION_MESSAGES, 'unique()', {
                    'chat_id': msg['chat_id'], 'sender': 'ai', 'text': ai_text, 
                    'is_voice': True, 'audio_file_id': audio_id
                })
                
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка в цикле обработки: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()