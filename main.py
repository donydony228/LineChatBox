import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import re
import datetime
import uvicorn
from dotenv import load_dotenv

# 載入.env文件中的環境變數(本地開發用)
load_dotenv()

app = FastAPI()

# 從環境變數中獲取LINE認證資訊
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 簡單的價格資料 - 直接硬編碼在程式中
ROOM_PRICE = {
    "weekday": 2000,  # 平日價格
    "weekend": 2500   # 週末價格
}

# 判斷日期類型 (平日/週末)
def check_date_type(date):
    if date.weekday() >= 5:  # 週六或週日
        return "weekend"
    return "weekday"

# 解析日期 - 支援多種格式
def parse_date(date_str):
    patterns = [
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',  # YYYY/MM/DD or YYYY-MM-DD
        r'(\d{1,2})[/-](\d{1,2})',              # MM/DD or MM-DD
        r'(\d{1,2})月(\d{1,2})日'               # MM月DD日
    ]
    
    for pattern in patterns:
        match = re.search(pattern, date_str)
        if match:
            groups = match.groups()
            if len(groups) == 3:  # YYYY/MM/DD
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            elif len(groups) == 2:  # MM/DD or MM月DD日
                current_year = datetime.datetime.now().year
                month, day = int(groups[0]), int(groups[1])
                year = current_year
            
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None
    
    return None

@app.post("/callback")
async def callback(request: Request):
    # 獲取X-Line-Signature頭部值
    signature = request.headers.get('X-Line-Signature', '')
    
    # 獲取請求主體
    body = await request.body()
    body_text = body.decode('utf-8')
    
    # 處理webhook
    try:
        handler.handle(body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    return PlainTextResponse(content='OK')

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    
    # 檢查是否為日期查詢
    date = parse_date(text)
    
    if date:
        date_type = check_date_type(date)
        price = ROOM_PRICE[date_type]
        
        # 格式化回應訊息
        date_str = date.strftime('%Y年%m月%d日')
        response = f"{date_str}的標準房價格為 ${price}元。"
    else:
        response = "歡迎查詢房價！請輸入您想入住的日期，例如：\n2025/3/20\n3月20日\n3/20"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response)
    )

# 健康檢查端點 (Render會用來確認服務運行狀態)
@app.get("/")
def health_check():
    return {"status": "ok"}

# 以下代碼只在本地開發時運行
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)