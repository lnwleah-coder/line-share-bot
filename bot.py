import os
import threading
import time
import datetime
import pytz
import random
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage

# --- 0. ข้อมูลเวอร์ชัน ---
BOT_VERSION = "1.4.0"
LAST_UPDATE = "12/02/2026 (Witty Persona & Random Speech)"

app = Flask(__name__)

# --- 1. ตั้งค่า LINE API ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '57EveirH1YZetV9+CwFRuZOhTE5yZ0fiqpLfyNdspxI7mRRXNrCuiKtI/Ie69Wcs6mNqXJ6AdrN3inLxptPdFjPfeDUap8PtgeLhBSULc4BQkVTolXNeJGUVjnXtjmc/OPnmLN93NLNpnq4AJNZQ3QdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '7c335f0de71e4cb1379a75134e3a7a50')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. เชื่อมต่อ Firebase ---
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {'databaseURL': 'https://mysharebot-default-rtdb.asia-southeast1.firebasedatabase.app/'})

ref = db.reference('share_circle')
tz_bangkok = pytz.timezone('Asia/Bangkok')

def get_state(): return ref.get() or {}
def get_now_str(): return datetime.datetime.now(tz_bangkok).strftime('%d/%m/%Y %H:%M')

# --- 🎁 คลังคำพูดสไตล์พี่รวย ---
def get_random_speech(type):
    speeches = {
        "start": [
            "📢 กระดานเปิดแล้ว! ใครร้อนเงิน ใครอยากรวย เชิญทางนี้ครับ บิดขั้นต่ำ {min} บ. ลุย!",
            "📢 สมรภูมิคนอยากรวยเริ่มขึ้นแล้ว! วันนี้ใครจะเป็นราชาเงินก้อน? บิดขั้นต่ำ {min} บ. พิมพ์มา!",
            "📢 @all ได้เวลาถอนทุนคืน! พี่รวยเปิดประมูลแล้วครับ ขั้นต่ำแค่ {min} บ. อย่ามัวแต่ดู เพื่อนจะคาบไปกินก่อนนะ!",
            "📢 เงินก้อนรออยู่ตรงหน้า ใครช้าอดนะครับ! ขั้นต่ำ {min} บ. พี่รวยพร้อมรับยอดแล้ว!"
        ],
        "accept": [
            "✅ ยอด {bid} บ. มาแล้ว! โดยคุณ {name} ทรงอย่างแบด แซดอย่างบ่อย แต่ยอดบิดอย่างหล่อ!",
            "✅ คุณ {name} จัดให้ที่ {bid} บ. ใจถึงพึ่งได้จริงๆ ครับท่านนี้!",
            "✅ รับยอด {bid} บ. จากคุณ {name} ครับผม! มีใครจะกล้าปาดหน้าไหมเอ่ย?",
            "✅ ฮั่นแน่! คุณ {name} บิดมาที่ {bid} บ. แล้วครับ ยอดนี้จะอยู่ถึงจบไหมนะ?"
        ],
        "30s": [
            "⏳ 30 วิสุดท้าย! ค้อนเริ่มสั่นแล้วนะ ยอด {bid} บ. จะโดนใครปาดไหม?",
            "⏳ โค้งสุดท้าย 30 วินาที! ยอด {bid} บ. ของคุณ {name} จะได้ไปจริงๆ เหรอ? ใครไหวจัดมา!",
            "⏳ อีก 30 วิ พี่รวยจะเคาะแล้วนะ! {bid} บ. คือราคาสุดท้ายจริงเหรอสมาชิก?",
            "⏳ ยอดปัจจุบัน {bid} บ. นับถอยหลัง 30 วิ! ใครจะหล่อปาดหน้าเค้กวินาทีสุดท้าย เชิญ!"
        ],
        "low_bid": [
            "⚠️ ยอดน้อยไปหน่อยนะจ๊ะ! ต้องบิดอย่างน้อย {req} บ. พี่รวยถึงจะชายตามอง",
            "⚠️ ขั้นต่ำคือ {req} บ. ครับสมาชิก บิดต่ำกว่านี้พี่รวยปวดใจ!",
            "⚠️ ผิดกติกาครับ! พี่รวยบอกให้เริ่มที่ {req} บ. ลองใหม่อีกทีนะคนสวย/คนหล่อ",
            "⚠️ ยอดนี้พี่รวยรับไม่ได้จริงๆ ครับ ต้องอย่างน้อย {req} บ. เท่านั้น!"
        ]
    }
    return random.choice(speeches[type])

