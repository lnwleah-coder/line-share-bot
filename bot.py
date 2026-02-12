import os
import threading
import time
import datetime
import pytz
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage

app = Flask(__name__)

# --- 1. ตั้งค่า LINE API (ใช้ข้อมูลที่คุณให้มา) ---
# แนะนำให้ใช้ Environment Variables บน Render เพื่อความปลอดภัย
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'a0b27ece169f30e2a3574f5717497e27')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. เชื่อมต่อ Firebase ---
if not firebase_admin._apps:
    # ต้องอัปโหลดไฟล์ serviceAccountKey.json ขึ้น GitHub ด้วยนะครับ
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'
    })

ref = db.reference('share_circle')
tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state():
    return ref.get() or {}

# --- 3. ระบบนับถอยหลัง (30 วิ และ 10-1 ทุก 3 วิ) ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) # รอ 30 วินาทีแรก
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! เหลือ 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครสู้เพิ่มไหม?"))
        
        for i in range(10, 0, -1):
            curr_state = get_state()
            if not curr_state.get("auction", {}).get("is_active") or curr_state["auction"]["current_price"] != bid_amount:
                return 
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            time.sleep(3) # หน่วงเวลาเลขละ 3 วินาทีตามที่ต้องการ
        
        final_state = get_state()
        if final_state.get("auction", {}).get("is_active") and final_state["auction"]["current_price"] == bid_amount:
            winner = final_state["auction"]["winner_name"]
            ref.child('auction').update({"is_active": False, "waiting_for_account": True})
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"🏁 ปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดหัก: {bid_amount} บ.\n⚠️ ส่งเลขบัญชีมาได้เลยครับ"))
            
            won_list = final_state.get("won_names", [])
            if winner not in won_list:
                won_list.append(winner)
                ref.update({"won_names": won_list})

# --- 4. Webhook Callback ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 5. ยืนยันการจ่ายด้วยสลิป (รูปภาพ) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id
    
    try:
        profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
        name = profile.display_name
        ref.child('members').child(user_id).update({"name": name, "has_paid": True})
        
        paid_count = sum(1 for m in get_state().get("members", {}).values() if m.get("has_paid"))
        total = state.get("total_members_count", 0)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📸 พี่รวยรับสลิปคุณ {name} แล้ว!\n✅ จ่ายแล้ว: {paid_count}/{total} คน"))
    except: pass

# --- 6. จัดการข้อความ ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    if text == "/help":
        msg = ("📖 เมนูพี่รวย:\n- ตั้งค่าวงแชร์\n- /status\n- /start_bid\n- /check_pay\n- /reset_circle")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/start_bid":
        ref.child('auction').update({"is_active": True, "current_price": 0})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📢 พี่รวยเปิดประมูล! บิดมาเลยครับพี่น้อง!"))
        return

    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr = state.get("auction", {}).get("current_price", 0)
        min_inc = state.get("auction", {}).get("min_increment", 100)
        if bid >= (curr + min_inc if curr > 0 else min_inc):
            try:
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
                name = profile.display_name
                ref.child('auction').update({"current_price": bid, "winner_name": name, "winner_id": user_id})
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด {bid} บ. โดยคุณ {name}"))
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)