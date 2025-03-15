import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import re
import datetime
import uvicorn
from datetime import timedelta
from dotenv import load_dotenv

# 載入.env文件中的環境變數(本地開發用)
load_dotenv()

app = FastAPI()

# 從環境變數中獲取LINE認證資訊
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 定義基本價格策略
BASE_PRICE = {
    "weekday": 2000,    # 平日價格
    "friday": 2500,     # 週五價格
    "weekend": 2800,    # 週六價格
    "sunday": 2300,     # 週日價格
    "holiday": 3000     # 連假價格
}

# 基於人數的房間需求
ROOM_REQUIREMENTS = {
    (1, 2): 1,  # 1-2人需要1間房
    (3, 4): 2,  # 3-4人需要2間房
    (5, 6): 3,  # 5-6人需要3間房
    (7, 8): 3,  # 7-8人需要3間房+加床
}

# 額外費用
EXTRA_FEES = {
    "extra_bed": 800,  # 加床費用
}

# 台灣2025年連假日期 (可根據實際情況調整)
HOLIDAYS_2025 = [
    # 元旦
    datetime.date(2025, 1, 1),
    # 農曆新年 (假設2025年是2月初)
    datetime.date(2025, 2, 1), datetime.date(2025, 2, 2), 
    datetime.date(2025, 2, 3), datetime.date(2025, 2, 4), 
    datetime.date(2025, 2, 5),
    # 228和平紀念日
    datetime.date(2025, 2, 28),
    # 清明節
    datetime.date(2025, 4, 4), datetime.date(2025, 4, 5), 
    datetime.date(2025, 4, 6),
    # 勞動節
    datetime.date(2025, 5, 1),
    # 端午節
    datetime.date(2025, 6, 1), datetime.date(2025, 6, 2),
    # 中秋節
    datetime.date(2025, 9, 12), datetime.date(2025, 9, 13), 
    datetime.date(2025, 9, 14),
    # 國慶日
    datetime.date(2025, 10, 10),
    # 元旦連假
    datetime.date(2025, 12, 31)
]

# 使用者對話狀態
user_states = {}

# 判斷日期類型
def check_date_type(date):
    # 判斷是否為連假
    if date in HOLIDAYS_2025:
        return "holiday"
    
    # 判斷星期幾
    weekday = date.weekday()
    if weekday == 4:  # 週五
        return "friday"
    elif weekday == 5:  # 週六
        return "weekend"
    elif weekday == 6:  # 週日
        return "sunday"
    else:  # 平日 (週一至週四)
        return "weekday"

# 解析日期範圍 - 支援單日和多日範圍
def parse_date_range(date_str):
    # 嘗試匹配範圍格式
    range_patterns = [
        r'(\d{1,2})[/-](\d{1,2})\s*[-~到至]\s*(\d{1,2})[/-](\d{1,2})',  # MM/DD-MM/DD
        r'(\d{1,2})月(\d{1,2})日\s*[-~到至]\s*(\d{1,2})月(\d{1,2})日'   # MM月DD日-MM月DD日
    ]
    
    for pattern in range_patterns:
        match = re.search(pattern, date_str)
        if match:
            groups = match.groups()
            current_year = datetime.datetime.now().year
            
            # 解析起始日和結束日
            start_month, start_day = int(groups[0]), int(groups[1])
            end_month, end_day = int(groups[2]), int(groups[3])
            
            try:
                start_date = datetime.date(current_year, start_month, start_day)
                end_date = datetime.date(current_year, end_month, end_day)
                
                # 處理跨年的情況
                if end_month < start_month:
                    end_date = datetime.date(current_year + 1, end_month, end_day)
                
                return (start_date, end_date)
            except ValueError:
                return None
    
    # 如果不是範圍格式，嘗試解析單一日期
    single_date = parse_single_date(date_str)
    if single_date:
        # 對於單一日期，假設住一晚
        return (single_date, single_date)
    
    return None

# 解析單一日期
def parse_single_date(date_str):
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