# --- 3. ระบบนับถอยหลัง ---
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30)
    state = get_state()
    auction = state.get("auction", {})
    
    if auction.get("is_active") and auction.get("current_price") == bid_amount:
        # สุ่มคำพูดแจ้งเตือน 30 วิ
        msg_30s = get_random_speech("30s").format(bid=bid_amount, name=auction.get("winner_name"))
        try: line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg_30s))
        except: pass

        for i in range(10, 0, -1):
            time.sleep(3)
            curr_auction = get_state().get("auction", {})
            if not curr_auction.get("is_active") or curr_auction.get("current_price") != bid_amount: return 
            try: line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
            except: pass
        
        final_state = get_state()
        final_auction = final_state.get("auction", {})
        if final_auction.get("is_active") and final_auction.get("current_price") == bid_amount:
            winner = final_auction.get("winner_name", "ไม่ระบุ")
            now_date = get_now_str().split()[0]
            ref.child('auction').update({"is_active": False, "waiting_for_account": True})
            
            history = final_state.get("winners_history", [])
            history.append({"name": winner, "date": now_date, "bid": bid_amount})
            won_names = final_state.get("won_names", [])
            if winner not in won_names: won_names.append(winner)
            ref.update({"winners_history": history, "won_names": won_names})
            
            msg_end = f"🏁 ปิดประมูล!\n🏆 ยินดีกับเศรษฐีใหม่ คุณ {winner}\n💰 คว้าเงินก้อนไปด้วยยอดบิด {bid_amount} บ.\n📅 วันที่ชนะ: {now_date}\n⚠️ รบกวนส่งเลขบัญชีด้วยนะครับเพื่อนๆ รอโอนอยู่!"
            try: line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg_end))
            except: pass

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
        paid_count = sum(1 for m in get_state().get("members", {}).values() if m.get("has_paid"))
        total = get_state().get("total_members", 0)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับสลิปคุณ {name} แล้วครับ! หวานเจี๊ยบ~\n📊 จ่ายแล้ว: {paid_count}/{total} คน"))
    except: pass

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    # --- 1. คำสั่งสำคัญ ---
    if text == "ตั้งค่าวงแชร์":
        ref.update({"setup_step": 1, "won_names": [], "winners_history": [], "reminded": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 เริ่มโหมดตั้งค่า (สุภาพแต่เร้าใจ)\n1. ยอดส่งต่อคนเท่าไหร่ดีครับ? (พิมพ์แค่ตัวเลข)"))
        return

    if text == "/start_bid":
        ref.update({"setup_step": 0})
        ref.child('auction').update({"is_active": True, "current_price": 0, "winner_name": "", "winner_id": ""})
        members = state.get("members") or {}
        for mid in members: ref.child('members').child(mid).update({"has_paid": False})
        
        min_inc = state.get('auction',{}).get('min_increment', 0)
        msg = get_random_speech("start").format(min=min_inc)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # --- 2. ระบบบิดราคา (สุ่มคำรับยอด) ---
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr = state["auction"].get("current_price", 0)
        min_inc = state["auction"].get("min_increment", 0)
        required = curr + min_inc if curr > 0 else min_inc
        
        if bid >= required:
            try:
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
                name = profile.display_name
                if name in state.get("won_names", []):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ใจเย็นครับคุณ {name} เปียร์ไปแล้ว ให้เพื่อนรวยบ้าง!"))
                    return
                ref.child('auction').update({"current_price": bid, "winner_name": name, "winner_id": user_id})
                # สุ่มคำพูดตอบรับยอดบิด
                msg_acc = get_random_speech("accept").format(bid=bid, name=name)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_acc))
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        else:
            # สุ่มคำพูดเตือนบิดต่ำ
            msg_low = get_random_speech("low_bid").format(req=required)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_low))
        return

    # --- คำสั่งอื่นๆ (เหมือนเดิม) ---
    if text == "/help":
        msg = (f"📖 คู่มือพี่รวย (V.{BOT_VERSION})\n• ตั้งค่าวงแชร์ : เริ่มใหม่\n• /start_bid : เปิดประมูล\n• /status : ดูภาพรวม\n• /end_share : จบวง")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
    elif text == "/status":
        history = state.get("winners_history", [])
        hist_text = "\n".join([f"{i+1}. {h['name']} ({h['bid']}บ.)" for i, h in enumerate(history)])
        msg = (f"📊 สถานะปัจจุบัน\n💰 ส่ง: {state.get('share_amount')} บ.\n🏆 ผู้ชนะแล้ว:\n{hist_text if hist_text else '- ยังไม่มี -'}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)