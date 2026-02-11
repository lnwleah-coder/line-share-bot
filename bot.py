import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage
import threading
import time
import datetime

# --- 1. เชื่อมต่อ Firebase ---
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/' 
})

ref = db.reference('share_circle')
app = Flask(__name__)

# --- กุญแจ LINE ---
line_bot_api = LineBotApi('7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('a0b27ece169f30e2a3574f5717497e27')

# --- Helper Functions ---
def get_state():
    return ref.get() or {}

def update_db(path, value):
    ref.child(path).set(value)

# --- ระบบนับถอยหลังสไตล์เดิม (เลขละ 3 วิ) ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30) 
    state = get_state()
    if state.get("auction", {}).get("is_active") and state["auction"]["current_price"] == bid_amount:
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ แง้มค้อนแล้ว! เหลือเวลาอีก 30 วินาทีสุดท้าย ยอดปัจจุบัน {bid_amount} บาท มีใครจะสู้เพิ่มไหมครับ?"))
        
        for i in range(10, 0, -1):
            curr = get_state()
            if not curr.get("auction", {}).get("is_active") or curr["auction"]["current_price"] != bid_amount:
                return
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            time.sleep(3)
        
        curr = get_state()
        if curr.get("auction", {}).get("is_active") and curr["auction"]["current_price"] == bid_amount:
            update_db("auction/is_active", False)
            update_db("auction/waiting_for_account", True)
            winner = curr["auction"]["winner_name"]
            msg = f"🏁 ปิดประมูล!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดหักเข้ากองกลาง: {bid_amount} บาท\n\n⚠️ รบกวนคุณ {winner} พิมพ์เลขบัญชีและธนาคารส่งมาได้เลยครับ"
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

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    state = get_state()
    text = event.message.text.strip()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    profile = line_bot_api.get_profile(user_id)
    name = profile.display_name
    if hasattr(event.source, 'group_id'): update_db("group_id", event.source.group_id)

    # --- ฟังก์ชันใหม่: จัดการวงแชร์ ---
    if text == "/reset_circle":
        default_state = {
            "share_amount": 1000, "play_date": "ระบุวันที่", "play_time": "20:00",
            "won_names": [], "pot_balance": 0, "members": {}, "setup_step": 0,
            "auction": {"is_active": False, "current_price": 0, "min_increment": 100}
        }
        ref.set(default_state)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🧹 รีเซ็ตวงแชร์เรียบร้อย! เริ่มต้นใหม่ได้เลยครับท่านท้าว"))
        return

    if text.startswith("/remove_winner"):
        name_to_remove = text.replace("/remove_winner", "").strip()
        won_list = state.get("won_names", [])
        if name_to_remove in won_list:
            won_list.remove(name_to_remove)
            update_db("won_names", won_list)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ ลบชื่อคุณ {name_to_remove} ออกจากรายชื่อผู้ชนะแล้วครับ"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ไม่พบชื่อคุณ {name_to_remove} ในรายชื่อผู้ชนะครับ"))
        return

    # --- Setup Wizard คำพูดเดิม ---
    if text == "ตั้งค่าวงแชร์":
        update_db("setup_step", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เริ่มตั้งค่าวงแชร์ครับ 📝 'ยอดเงินที่ต้องส่งต่อคน' คือเท่าไหร่ครับ? (พิมพ์แค่ตัวเลข)"))
        return

    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1 and text.isdigit():
            update_db("share_amount", int(text)); update_db("setup_step", 2)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="รับทราบครับ 📈 'ขั้นต่ำในการบิดประมูล' เพิ่มครั้งละกี่บาทครับ?"))
        elif step == 2 and text.isdigit():
            update_db("auction/min_increment", int(text)); update_db("setup_step", 3)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📅 กำหนดเปียร์แชร์ทุกวันที่เท่าไหร่ของเดือนครับ?"))
        elif step == 3:
            update_db("play_date", text); update_db("setup_step", 4)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕗 ให้บอทเริ่มเปิดประมูลตอนกี่โมงครับ? (เช่น 20:00)"))
        elif step == 4:
            update_db("play_time", text); update_db("setup_step", 5)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏆 วงนี้มีคนที่ 'เคยเปียร์ชนะไปแล้ว' ไหมครับ? (ถ้าไม่มีพิมพ์ 'ไม่มี')"))
        elif step == 5:
            if text != "ไม่มี": update_db("won_names", text.replace("@","").split())
            update_db("setup_step", 6)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💎 คำถามสุดท้ายครับ! ตอนนี้มี 'เงินสะสมในกองกลาง' อยู่กี่บาท?"))
        elif step == 6 and text.isdigit():
            update_db("pot_balance", int(text)); update_db("setup_step", 0)
            msg = f"🎉 ตั้งค่าวงแชร์เสร็จสมบูรณ์ร้อยเปอร์เซ็นต์ครับ!\n\nบอทกรรมการพร้อมทำงานแล้วครับท่านท้าว! 🫡"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- ระบบประมูล คำพูดเดิม ---
    if text == "/start_bid":
        update_db("auction/is_active", True)
        update_db("auction/current_price", 0)
        update_db("auction/waiting_for_account", False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📢 เริ่มการประมูล!\nกติกา: บิดขั้นต่ำ {state.get('auction',{}).get('min_increment',100)}.-\n⏳ จับเวลา 1 นาทีครับ!"))
    
    elif text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr_price = state["auction"].get("current_price", 0)
        min_inc = state["auction"].get("min_increment", 100)
        
        if name in state.get("won_names", []):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ คุณ {name} เคยได้ไปแล้ว ไม่มีสิทธิ์ประมูลครับ"))
            return

        required = curr_price + min_inc if curr_price > 0 else min_inc
        if bid >= required:
            update_db("auction/current_price", bid)
            update_db("auction/winner_name", name)
            update_db("auction/winner_id", user_id)
            threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด!\n🔥 ล่าสุด: {bid} บาท\n🙋‍♂️ โดย: คุณ {name}\n⏳ รีเซ็ตเวลานับ 1 นาทีใหม่..."))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ยอดน้อยไปครับ! ต้องใส่ {required} บาทขึ้นไป"))

    elif state.get("auction", {}).get("waiting_for_account") and user_id == state["auction"].get("winner_id"):
        update_db("auction/waiting_for_account", False)
        update_db("auction/payment_phase", True)
        update_db("pot_balance", state.get("pot_balance", 0) + state["auction"]["current_price"])
        msg = f"📊 สรุปยอดโอนรอบนี้\n🏆 ผู้รับเงิน: คุณ {name}\n🏦 บัญชี: {text}\n\n💸 สมาชิกท่านอื่นรบกวนโอนท่านละ {state.get('share_amount')} บาท แล้วส่งสลิปมาได้เลยครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    elif text == "/status":
        pot = state.get("pot_balance", 0)
        won = ", ".join(state.get("won_names", [])) if state.get("won_names") else "ยังไม่มี"
        msg = f"📊 ข้อมูลวงแชร์:\n💰 ยอดส่ง: {state.get('share_amount')} บ./คน\n📈 บิดขั้นต่ำ: {state.get('auction',{}).get('min_increment')} บ.\n📅 เปียร์วันที่: {state.get('play_date')} เวลา {state.get('play_time')}\n🏆 คนเปียร์ได้แล้ว: {won}\n💎 กองกลางสะสม: {pot} บาท"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

if __name__ == "__main__":
    app.run(port=5000)