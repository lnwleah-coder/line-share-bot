import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import threading, time, datetime
import google.generativeai as genai
import pytz

# --- 1. เชื่อมต่อ Firebase ---
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'})
ref = db.reference('share_circle')

# --- 2. ตั้งค่า Gemini AI (พี่รวย) ---
genai.configure(api_key="AIzaSyAMTRJdIUtqVdB2vHHEegnS7SCso2Zt7GE")
model = genai.GenerativeModel('gemini-1.5-flash')

app = Flask(__name__)
line_bot_api = LineBotApi('7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('a0b27ece169f30e2a3574f5717497e27')

tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state(): return ref.get() or {}
def update_db(path, value): ref.child(path).set(value)

# --- ระบบแจ้งเตือน 4 ชม. และ เปิดประมูลอัตโนมัติ ---
# (ใช้ฟังก์ชันเดิมของคุณได้เลยครับ)
def bg_schedule_checker():
    while True:
        state = get_state()
        if state.get("play_date") != "ระบุวันที่" and state.get("group_id"):
            now = datetime.datetime.now(tz_bangkok) 
            try:
                day = int(state["play_date"])
                hr, mn = map(int, state["play_time"].split(":"))
                target = now.replace(day=day, hour=hr, minute=mn, second=0)
                
                # 1. แจ้งเตือนล่วงหน้า 4 ชั่วโมง
                remind = target - datetime.timedelta(hours=4)
                if now.hour == remind.hour and now.minute == remind.minute:
                    line_bot_api.push_message(state["group_id"], TextSendMessage(text=f"📢 ประกาศจากพี่รวย! คืนนี้เวลา {state['play_time']} น. จะเริ่มเปิดประมูลแชร์นะครับ เตรียมเงินให้พร้อม!"))
                
                # 2. เปิดประมูลอัตโนมัติ ตรงเวลาเป๊ะ!
                if now.hour == target.hour and now.minute == target.minute and not state.get("auction", {}).get("is_active"):
                    update_db("auction/is_active", True)
                    update_db("auction/current_price", 0)
                    update_db("auction/waiting_for_account", False)
                    
                    msg = f"📢 ถึงเวลาแล้ว! พี่รวยเปิดประมูลอัตโนมัติ!\nกติกา: บิดขั้นต่ำ {state.get('auction',{}).get('min_increment',100)}.-\n⏳ จับเวลา 1 นาทีครับ! ใครอยากรวยพิมพ์ตัวเลขบิดมาเลย!!"
                    line_bot_api.push_message(state["group_id"], TextSendMessage(text=msg))

            except Exception as e: 
                pass
        time.sleep(60)

# --- 🤖 AI พี่รวย ช่วยบิ้ว และ ตอบโต้ ---
# (ใช้ฟังก์ชันเดิมของคุณได้เลยครับ)
def ai_hype_man(reply_to_id, user_name, bid_amount):
    prompt = (f"คุณคือ 'พี่รวย' กรรมการวงแชร์สายปั่นมาดป๋า สุภาพแต่กวนฮา "
              f"ตอนนี้มีการประมูลแชร์ คุณ {user_name} บิดราคามาที่ {bid_amount} บาท "
              f"ช่วยพูดเชียร์ให้คนในกลุ่มอยากสู้ราคาเพิ่ม เอาแบบดูรวยๆ กวนๆ ตลกๆ "
              f"เน้นความสนุกสนาน (ตอบสั้นๆ ไม่เกิน 2 ประโยค)")
    try:
        response = model.generate_content(prompt)
        hype_msg = response.text.strip()
    except:
        hype_msg = f"คุณ {user_name} จัดมา {bid_amount} แล้ว! พี่รวยบอกเลยว่าราคานี้จิ๊บๆ ใครจะสู้ต่อเชิญเลยครับ!"
    
    try: line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"🤖 พี่รวย: {hype_msg}"))
    except: pass

