import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
import threading, time, datetime
import pytz

# --- 1. เชื่อมต่อ Firebase ---
# ตรวจสอบให้แน่ใจว่าไฟล์ serviceAccountKey.json อยู่ในโฟลเดอร์เดียวกับ bot.py
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'
})
ref = db.reference('share_circle')

app = Flask(__name__)

# --- 2. ตั้งค่า LINE API ---
line_bot_api = LineBotApi('7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('a0b27ece169f30e2a3574f5717497e27')

tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state(): return ref.get() or {}
def update_db(path, value): ref.child(path).set(value)

# --- 3. ระบบ Background Checker (แจ้งเตือน & เปิดประมูล) ---
def bg_schedule_checker():
    while True:
        state = get_state()
        if state.get("play_date") != "ระบุวันที่" and state.get("group_id"):
            now = datetime.datetime.now(tz_bangkok)
            try:
                day = int(state["play_date"])
                hr, mn = map(int, state["play_time"].split(":"))
                target = now.replace(day=day, hour=hr, minute=mn, second=0)
                
                # แจ้งเตือนล่วงหน้า 4 ชั่วโมง
                remind = target - datetime.timedelta(hours=4)
                if now.hour == remind.hour and now.minute == remind.minute:
                    line_bot_api.push_message(state["group_id"], TextSendMessage(text=f"📢 ประกาศจากพี่รวย! คืนนี้เวลา {state['play_time']} น. จะเริ่มเปิดประมูลแชร์นะครับ เตรียมเงินให้พร้อม!"))
                
                # เปิดประมูลอัตโนมัติ
                if now.hour == target.hour and now.minute == target.minute and not state.get("auction", {}).get("is_active"):
                    update_db("auction/is_active", True)
                    update_db("auction/current_price", 0)
                    update_db("auction/paid_members", []) 
                    update_db("auction/waiting_for_account", False)
                    
                    msg = f"📢 ถึงเวลาแล้ว! พี่รวยเปิดประมูลอัตโนมัติ!\nกติกา: บิดขั้นต่ำ {state.get('auction',{}).get('min_increment',100)}.-\n⏳ จับเวลา 1 นาทีครับ! ใครอยากรวยพิมพ์ตัวเลขบิดมาเลย!!"
                    line_bot_api.push_message(state["group_id"], TextSendMessage(text=msg))
            except: pass
        time.sleep(60)

# --- 4. ระบบนับถอยหลังการประมูล ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) 
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! เหลือ 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครจะสู้เพิ่มไหม?"))
        
        for i in range(10, 0, -1):
            curr = get_state()
            if not curr.get("auction", {}).get("is_active") or curr["auction"]["current_price"] != bid_amount: return
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            time.sleep(3)
        
        curr = get_state()
        if curr.get("auction", {}).get("is_active") and curr["auction"]["current_price"] == bid_amount:
            update_db("auction/is_active", False)
            update_db("auction/waiting_for_account", True)
            winner = curr["auction"]["winner_name"]
            msg = f"🏁 พี่รวยขอปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดดอกเบี้ย: {bid_amount} บาท\n\n⚠️ รบกวนคุณ {winner} ส่งเลขบัญชีมาด้วยครับ"
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))
            won_list = curr.get("won_names", [])
            if winner not in won_list:
                won_list.append(winner)
                update_db("won_names", won_list)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

