import requests

from telegram_config import TELEGRAM_TOKEN, CHAT_ID  # 私密配置不入库（.gitignore）

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
response = requests.post(url, json={
    "chat_id": CHAT_ID,
    "text": "✅ Telegram test message works!",
    "parse_mode": "HTML"
})

print(response.status_code)
print(response.json())