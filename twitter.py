import os
import requests
import xml.etree.ElementTree as ET

TELEGRAM_TOKEN = "8847077981:AAHtmNitAv8FJEojD8ZgtiRgX7SiDZyIVWk"
TELEGRAM_CHAT_ID = "1509458456"

X_USER = "0xGwoni"
RSS_URL = f"https://rsshub.app/twitter/user/{X_USER}"
STATE_FILE = "last_tweet.txt"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try: requests.post(url, json=payload)
    except Exception as e: print(f"텔레그램 발송 에러: {e}")

def main():
    print(f"[{X_USER}] 트윗 확인 중...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(RSS_URL, headers=headers, timeout=20)
        if response.status_code != 200: return

        root = ET.fromstring(response.content)
        items = root.findall('.//item')
        if not items: return
            
        latest_item = items[0]
        latest_title = latest_item.find('title').text if latest_item.find('title') is not None else "내용 없음"
        latest_link = latest_item.find('link').text if latest_item.find('link') is not None else ""
        latest_guid = latest_item.find('guid').text if latest_item.find('guid') is not None else latest_link

        last_seen_guid = ""
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                last_seen_guid = f.read().strip()

        if latest_guid != last_seen_guid:
            print("🔥 새 트윗 발견!")
            message = f"🚨 <b>[X 실시간 알림] @{X_USER}</b>\n\n{latest_title}\n\n🔗 <a href='{latest_link}'>트윗 바로가기</a>"
            send_telegram_message(message)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                f.write(latest_guid)
    except Exception as e: print(f"오류: {e}")

if __name__ == "__main__":
    main()
