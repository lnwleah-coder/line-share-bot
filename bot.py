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
BOT_VERSION = "1.2.0"
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

# --- ฟังก์ชันช่วยจัดการข้อมูล ---
def get_state(): return ref.get() or {}
def get_now_str(): return datetime.datetime.now(tz_bangkok).strftime('%d/%m/%Y %H:%M')

# --- 3. ระบบแจ้งเตือน 4 ชม. & แท็ก @all ---
def bg_schedule_checker():
    while True:
        state = get_state()
        if state.get("play_date") and state.get("group_id"):
            now = datetime.datetime.now(tz_bangkok)
            try:
                day = int(state["play_date"])
                hr, mn = map(int, state["play_time"].split(":"))
                target = now.replace(day=day, hour=hr, minute=mn, second=0)
                remind = target - datetime.timedelta(hours=4)
                
                if now.hour == remind.hour and now.minute == remind.minute:
                    if not state.get("reminded"):
                        msg = f"📢 ประกาศจากพี่รวย! อีก 4 ชม. จะเริ่มประมูลแชร์เวลา {state['play_time']} น. เตรียมตัวให้พร้อมครับ!"
                        line_bot_api.push_message(state["group_id"], TextSendMessage(text=f"📢 @all {msg}"))
                        ref.update({"reminded": True})
            except: pass
        time.sleep(60)

# --- 4. ระบบนับถอยหลัง 2 จังหวะ (30 วิ + นับ 10-1 ทุก 3 วิ) ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) # จังหวะที่ 1: รอ 30 วินาที
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! เหลือ 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครสู้เพิ่มไหม?"))
        
        # จังหวะที่ 2: นับถอยหลัง 10-1 ทุก 3 วินาที
        for i in range(10, 0, -1):
            curr = get_state()
            if not curr.get("auction", {}).get("is_active") or curr["auction"]["current_price"] != bid_amount:
                return # Anti-Sniping: หยุดนับถ้ามีคนบิดใหม่
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            time.sleep(3)
        
        # ปิดประมูล
        final_state = get_state()
        if final_state.get("auction", {}).get("is_active") and final_state["auction"]["current_price"] == bid_amount:
            winner = final_state["auction"]["winner_name"]
            now_date = get_now_str().split()[0]
            ref.child('auction').update({"is_active": False, "waiting_for_account": True})
            
            # บันทึกประวัติคนชนะ
            history = final_state.get("winners_history", [])
            history.append({"name": winner, "date": now_date, "bid": bid_amount})
            ref.update({"winners_history": history, "won_names": final_state.get("won_names", []) + [winner]})

            msg = f"🏁 ปิดประมูลเรียบร้อย!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดบิด: {bid_amount} บ.\n📅 วันที่ชนะ: {now_date}\n⚠️ รบกวนส่งเลขบัญชีด้วยครับ"
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

# --- 5. จัดการรูปภาพ (สลิปโอนเงินอัตโนมัติ) ---
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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับสลิปคุณ {name} แล้ว!\n📊 จ่ายแล้ว: {paid_count}/{total} คน"))
    except: pass

# --- 6. จัดการข้อความ & เมนูคำสั่ง ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    # [คำสั่งตรวจสอบเวอร์ชัน]
    if text == "/version":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 พี่รวยร่างทอง\nเวอร์ชัน: {BOT_VERSION}\nอัปเดตล่าสุด: {LAST_UPDATE}"))
        return

    # [คำสั่ง /status แบบละเอียด]
    if text == "/status":
        history = state.get("winners_history", [])
        hist_text = "\n".join([f"{i+1}. {h['name']} | {h['date']} | {h['bid']} บ." for i, h in enumerate(history)]) if history else "ยังไม่มี"
        pot_use = state.get("pot_usage", [])
        total_used = sum(u['amount'] for u in pot_use)
        msg = (f"📊 สรุปสถานะวงแชร์\n📅 วันที่: {get_now_str()}\n"
               f"💰 วง: ส่ง {state.get('share_amount')} บ. | บิดขั้นต่ำ {state.get('auction',{}).get('min_increment')} บ.\n"
               f"⏰ นัดประมูล: วันที่ {state.get('play_date')} เวลา {state.get('play_time')} น.\n\n"
               f"🏆 ประวัติคนเปียร์ได้แล้ว:\n{hist_text}\n\n"
               f"💎 เงินกองกลาง:\n- ยอดสะสม: {state.get('pot_balance', 0)} บ.\n- ใช้ไปแล้ว: {total_used} บ.\n- คงเหลือ: {state.get('pot_balance', 0) - total_used} บ.")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # [เริ่มประมูล พร้อมแท็ก @all]
    if text == "/start_bid":
        ref.child('auction').update({"is_active": True, "current_price": 0})
        for uid in state.get("members", {}): ref.child('members').child(uid).update({"has_paid": False})
        msg = f"📢 พี่รวยเปิดประมูลรอบวันที่ {get_now_str().split()[0]}! 📈 บิดขั้นต่ำ: {state.get('auction',{}).get('min_increment')} บ. เริ่มบิดได้เลยครับ!"
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"📢 @all {msg}"))
        return

    # [ระบบบิดราคา & ตรวจสอบขั้นต่ำ]
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr = state.get("auction", {}).get("current_price", 0)
        min_inc = state.get("auction", {}).get("min_increment", 100)
        required = curr + min_inc if curr > 0 else min_inc
        if bid >= required:
            try:
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
                name = profile.display_name
                if name in state.get("won_names", []): return
                ref.child('auction').update({"current_price": bid, "winner_name": name, "winner_id": user_id})
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด {bid} บ. โดย {name}"))
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ ยอดบิดไม่ถึงเกณฑ์! ต้องอย่างน้อย {required} บ."))
        return

    # [เมนูช่วยเหลือ /help]
    if text == "/help":
        msg = (f"📖 คู่มือพี่รวย (V.{BOT_VERSION})\n"
               "- ตั้งค่าวงแชร์ : เริ่มตั้งค่าใหม่\n"
               "- /status : สรุปสถานะและกองกลาง\n"
               "- /start_bid : เปิดประมูล (แท็ก @all)\n"
               "- /check_pay : เช็กรายชื่อคนโอน\n"
               "- /use_pot [ยอด] [เหตุผล] : หักกองกลาง\n"
               "- /version : ตรวจสอบเวอร์ชันบอท")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

if __name__ == "__main__":
    threading.Thread(target=bg_schedule_checker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)