# --- 5. จัดการการส่งรูปภาพ (สลิปโอนเงิน) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    state = get_state()
    user_id = event.source.user_id
    if event.source.type == 'group':
        reply_to_id = event.source.group_id
        try:
            profile = line_bot_api.get_group_member_profile(reply_to_id, user_id)
            name = profile.display_name
        except: name = "สมาชิก"
        
        paid_list = state.get("auction", {}).get("paid_members", [])
        if name not in paid_list:
            paid_list.append(name)
            update_db("auction/paid_members", paid_list)
            
            total = state.get("total_members_count", 0)
            current_paid = len(paid_list)
            remain = total - current_paid if total > 0 else 0
            
            reply = (f"📸 พี่รวยบันทึกสลิปของคุณ {name} แล้ว!\n"
                     f"✅ จ่ายแล้ว: {current_paid}/{total} คน\n"
                     f"⏳ ขาดอีก: {remain} คนจะครบวงครับ")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 6. จัดการข้อความตัวอักษร ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    state = get_state()
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    if event.source.type == 'group':
        reply_to_id = event.source.group_id
        update_db("group_id", reply_to_id)
        try:
            profile = line_bot_api.get_group_member_profile(reply_to_id, user_id)
            name = profile.display_name
        except: name = "สมาชิก"
    else:
        reply_to_id = user_id
        try:
            profile = line_bot_api.get_profile(user_id)
            name = profile.display_name
        except: name = "สมาชิก"

    # เมนู Help
    if text == "/help":
        msg = ("📖 เมนูพี่รวยร่างทอง:\n"
               "1. พิมพ์ 'ตั้งค่าวงแชร์' - เริ่มตั้งค่าใหม่\n"
               "2. /status - ดูข้อมูลวงปัจจุบัน\n"
               "3. /start_bid - เปิดประมูลทันที\n"
               "4. /check_pay - เช็กรายชื่อคนโอน\n"
               "5. เลื่อนแชร์ [เวลา] - เปลี่ยนเวลาประมูล\n"
               "6. /reset_circle - ล้างข้อมูลทั้งหมด\n"
               "7. /remove_winner [ชื่อ] - ลบรายชื่อคนเปียร์ได้")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ระบบตรวจสอบการโอนเงิน
    if text == "/check_pay":
        paid_list = state.get("auction", {}).get("paid_members", [])
        total = state.get("total_members_count", 0)
        msg = (f"📊 สถานะการโอนเงิน:\n"
               f"✅ โอนแล้ว ({len(paid_list)}): {', '.join(paid_list) if paid_list else 'ยังไม่มี'}\n"
               f"❌ ขาดอีก: {total - len(paid_list)} คน\n"
               f"💰 ยอดต่อคน: {state.get('share_amount', 0)} บาท")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ระบบเลื่อนเวลา
    if text.startswith("เลื่อนแชร์"):
        parts = text.split()
        if len(parts) >= 2:
            new_time = parts[-1]
            update_db("play_time", new_time)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔄 เลื่อนเวลาประมูลเป็น {new_time} น. เรียบร้อย!"))
        return

    # Setup Wizard
    if text == "ตั้งค่าวงแชร์":
        update_db("setup_step", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เริ่มตั้งค่าครับ 📝 ยอดส่งต่อคนเท่าไหร่? (เลขเท่านั้น)"))
        return

    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1 and text.isdigit():
            update_db("share_amount", int(text)); update_db("setup_step", 2)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📈 ในวงมีสมาชิกทั้งหมดกี่คน?"))
        elif step == 2 and text.isdigit():
            update_db("total_members_count", int(text)); update_db("setup_step", 3)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📈 บิดขั้นต่ำเพิ่มครั้งละกี่บาท?"))
        elif step == 3 and text.isdigit():
            update_db("auction/min_increment", int(text)); update_db("setup_step", 4)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📅 เปียร์วันที่เท่าไหร่? (1-31)"))
        elif step == 4:
            update_db("play_date", text); update_db("setup_step", 5)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕗 เริ่มประมูลกี่โมง? (เช่น 20:00)"))
        elif step == 5:
            update_db("play_time", text); update_db("setup_step", 0)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 ตั้งค่าสำเร็จ! พี่รวยพร้อมทำงานครับ"))
        return

    # ระบบประมูล
    if text == "/start_bid":
        update_db("auction/is_active", True); update_db("auction/current_price", 0); update_db("auction/paid_members", [])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📢 เปิดประมูล! ใครจะสู้พิมพ์ตัวเลขเลย!"))
        return

    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text); curr = state["auction"].get("current_price", 0); min_inc = state["auction"].get("min_increment", 100)
        if name in state.get("won_names", []): return
        required = curr + min_inc if curr > 0 else min_inc
        if bid >= required:
            update_db("auction/current_price", bid); update_db("auction/winner_name", name); update_db("auction/winner_id", user_id)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ ล่าสุด {bid} บ. โดยคุณ {name}"))
            threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
        return

if __name__ == "__main__":
    threading.Thread(target=bg_schedule_checker, daemon=True).start()
    app.run(port=5000)