def ai_general_chat(reply_to_id, user_name, user_text, current_bid):
    prompt = (f"คุณคือ 'พี่รวย' กรรมการวงแชร์สายปั่นมาดป๋า สุภาพแต่กวนฮา "
              f"ตอนนี้กำลังอยู่ในช่วงเปิดประมูล 1 นาที (ยอดล่าสุดในกระดานคือ {current_bid} บาท) "
              f"คุณ {user_name} พิมพ์แชทมาว่า: '{user_text}' "
              f"จงตอบกลับเพื่อเชียร์ให้สู้ราคา อวย หรือคุยเล่นแบบป๋าๆ รวยๆ "
              f"ตอบสั้นๆ ไม่เกิน 2 ประโยค")
    try:
        response = model.generate_content(prompt)
        chat_msg = response.text.strip()
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"🤖 พี่รวย: {chat_msg}"))
    except: pass

# --- ระบบนับถอยหลัง ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) 
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! เหลือ 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครจะสู้เพิ่มไหมครับ?"))
        
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
            msg = f"🏁 พี่รวยขอปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดหักเข้ากองกลาง: {bid_amount} บาท\n\n⚠️ รบกวนคุณ {winner} พิมพ์เลขบัญชีและธนาคารส่งมาให้พี่รวยด้วยครับ"
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

