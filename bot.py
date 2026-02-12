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

# --- 1. ตั้งค่า LINE API ---
# ดึงจาก Environment Variable บน Cloud (Render/Heroku)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'a0b27ece169f30e2a3574f5717497e27')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. เชื่อมต่อ Firebase ---
# ตรวจสอบว่ามีไฟล์ serviceAccountKey.json ในเครื่อง/Server
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'
    })

ref = db.reference('share_circle')
tz_bangkok = pytz.timezone('Asia/Bangkok')

# --- ฟังก์ชันช่วยจัดการข้อมูล ---
def get_state():
    return ref.get() or {
        "group_id": None, "setup_step": 0, "share_amount": 1000,
        "play_date": "ระบุวันที่", "play_time": "20:00", "won_names": [],
        "pot_balance": 0, "reminder_sent": False, "members": {},
        "auction": {"is_active": False, "waiting_for_account": False, "payment_phase": False, 
                    "current_price": 0, "min_increment": 100, "winner_id": None, "winner_name": None}
    }

def update_db(data):
    ref.update(data)

# ==========================================
# ส่วนที่ 1: ระบบแจ้งเตือน และ เคานต์ดาวน์ประมูล
# ==========================================
def background_schedule_checker():
    while True:
        state = get_state()
        if state.get("play_date") != "ระบุวันที่" and state.get("group_id"):
            now = datetime.datetime.now(tz_bangkok)
            try:
                day = int(state["play_date"])
                hr, mn = map(int, state["play_time"].split(":"))
                target_time = now.replace(day=day, hour=hr, minute=mn, second=0)
                remind_time = target_time - datetime.timedelta(hours=4)
                
                if now.hour == remind_time.hour and now.minute == remind_time.minute:
                    if not state.get("reminder_sent"):
                        msg = f"📢 ประกาศจากกรรมการ! คืนนี้เวลา {state['play_time']} น. จะเริ่มเปิดประมูลแชร์นะครับ เตรียมตัวให้พร้อม!"
                        line_bot_api.push_message(state["group_id"], TextSendMessage(text=msg))
                        ref.update({"reminder_sent": True})
            except: pass
        time.sleep(60)

def warning_30s(reply_to_id, bid_amount):
    state = get_state()
    if state["auction"]["is_active"] and state["auction"]["current_price"] == bid_amount:
        msg = f"⏳ แง้มค้อนแล้ว! เหลืออีก 30 วินาทีสุดท้าย ยอดปัจจุบัน {bid_amount} บาท มีใครจะสู้เพิ่มไหมครับ?"
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))
        
        # หน่วงเวลาสั้นๆ ก่อนเริ่มนับถอยหลัง
        time.sleep(1)
        countdown_10s(reply_to_id, bid_amount)

def countdown_10s(reply_to_id, bid_amount):
    for i in range(10, 0, -1):
        state = get_state()
        if not state["auction"]["is_active"] or state["auction"]["current_price"] != bid_amount:
            return 
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
        time.sleep(3) # หน่วง 3 วินาทีตามที่ขอ
        
    state = get_state()
    if state["auction"]["is_active"] and state["auction"]["current_price"] == bid_amount:
        end_auction(reply_to_id)

def end_auction(reply_to_id):
    state = get_state()
    if state["auction"]["is_active"]:
        ref.child('auction').update({"is_active": False, "waiting_for_account": True})
        winner_name = state["auction"]["winner_name"]
        
        msg = f"🏁 ปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner_name}\n💰 ยอดหักเข้ากองกลาง: {state['auction']['current_price']} บาท\n\n⚠️ รบกวนคุณ {winner_name} พิมพ์เลขบัญชีและธนาคารส่งมาได้เลยครับ"
        
        won_list = state.get("won_names", [])
        if winner_name not in won_list:
            won_list.append(winner_name)
            ref.update({"won_names": won_list})
        
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))

