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

# --- 0. ข้อมูลเวอร์ชัน ---
BOT_VERSION = "1.3.0"
LAST_UPDATE = "12/02/2026"

app = Flask(__name__)

# --- 1. ตั้งค่า LINE API ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'a0b27ece169f30e2a3574f5717497e27')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. เชื่อมต่อ Firebase ---
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'
    })

ref = db.reference('share_circle')
tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state(): return ref.get() or {}
def get_now_str(): return datetime.datetime.now(tz_bangkok).strftime('%d/%m/%Y %H:%M')

# --- 3. ระบบนับถอยหลัง 2 จังหวะ ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30)
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"].get("current_price") == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ 30 วิสุดท้าย! ยอดปัจจุบัน {bid_amount} บ. มีใครสู้เพิ่มไหม?"))
        for i in range(10, 0, -1):
            curr = get_state()
            if not curr.get("auction", {}).get("is_active") or curr["auction"].get("current_price") != bid_amount:
                return 
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            time.sleep(3)
        
        final_state = get_state()
        if final_state.get("auction", {}).get("is_active") and final_state["auction"].get("current_price") == bid_amount:
            winner = final_state["auction"].get("winner_name")
            now_date = get_now_str().split()[0]
            ref.child('auction').update({"is_active": False})
            
            history = final_state.get("winners_history", [])
            history.append({"name": winner, "date": now_date, "bid": bid_amount})
            ref.update({"winners_history": history, "won_names": final_state.get("won_names", []) + [winner]})
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"🏁 ปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดบิด: {bid_amount} บ.\n⚠️ รบกวนส่งเลขบัญชีด้วยครับ"))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id
    try:
        profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
        name = profile.display_name
        ref.child('members').child(user_id).update({"name": name, "has_paid": True})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับสลิปคุณ {name} เรียบร้อย!"))
    except: pass

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    # --- คำสั่งพื้นฐาน ---
    if text == "ตั้งค่าวงแชร์":
        ref.update({"setup_step": 1, "won_names": [], "winners_history": [], "reminded": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 เริ่มตั้งค่าใหม่\n1. ยอดส่งต่อคนเท่าไหร่? (ตัวเลข)"))
        return

    if text == "/version":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 เวอร์ชัน: {BOT_VERSION}\nอัปเดต: {LAST_UPDATE}"))
        return

    # --- Setup Logic ---
    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1:
            ref.update({"share_amount": int(text), "setup_step": 2})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="2. สมาชิกทั้งหมดกี่คน?"))
        elif step == 2:
            ref.update({"total_members": int(text), "setup_step": 3})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="3. ยอดบิดขั้นต่ำกี่บาท?"))
        elif step == 3:
            ref.child('auction').update({"min_increment": int(text)})
            ref.update({"setup_step": 4})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="4. เงินกองกลางเริ่มต้นกี่บาท? (ถ้าไม่มีใส่ 0)"))
        elif step == 4:
            ref.update({"pot_balance": int(text), "setup_step": 5})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="5. วันที่เปียร์แชร์? (1-31)"))
        elif step == 5:
            ref.update({"play_date": text, "setup_step": 6})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="6. เวลาประมูล? (เช่น 20:00)"))
        elif step == 6:
            ref.update({"play_time": text, "setup_step": 0, "group_id": reply_to_id})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 ตั้งค่าสำเร็จ! เช็กสถานะได้ที่ /status"))
        return

    # --- คำสั่งบริหารจัดการ ---
    if text == "/start_bid":
        ref.child('auction').update({"is_active": True, "current_price": 0, "winner_name": "", "winner_id": ""})
        # ล้างสถานะการโอนของสมาชิกเมื่อเริ่มรอบใหม่
        members = state.get("members", {})
        for mid in members: ref.child('members').child(mid).update({"has_paid": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📢 @all เริ่มประมูล! บิดขั้นต่ำ {state.get('auction',{}).get('min_increment', 0)} บ."))
        return

    if text.startswith("/remove_winner"):
        name_to_remove = text.replace("/remove_winner", "").strip()
        won_names = state.get("won_names", [])
        if name_to_remove in won_names:
            won_names.remove(name_to_remove)
            ref.update({"won_names": won_names})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑 ลบคุณ {name_to_remove} ออกจากรายชื่อผู้ชนะแล้ว"))
        return

    if text == "/end_share":
        ref.set({}) # ล้างข้อมูลทั้งหมดใน Firebase
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ จบวงแชร์และล้างข้อมูลทั้งหมดเรียบร้อยแล้วครับ"))
        return

    if text == "/check_pay":
        members = state.get("members", {})
        paid = [m['name'] for m in members.values() if m.get('has_paid')]
        unpaid = [m['name'] for m in members.values() if not m.get('has_paid')]
        msg = f"💳 สถานะโอนเงิน\n✅ จ่ายแล้ว: {', '.join(paid) if paid else '-'}\n❌ ยังไม่โอน: {', '.join(unpaid) if unpaid else '-'}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/status":
        history = state.get("winners_history", [])
        hist_text = "\n".join([f"{i+1}. {h['name']} ({h['bid']}บ.)" for i, h in enumerate(history)])
        msg = (f"📊 สถานะวง\n💰 ส่ง: {state.get('share_amount')} บ.\n📈 บิดขั้นต่ำ: {state.get('auction',{}).get('min_increment')} บ.\n"
               f"💎 กองกลาง: {state.get('pot_balance', 0)} บ.\n🏆 ผู้ชนะแล้ว:\n{hist_text if hist_text else '-'}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- ระบบบิดราคา ---
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr = state["auction"].get("current_price", 0)
        min_inc = state["auction"].get("min_increment", 0)
        if bid >= (curr + min_inc):
            try:
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
                name = profile.display_name
                if name in state.get("won_names", []):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ คุณเปียร์ไปแล้ว ไม่มีสิทธิ์บิดครับ"))
                    return
                ref.child('auction').update({"current_price": bid, "winner_name": name})
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด {bid} โดย {name}"))
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        return

    if text == "/help":
        msg = (f"📖 คู่มือพี่รวย (V.{BOT_VERSION})\n"
               "• ตั้งค่าวงแชร์ : เริ่ม Setup ใหม่\n"
               "• /status : ดูสถานะ/กองกลาง/คนชนะ\n"
               "• /start_bid : เริ่มเปิดประมูล\n"
               "• /check_pay : เช็กคนโอนเงิน\n"
               "• /remove_winner [ชื่อ] : ลบชื่อผู้ชนะที่ใส่ผิด\n"
               "• /end_share : ล้างข้อมูลจบวงแชร์\n"
               "• /use_pot [ยอด] [เหตุผล] : บันทึกใช้เงิน\n"
               "• /version : เช็กเวอร์ชันปัจจุบัน")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)