# ✅ [แก้ไข 1] ครอบเช็กว่าต้องเป็นข้อความเท่านั้น (ป้องกันคนส่งรูป/สติ๊กเกอร์แล้วบอทพัง)
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

    # --- คำสั่งระบบ ---
    if text == "/help":
        msg = "📖 เมนูพี่รวย:\n- พิมพ์ 'ตั้งค่าวงแชร์'\n- /status\n- /start_bid\n- /reset_circle\n- /remove_winner [ชื่อ]"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/reset_circle":
        ref.set({"share_amount": 1000, "play_date": "ระบุวันที่", "play_time": "20:00", "won_names": [], "pot_balance": 0, "setup_step": 0, "auction": {"is_active": False, "current_price": 0, "min_increment": 100}})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🧹 พี่รวยกวาดวงแชร์เรียบร้อย! เริ่มต้นใหม่ได้เลยครับท่านท้าว"))
        return

    if text.startswith("/remove_winner"):
        target = text.replace("/remove_winner", "").strip()
        won_list = state.get("won_names", [])
        if target in won_list:
            won_list.remove(target); update_db("won_names", won_list)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยลบชื่อคุณ {target} ออกแล้ว สู้ใหม่ได้เลย!"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ พี่รวยหาชื่อคุณ {target} ไม่เจอครับ"))
        return

    if text == "/status":
        won = ", ".join(state.get("won_names", [])) if state.get("won_names") else "ยังไม่มี"
        msg = f"📊 ข้อมูลวงแชร์ของพี่รวย:\n💰 ยอดส่ง: {state.get('share_amount')} บ./คน\n📈 บิดขั้นต่ำ: {state.get('auction',{}).get('min_increment')} บ.\n📅 เปียร์วันที่: {state.get('play_date')} เวลา {state.get('play_time')}\n🏆 คนเปียร์ได้แล้ว: {won}\n💎 กองกลางสะสม: {state.get('pot_balance',0)} บาท"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- Setup Wizard ---
    if text == "ตั้งค่าวงแชร์":
        update_db("setup_step", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เริ่มตั้งค่าวงแชร์ครับ 📝 'ยอดเงินที่ต้องส่งต่อคน' คือเท่าไหร่ครับ? (พิมพ์แค่ตัวเลข)"))
        return

    step = state.get("setup_step", 0)
    if step > 0:
        # ✅ [แก้ไข 2] เพิ่มการดักจับข้อผิดพลาด (else) ในช่วงตั้งค่า
        if step == 1:
            if text.isdigit():
                update_db("share_amount", int(text)); update_db("setup_step", 2)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="รับทราบครับ 📈 'ขั้นต่ำในการบิดประมูล' เพิ่มครั้งละกี่บาทครับ?"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาพิมพ์เป็น 'ตัวเลข' เท่านั้นครับ"))
        
        elif step == 2:
            if text.isdigit():
                update_db("auction/min_increment", int(text)); update_db("setup_step", 3)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📅 กำหนดเปียร์แชร์ทุกวันที่เท่าไหร่ของเดือนครับ? (พิมพ์ตัวเลข 1-31)"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาพิมพ์เป็น 'ตัวเลข' เท่านั้นครับ"))
        
        elif step == 3:
            update_db("play_date", text); update_db("setup_step", 4)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕗 ให้บอทเริ่มเปิดประมูลตอนกี่โมงครับ? (รูปแบบเช่น 20:00)"))
        
        elif step == 4:
            update_db("play_time", text); update_db("setup_step", 5)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏆 วงนี้มีคนที่ 'เคยเปียร์ชนะไปแล้ว' ไหมครับ? (ถ้าไม่มีพิมพ์ 'ไม่มี')"))
        
        elif step == 5:
            if text != "ไม่มี": update_db("won_names", text.replace("@","").split())
            update_db("setup_step", 6)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💎 คำถามสุดท้ายครับ! ตอนนี้มี 'เงินสะสมในกองกลาง' อยู่กี่บาท? (ถ้าไม่มีพิมพ์ 0)"))
        
        elif step == 6:
            if text.isdigit():
                update_db("pot_balance", int(text)); update_db("setup_step", 0)
                msg = f"🎉 ตั้งค่าวงแชร์เสร็จสมบูรณ์ร้อยเปอร์เซ็นต์ครับ!\n\nพี่รวยพร้อมทำงานรับใช้ท่านท้าวแล้ว! 🫡"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาพิมพ์เป็น 'ตัวเลข' เท่านั้นครับ"))
        return

    # --- 1. เปิดประมูล ---
    if text == "/start_bid":
        update_db("auction/is_active", True); update_db("auction/current_price", 0); update_db("auction/waiting_for_account", False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📢 พี่รวยขอเปิดกระดานประมูล!\nกติกา: บิดขั้นต่ำ {state.get('auction',{}).get('min_increment',100)}.-\n⏳ จับเวลา 1 นาทีครับ! ลุย!!"))
        return
    
    # --- 2. กรณีคนพิมพ์ตัวเลขบิดสู้ราคา ---
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text); curr = state["auction"].get("current_price", 0); min_inc = state["auction"].get("min_increment", 100)
        
        if name in state.get("won_names", []):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ คุณ {name} เคยได้ไปแล้ว รอรอบหน้านะครับ!"))
            return

        required = curr + min_inc if curr > 0 else min_inc
        if bid >= required:
            update_db("auction/current_price", bid); update_db("auction/winner_name", name); update_db("auction/winner_id", user_id)
            
            # บอทระบบตอบทันที
            msg = f"✅ พี่รวยรับยอด!\n🔥 ล่าสุด: {bid} บาท\n🙋‍♂️ โดย: คุณ {name}\n⏳ รีเซ็ตเวลานับ 1 นาทีใหม่..."
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

            # ให้ AI พี่รวยเชียร์ตามหลังมา
            threading.Thread(target=ai_hype_man, args=[reply_to_id, name, bid]).start()
            # เริ่มจับเวลาใหม่
            threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ยอดน้อยไปนิด! ต้องใส่ {required} บาทขึ้นไปครับ"))
        return

    # --- 3. 🌟 โหมดพี่รวยคุยเล่น (จะทำงานเฉพาะตอนที่ประมูลยังไม่จบ) 🌟 ---
    if state.get("auction", {}).get("is_active") and not text.startswith("/"):
        curr = state["auction"].get("current_price", 0)
        # ปล่อยให้ AI พี่รวยตอบกลับข้อความทั่วไปแบบกวนๆ
        threading.Thread(target=ai_general_chat, args=[reply_to_id, name, text, curr]).start()
        return

    # --- สรุปยอด ---
    if state.get("auction", {}).get("waiting_for_account") and user_id == state["auction"].get("winner_id"):
        update_db("auction/waiting_for_account", False); update_db("pot_balance", state.get("pot_balance", 0) + state["auction"]["current_price"])
        msg = f"📊 พี่รวยขอสรุปยอดโอนรอบนี้\n🏆 ผู้รับเงิน: คุณ {name}\n🏦 บัญชี: {text}\n\n💸 สมาชิกท่านอื่นรบกวนโอนท่านละ {state.get('share_amount')} บาท แล้วส่งสลิปมาได้เลยครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

if __name__ == "__main__":
    threading.Thread(target=bg_schedule_checker, daemon=True).start()
    app.run(port=5000)