# ==========================================
# ส่วนที่ 2: จัดการข้อความ และ Setup
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    state = get_state()
    text = event.message.text.strip()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id
    
    # ดึงชื่อสมาชิก
    try:
        if hasattr(event.source, 'group_id'):
            profile = line_bot_api.get_group_member_profile(reply_to_id, user_id)
            ref.update({"group_id": reply_to_id})
        else:
            profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name
    except: user_name = "สมาชิก"

    # บันทึกสมาชิกเข้า Firebase (Auto-Join)
    ref.child('members').child(user_id).update({"name": user_name})

    # --- เมนู /help ---
    if text == "/help":
        msg = ("📖 เมนูพี่รวย:\n- พิมพ์ 'ตั้งค่าวงแชร์'\n- /status\n- /start_bid\n- /reset_circle\n- /use_pot [ยอด] [เหตุผล]")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- Setup Wizard ---
    if text == "ตั้งค่าวงแชร์":
        ref.update({"setup_step": 1})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เริ่มตั้งค่าครับ 📝 ยอดเงินส่งต่อคนเท่าไหร่? (พิมพ์ตัวเลข)"))
        return

    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1 and text.isdigit():
            ref.update({"share_amount": int(text), "setup_step": 2})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📈 บิดขั้นต่ำเพิ่มครั้งละกี่บาท?"))
        elif step == 2 and text.isdigit():
            ref.child('auction').update({"min_increment": int(text)})
            ref.update({"setup_step": 3})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📅 เปียร์วันที่เท่าไหร่ของเดือน? (ตัวเลขวันที่)"))
        elif step == 3:
            ref.update({"play_date": text, "setup_step": 4})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕗 เริ่มประมูลตอนกี่โมง? (เช่น 20:00)"))
        elif step == 4:
            ref.update({"play_time": text, "setup_step": 5})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏆 คนเคยได้แล้วมีใครบ้าง? (ถ้าไม่มีพิมพ์ 'ไม่มี')"))
        elif step == 5:
            if text != "ไม่มี":
                names = [n.strip() for n in text.replace("@", "").split()]
                ref.update({"won_names": names})
            ref.update({"setup_step": 6})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💎 เงินกองกลางสะสมปัจจุบันกี่บาท?"))
        elif step == 6 and text.isdigit():
            ref.update({"pot_balance": int(text), "setup_step": 0})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 ตั้งค่าสำเร็จ! พี่รวยพร้อมรับใช้ครับ"))
        return

    # --- ระบบประมูล ---
    if text == "/start_bid":
        new_auction = {"is_active": True, "waiting_for_account": False, "payment_phase": False, "current_price": 0, "winner_id": None, "winner_name": None, "min_increment": state["auction"]["min_increment"]}
        ref.child('auction').update(new_auction)
        # รีเซ็ตสถานะการโอนเงินของสมาชิก
        for uid in state.get("members", {}):
            ref.child('members').child(uid).update({"has_paid": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📢 เปิดประมูล! บิดขั้นต่ำตามที่ตั้งไว้ ใครจะสู้พิมพ์ตัวเลขเลย!"))
        return

    if text.isdigit() and state["auction"]["is_active"]:
        if user_name in state.get("won_names", []): return
        bid = int(text)
        curr = state["auction"]["current_price"]
        min_inc = state["auction"]["min_increment"]
        required = curr + min_inc if curr > 0 else min_inc
        
        if bid >= required:
            ref.child('auction').update({"current_price": bid, "winner_id": user_id, "winner_name": user_name})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด! {bid} บ. โดยคุณ {user_name} (รอ 30 วิ)"))
            # เริ่มจับเวลาใหม่
            threading.Thread(target=warning_30s, args=[reply_to_id, bid]).start()
        return

    # --- รับเลขบัญชี ---
    if state["auction"].get("waiting_for_account") and user_id == state["auction"].get("winner_id"):
        ref.child('auction').update({"waiting_for_account": False, "payment_phase": True})
        ref.child('members').child(user_id).update({"has_paid": True})
        ref.update({"pot_balance": state["pot_balance"] + state["auction"]["current_price"]})
        msg = f"📊 สรุปยอดโอนรอบนี้\n🏆 ผู้รับเงิน: คุณ {user_name}\n🏦 บัญชี: {text}\n💸 สมาชิกท่านอื่นโอน {state['share_amount']} บ. แล้วส่งสลิปได้เลย!"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

# ==========================================
# ส่วนที่ 3: ตรวจจับสลิปโอนเงินอัตโนมัติ
# ==========================================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id
    
    if state["auction"].get("payment_phase"):
        try:
            if hasattr(event.source, 'group_id'):
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id)
            else:
                profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
        except: user_name = "สมาชิก"

        # เช็คว่ายังไม่ได้บันทึกว่าจ่าย
        member_info = state.get("members", {}).get(user_id, {})
        if not member_info.get("has_paid"):
            ref.child('members').child(user_id).update({"has_paid": True})
            unpaid = sum(1 for m in get_state()["members"].values() if not m.get("has_paid"))
            
            msg = f"✅ ได้รับสลิปจากคุณ {user_name} แล้วครับ\n(ขาดอีก {unpaid} ท่านจะครบวง)"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            
            if unpaid == 0:
                line_bot_api.push_message(reply_to_id, TextSendMessage(text="🎉 ทุกคนโอนครบแล้ว ปิดจ็อบครับ!"))

if __name__ == "__main__":
    threading.Thread(target=background_schedule_checker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)