# 解析人數
def parse_guest_count(text):
    patterns = [
        r'(\d+)\s*人',  # 例如: "4人"
        r'(\d+)\s*位',  # 例如: "4位"
        r'(\d+)\s*大人',  # 例如: "4大人"
        r'(\d+)'  # 純數字
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                count = int(match.group(1))
                if 1 <= count <= 8:  # 確保人數在合理範圍內
                    return count
            except ValueError:
                return None
    
    return None

# 計算房間需求和價格
def calculate_room_requirements(guest_count):
    for (min_guests, max_guests), rooms in ROOM_REQUIREMENTS.items():
        if min_guests <= guest_count <= max_guests:
            needs_extra_bed = (guest_count >= 7)
            return rooms, needs_extra_bed
    
    return None, None

# 計算指定日期、人數的住宿價格
def calculate_price(start_date, end_date, guest_count):
    rooms, needs_extra_bed = calculate_room_requirements(guest_count)
    if rooms is None:
        return None
    
    days = []
    total_price = 0
    date_range_str = []
    
    current_date = start_date
    while current_date <= end_date:
        date_type = check_date_type(current_date)
        daily_price = BASE_PRICE[date_type] * rooms
        
        # 如果需要加床，額外收費
        extra_bed_fee = EXTRA_FEES["extra_bed"] if needs_extra_bed else 0
        
        daily_total = daily_price + extra_bed_fee
        total_price += daily_total
        
        date_str = current_date.strftime('%m/%d')
        type_desc = get_date_type_description(date_type)
        
        room_desc = f"{rooms}間房"
        if needs_extra_bed:
            room_desc += "+加床"
        
        price_breakdown = f"{BASE_PRICE[date_type]}元 x {rooms}間"
        if needs_extra_bed:
            price_breakdown += f" + 加床{EXTRA_FEES['extra_bed']}元"
        
        days.append({
            "date": date_str,
            "type": type_desc,
            "room_desc": room_desc,
            "price_breakdown": price_breakdown,
            "daily_total": daily_total
        })
        
        date_range_str.append(current_date.strftime('%Y年%m月%d日'))
        
        current_date += timedelta(days=1)
    
    return {
        "start_date": date_range_str[0],
        "end_date": date_range_str[-1],
        "nights": len(days),
        "rooms": rooms,
        "needs_extra_bed": needs_extra_bed,
        "daily_breakdown": days,
        "total_price": total_price,
        "guest_count": guest_count
    }

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
    user_id = event.source.user_id
    text = event.message.text
    
    # 檢查用戶當前狀態
    if user_id not in user_states:
        user_states[user_id] = {"state": "initial"}
    
    current_state = user_states[user_id]["state"]
    
    # 處理關鍵字"房價"
    if text == "房價":
        response = "歡迎查詢房價！\n\n請先輸入您計劃的入住日期，格式為：\n3/20\n3月20日\n或日期範圍：\n3/20-3/22\n3月20日-3月22日"
        user_states[user_id] = {"state": "awaiting_date"}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response)
        )
        return
    
    # 處理日期輸入
    if current_state == "awaiting_date":
        date_range = parse_date_range(text)
        if date_range:
            start_date, end_date = date_range
            user_states[user_id] = {
                "state": "awaiting_guests",
                "start_date": start_date,
                "end_date": end_date
            }
            response = f"感謝您提供入住日期！\n入住日期: {start_date.strftime('%Y年%m月%d日')}\n退房日期: {end_date.strftime('%Y年%m月%d日')}\n\n請問有幾位旅客入住？(請輸入1-8的數字)"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )
            return
        else:
            response = "抱歉，無法識別您輸入的日期格式。請使用以下格式：\n3/20\n3月20日\n或日期範圍：\n3/20-3/22\n3月20日-3月22日"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )
            return
    
    # 處理人數輸入
    if current_state == "awaiting_guests":
        guest_count = parse_guest_count(text)
        if guest_count:
            start_date = user_states[user_id]["start_date"]
            end_date = user_states[user_id]["end_date"]
            
            price_info = calculate_price(start_date, end_date, guest_count)
            
            if price_info:
                # 格式化回應訊息
                response = f"您的住宿報價如下：\n\n"
                response += f"入住日期：{price_info['start_date']}\n"
                response += f"退房日期：{price_info['end_date']}\n"
                response += f"入住人數：{price_info['guest_count']}人\n"
                response += f"住宿天數：{price_info['nights']}晚\n"
                
                room_desc = f"{price_info['rooms']}間房"
                if price_info['needs_extra_bed']:
                    room_desc += " + 加床"
                response += f"房間需求：{room_desc}\n\n"
                
                response += "每日價格明細：\n"
                for day in price_info['daily_breakdown']:
                    response += f"{day['date']} ({day['type']})：{day['price_breakdown']} = {day['daily_total']}元\n"
                
                response += f"\n總價：${price_info['total_price']}元"
                response += "\n\n如需預訂，請回覆「預訂」。如有其他問題，請回覆「房價」重新查詢。"
                
                user_states[user_id] = {"state": "quote_provided", "price_info": price_info}
            else:
                response = "抱歉，無法計算該人數的房價。請輸入1-8之間的人數。"
                
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )
            return
        else:
            response = "抱歉，請輸入有效的人數（1-8人）。例如：4人"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )
            return
    
    # 如果接收到「預訂」且已提供報價
    if text == "預訂" and current_state == "quote_provided":
        price_info = user_states[user_id].get("price_info")
        if price_info:
            response = f"感謝您的預訂！\n\n"
            response += f"入住日期：{price_info['start_date']}\n"
            response += f"退房日期：{price_info['end_date']}\n"
            response += f"入住人數：{price_info['guest_count']}人\n"
            response += f"總價：${price_info['total_price']}元\n\n"
            response += "我們已收到您的預訂請求，客服人員將盡快與您聯繫確認詳情。"
            
            user_states[user_id] = {"state": "initial"}  # 重置狀態
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )
            return
    
    # 處理其他未識別的輸入（當不是在等待日期或人數時）
    if text != "房價" and current_state == "initial":
        response = "您好！如需查詢房價，請輸入「房價」開始查詢流程，或等待小編盡快為您服務，謝謝！"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response)
        )

# 獲取日期類型的中文描述
def get_date_type_description(date_type):
    descriptions = {
        "weekday": "平日",
        "friday": "週五",
        "weekend": "週六",
        "sunday": "週日",
        "holiday": "連假"
    }
    return descriptions.get(date_type, date_type)

# 健康檢查端點 (Render會用來確認服務運行狀態)
@app.get("/")
def health_check():
    return {"status": "ok"}
# 以下代碼只在本地開發時運行
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)