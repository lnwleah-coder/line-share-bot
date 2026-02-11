import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import threading, time, datetime
import google.generativeai as genai
import pytz # สำหรับล็อกเวลาประเทศไทย

# --- 1. เชื่อมต่อ Firebase ---
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'})
ref = db.reference('share_circle')

# --- 2. ตั้งค่า Gemini AI (พี่รวย กรรมการวงแชร์) ---
genai.configure(api_key="AIzaSyAMTRJdIUtqVdB2vHHEegnS7SCso2Zt7GE")
model = genai.GenerativeModel('gemini-1.5-flash')

app = Flask(__name__)
# กุญแจ Messaging API ของคุณ
line_bot_api = LineBotApi('7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('a0b27ece169f30e2a3574f5717497e27')

# กำหนดเขตเวลาไทย
tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state(): return ref.get() or {}
def update_db(path, value): ref.child(path).set(value)

# --- ระบบแจ้งเตือน 4 ชม. ล่วงหน้า (ฉบับล็อกเวลาไทย) ---
def bg_schedule_checker():
    while True:
        state = get_state()
        if state.get("play_date") != "ระบุวันที่" and state.get("group_id"):
            now = datetime.datetime.now(tz_bangkok) 
            try:
                day = int(state["play_date"])
                hr, mn = map(int, state["play_time"].split(":"))
                target = now.replace(day=day, hour=hr, minute=mn, second=0)
                remind = target - datetime.timedelta(hours=4)
                
                if now.hour == remind.hour and now.minute == remind.minute:
                    line_bot_api.push_message(state["group_id"], TextSendMessage(text=f"📢 พี่รวยมาเตือน! อีก 4 ชม. ({state['play_time']} น.) จะเริ่มประมูลแล้วนะจ๊ะ เตรียมเงินให้พร้อม!"))
            except: pass
        time.sleep(60)

# --- ฟังก์ชัน AI พี่รวย ช่วยบิ้ว (ทำงานเฉพาะตอนประมูล) ---
def ai_hype_man(user_name, bid_amount):
    prompt = (f"คุณคือ 'พี่รวย' กรรมการวงแชร์สายปั่นมาดป๋า สุภาพแต่กวนฮา "
              f"ตอนนี้มีการประมูลแชร์ คุณ {user_name} บิดราคามาที่ {bid_amount} บาท "
              f"ช่วยพูดเชียร์ให้คนในกลุ่มอยากสู้ราคาเพิ่ม เอาแบบดูรวยๆ กวนๆ ตลกๆ "
              f"เน้นความสนุกสนาน (ตอบสั้นๆ ไม่เกิน 2 ประโยค)")
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except:
        return f"คุณ {user_name} จัดมา {bid_amount} แล้ว! พี่รวยบอกเลยว่าราคานี้จิ๊บๆ ใครจะสู้ต่อเชิญเลยครับ!"

# --- ลอจิกนับถอยหลัง 10-1 (เลขละ 3 วิ) ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) 
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครจะสู้เพิ่มไหม?"))
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
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"🏁 ปิดประมูล! ยินดีกับคุณ {winner} ชนะที่ {bid_amount} บ.\nเฮงๆ รวยๆ ครับ! รบกวนส่งเลขบัญชีด้วยนะ"))
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

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    state = get_state()
    text = event.message.text.strip()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id
    profile = line_bot_api.get_profile(user_id)
    name = profile.display_name

    if hasattr(event.source, 'group_id'): update_db("group_id", event.source.group_id)

    # --- เมนูช่วยเหลือ ---
    if text == "/help":
        msg = ("📖 เมนูพี่รวย:\n"
               "- พิมพ์ 'ตั้งค่าวงแชร์' : เริ่มตั้งค่าใหม่\n"
               "- /status : ดูสถานะวง\n"
               "- /start_bid : เริ่มประมูล (1 นาที)\n"
               "- /reset_circle : ล้างวงใหม่\n"
               "- /remove_winner [ชื่อ] : ลบชื่อคนชนะ")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/status":
        won = ", ".join(state.get("won_names", [])) if state.get("won_names") else "ยังไม่มี"
        msg = f"📊 ข้อมูลวงแชร์:\n💰 ยอดส่ง: {state.get('share_amount')} บ.\n📅 เปียร์วันที่: {state.get('play_date')} เวลา {state.get('play_time')}\n🏆 คนได้แล้ว: {won}\n💎 กองกลาง: {state.get('pot_balance',0)} บ."
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- ฟังก์ชันจัดการวง ---
    if text == "/reset_circle":
        ref.set({"share_amount": 1000, "play_date": "ระบุวันที่", "play_time": "20:00", "won_names": [], "pot_balance": 0, "setup_step": 0, "auction": {"is_active": False, "current_price": 0, "min_increment": 100}})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🧹 พี่รวยกวาดบ้านเรียบร้อย! เริ่มต้นใหม่ได้เลยจ้า"))
        return

    if text.startswith("/remove_winner"):
        target = text.replace("/remove_winner", "").strip()
        won_list = state.get("won_names", [])
        if target in won_list:
            won_list.remove(target); update_db("won_names", won_list)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ ลบชื่อคุณ {target} ออกแล้ว สู้ใหม่ได้เลย!"))
        return

    # --- Setup Wizard ---
    if text == "ตั้งค่าวงแชร์":
        update_db("setup_step", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เริ่มตั้งค่าครับ 💰 ยอดส่งต่อคนกี่บาท?"))
        return

    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1: update_db("share_amount", int(text)); update_db("setup_step", 2); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📈 บิดขั้นต่ำกี่บาท?"))
        elif step == 2: update_db("auction/min_increment", int(text)); update_db("setup_step", 3); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📅 เปียร์วันที่เท่าไหร่? (1-31)"))
        elif step == 3: update_db("play_date", text); update_db("setup_step", 4); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕗 เวลากี่โมง? (เช่น 20:00)"))
        elif step == 4: update_db("play_time", text); update_db("setup_step", 5); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏆 ใครเคยได้แล้ว? (ถ้าไม่มีพิมพ์ 'ไม่มี')"))
        elif step == 5:
            if text != "ไม่มี": update_db("won_names", text.replace("@","").split())
            update_db("setup_step", 6); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💎 เงินกองกลางกี่บาท?"))
        elif step == 6: update_db("pot_balance", int(text)); update_db("setup_step", 0); line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 พี่รวยบันทึกเรียบร้อย! พร้อมลุยครับท่านท้าว 🫡"))
        return

    # --- ระบบประมูล + AI พี่รวย ---
    if text == "/start_bid":
        update_db("auction/is_active", True); update_db("auction/current_price", 0); update_db("auction/waiting_for_account", False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📢 พี่รวยเปิดประมูลแล้ว! ⏳ 1 นาทีทอง ใครอยากรวยบิดมา!"))
        return
    
    elif text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text); curr = state["auction"].get("current_price", 0); min_inc = state["auction"].get("min_increment", 100)
        if name in state.get("won_names", []): return
        required = curr + min_inc if curr > 0 else min_inc
        if bid >= required:
            update_db("auction/current_price", bid); update_db("auction/winner_name", name); update_db("auction/winner_id", user_id)
            threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            hype_msg = ai_hype_man(name, bid)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับยอด {bid} บ. จากคุณ {name}!\n\n🤖 {hype_msg}"))

    elif state.get("auction", {}).get("waiting_for_account") and user_id == state["auction"].get("winner_id"):
        update_db("auction/waiting_for_account", False); update_db("pot_balance", state.get("pot_balance", 0) + state["auction"]["current_price"])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 สรุปยอดโอนรอบนี้\n🏆 ผู้รับ: คุณ {name}\n🏦 บัญชี: {text}\n💸 สมาชิกท่านอื่นโอนคนละ {state.get('share_amount')} บ."))

if __name__ == "__main__":
    threading.Thread(target=bg_schedule_checker, daemon=True).start()
    app.run(port=5000)