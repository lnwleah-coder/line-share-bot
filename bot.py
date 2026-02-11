import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import threading, time, datetime
import google.generativeai as genai

# --- 1. เชื่อมต่อ Firebase ---
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'})
ref = db.reference('share_circle')

# --- 2. ตั้งค่า Gemini AI (พี่รวย กรรมการวงแชร์) ---
genai.configure(api_key="AIzaSyAMTRJdIUtqVdB2vHHEegnS7SCso2Zt7GE")
model = genai.GenerativeModel('gemini-1.5-flash')

app = Flask(__name__)
line_bot_api = LineBotApi('7/AMvtyIJ5rLy3xJoGq0LQXpZ70QyZikVC/q+ewSScQCPm62CSxd/Cm02zLpXQ9FRUmekKUY5DWdUXLeQMKtflmQk5k1RcCzMt74toTKPvZ7kbvLTXq2zFp4UTxhO3Ip0sIShFm1+mCTBiWjyArt+AdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('a0b27ece169f30e2a3574f5717497e27')

def get_state(): return ref.get() or {}
def update_db(path, value): ref.child(path).set(value)

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

    # --- คำสั่งระบบ ---
    if text == "/help":
        msg = ("📖 เมนูพี่รวย:\n"
               "- /status : ดูสถานะวง\n"
               "- /start_bid : เริ่มประมูล (1 นาที)\n"
               "- /reset_circle : ล้างวงใหม่\n"
               "- /remove_winner [ชื่อ] : ลบชื่อคนชนะ")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/start_bid":
        update_db("auction/is_active", True); update_db("auction/current_price", 0)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📢 พี่รวยเปิดประมูลแล้ว! ⏳ 1 นาทีทอง ใครอยากรวยบิดมาเลย!"))
        return

    # --- การประมูล + พี่รวย AI ---
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text); curr = state["auction"].get("current_price", 0); min_inc = state["auction"].get("min_increment", 100)
        if name in state.get("won_names", []): return
        required = curr + min_inc if curr > 0 else min_inc
        if bid >= required:
            update_db("auction/current_price", bid); update_db("auction/winner_name", name); update_db("auction/winner_id", user_id)
            threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            hype_msg = ai_hype_man(name, bid)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับยอด {bid} บ. จากคุณ {name}!\n\n🤖 {hype_msg}"))
        return

if __name__ == "__main__":
    app.run(port=5000)