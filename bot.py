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
BOT_VERSION = "1.4.1"
LAST_UPDATE = "12/02/2026 (Witty Persona Fixed)"

app = Flask(__name__)

# --- 1. ตั้งค่า LINE API ---
# อัปเดต Credentials ตามที่ท่านท้าวแจ้งมาครับ
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '57EveirH1YZetV9+CwFRuZOhTE5yZ0fiqpLfyNdspxI7mRRXNrCuiKtI/Ie69Wcs6mNqXJ6AdrN3inLxptPdFjPfeDUap8PtgeLhBSULc4BQkVTolXNeJGUVjnXtjmc/OPnmLN93NLNpnq4AJNZQ3QdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '7c335f0de71e4cb1379a75134e3a7a50')

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

# --- 🎁 ฟังก์ชันสุ่มคำพูดพี่รวย ---
def get_witty_speech(type, data=None):
    speeches = {
        "start": [
            f"📢 @all สมรภูมิคนอยากรวยเริ่มแล้ว! บิดขั้นต่ำ {data} บ. ใครร้อนเงินจัดมา!",
            f"📢 @all ได้เวลาถอนทุนคืน! พี่รวยเปิดประมูลแล้วครับ ขั้นต่ำแค่ {data} บ. ลุยยย!",
            f"📢 @all เงินก้อนรออยู่ตรงหน้า ใครช้าอดนะจ๊ะ! เริ่มต้นที่ {data} บ. พิมพ์มา!"
        ],
        "accept": [
            f"✅ รับยอด {data['bid']} บ. จากคุณ {data['name']} ใจถึงพึ่งได้จริงๆ ครับ!",
            f"✅ ยอด {data['bid']} บ. มาแล้วโดยคุณ {data['name']} ทรงอย่างแบด ยอดบิดอย่างหล่อ!",
            f"✅ ฮั่นแน่! คุณ {data['name']} ปาดมาที่ {data['bid']} บ. แล้ว มีใครจะสู้ต่อไหม?"
        ],
        "30s": [
            f"⏳ พี่รวยแง้มค้อนแล้ว! 30 วิสุดท้าย ยอดปัจจุบัน {data} บ. จะปิดที่ใคร!",
            f"⏳ โค้งสุดท้าย 30 วินาที! ยอด {data} บ. มีใครจะหล่อปาดหน้าวินาทีสุดท้ายไหม?",
            f"⏳ อีก 30 วิจะเคาะแล้วนะ! {data} บ. คือราคาสุดท้ายจริงๆ เหรอสมาชิก?"
        ],
        "low_bid": [
            f"⚠️ ยอดน้อยไปหน่อยนะจ๊ะ! ต้องบิดอย่างน้อย {data} บ. พี่รวยถึงจะรับยอด",
            f"⚠️ ขั้นต่ำคือ {data} บ. ครับสมาชิก บิดต่ำกว่านี้พี่รวยปวดใจ!",
            f"⚠️ ผิดกติกาครับ! ต้องใส่ {data} บ. ขึ้นไป ลองใหม่อีกทีนะคนหล่อ/คนสวย"
        ]
    }
    return random.choice(speeches[type])

# ======================================================
# 🕒 ส่วนที่ 1: ระบบนับถอยหลัง
# ======================================================
def countdown_logic(reply_to_id, bid_amount):
    time.sleep(30)
    state = get_state()
    auction = state.get("auction", {})
    
    if auction.get("is_active") and auction.get("current_price") == bid_amount:
        # [สุ่มคำพูด]: แจ้งเตือน 30 วิสุดท้าย
        try:
            msg_30s = get_witty_speech("30s", bid_amount)
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg_30s))
        except LineBotApiError as e:
            print(f"Push Error (30s): {e}")

        # นับถอยหลัง 10-1
        for i in range(10, 0, -1):
            time.sleep(3)
            curr_state = get_state()
            curr_auction = curr_state.get("auction", {})
            if not curr_auction.get("is_active") or curr_auction.get("current_price") != bid_amount:
                return 

            try:
                line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
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
            
            try:
                msg = f"🏁 ปิดประมูลเรียบร้อย!\n🏆 เศรษฐีใหม่คือคุณ: {winner}\n💰 ยอดบิดที่ชนะ: {bid_amount} บ.\n📅 วันที่ชนะ: {now_date}\n⚠️ ส่งเลขบัญชีมาด้วยครับเพื่อนๆ รอโอน!"
                line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))
            except LineBotApiError as e:
                print(f"Push Error (End): {e}")

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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับสลิปคุณ {name} แล้วครับ หวานเจี๊ยบ~\n📊 จ่ายแล้ว: {paid_count}/{total} คน"))
    except: pass

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    # 1. ตั้งค่าวงแชร์
    if text == "ตั้งค่าวงแชร์":
        ref.update({"setup_step": 1, "won_names": [], "winners_history": [], "reminded": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 เริ่มตั้งค่าใหม่ (พี่รวย)\n1. ยอดส่งต่อคนเท่าไหร่? (ตัวเลข)"))
        return

    # 2. เริ่มประมูล
    if text == "/start_bid":
        ref.update({"setup_step": 0})
        ref.child('auction').update({"is_active": True, "current_price": 0, "winner_name": "", "winner_id": ""})
        members = state.get("members") or {}
        for mid in members: ref.child('members').child(mid).update({"has_paid": False})
        
        min_inc = state.get('auction',{}).get('min_increment', 0)
        # [สุ่มคำพูด]: เริ่มประมูล
        msg_start = get_witty_speech("start", min_inc)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_start))
        return

    if text == "/end_share":
        ref.set({})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ล้างข้อมูลจบวงแชร์เรียบร้อยครับ"))
        return

    if text == "/help":
        msg = (f"📖 คู่มือพี่รวย (V.{BOT_VERSION})\n"
               "• ตั้งค่าวงแชร์ : ตั้งค่าใหม่\n"
               "• /start_bid : เปิดประมูล\n"
               "• /status : ดูสถานะ/กองกลาง\n"
               "• /check_pay : เช็กคนโอน\n"
               "• /use_pot [ยอด] [เหตุผล] : ใช้เงินกองกลาง\n"
               "• /remove_winner [ชื่อ] : ลบชื่อคนชนะ\n"
               "• /end_share : จบวง (ล้างข้อมูล)\n"
               "• [ส่งรูปสลิป] : เช็กชื่ออัตโนมัติ")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/version":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 พี่รวย V.{BOT_VERSION}\nUpdate: {LAST_UPDATE}"))
        return

    if text == "/status":
        history = state.get("winners_history", [])
        hist_text = "\n".join([f"{i+1}. {h['name']} ({h['bid']}บ.)" for i, h in enumerate(history)])
        pot_used = sum(u['amount'] for u in state.get("pot_usage", []))
        msg = (f"📊 สรุปสถานะวงแชร์\n📅 {get_now_str()}\n"
               f"💰 ส่ง: {state.get('share_amount')} บ. | บิดขั้นต่ำ: {state.get('auction',{}).get('min_increment')} บ.\n"
               f"💎 กองกลางสะสม: {state.get('pot_balance', 0)} บ.\n(ใช้ไป {pot_used} บ. | เหลือ {state.get('pot_balance', 0) - pot_used} บ.)\n\n"
               f"🏆 ทำเนียบคนชนะ:\n{hist_text if hist_text else '- ยังไม่มี -'}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "/check_pay":
        members = state.get("members") or {}
        paid = [m['name'] for m in members.values() if m.get('has_paid')]
        unpaid = [m['name'] for m in members.values() if not m.get('has_paid')]
        msg = f"💳 เช็คยอดโอน\n✅ โอนแล้ว ({len(paid)}): {', '.join(paid)}\n❌ ยังไม่โอน ({len(unpaid)}): {', '.join(unpaid)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("/remove_winner"):
        name = text.replace("/remove_winner", "").strip()
        won = state.get("won_names", [])
        if name in won:
            won.remove(name)
            ref.update({"won_names": won})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑 ลบชื่อคุณ {name} เรียบร้อย"))
        return

    if text.startswith("/use_pot"):
        parts = text.split()
        if len(parts) >= 3:
            amt = int(parts[1])
            reason = " ".join(parts[2:])
            usage = state.get("pot_usage", [])
            usage.append({"amount": amt, "reason": reason, "date": get_now_str()})
            ref.update({"pot_usage": usage})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"💸 หักกองกลาง {amt} บ. (ค่า {reason})"))
        return

    # 3. โหมดตั้งค่า (Setup)
    step = state.get("setup_step", 0)
    if step > 0:
        if step == 1 and text.isdigit():
            ref.update({"share_amount": int(text), "setup_step": 2})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="2. สมาชิกทั้งหมดกี่คน?"))
        elif step == 2 and text.isdigit():
            ref.update({"total_members": int(text), "setup_step": 3})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="3. ยอดบิดขั้นต่ำกี่บาท?"))
        elif step == 3 and text.isdigit():
            ref.child('auction').update({"min_increment": int(text)})
            ref.update({"setup_step": 4})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="4. เงินกองกลางเริ่มต้นกี่บาท? (ใส่ 0 ได้)"))
        elif step == 4:
            ref.update({"pot_balance": int(text), "setup_step": 5})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="5. วันที่เปียร์แชร์? (1-31)"))
        elif step == 5:
            ref.update({"play_date": text, "setup_step": 6})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="6. เวลาประมูล? (เช่น 20:00)"))
        elif step == 6:
            ref.update({"play_time": text, "setup_step": 0, "group_id": reply_to_id})
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 ตั้งค่าสำเร็จ! พี่รวยพร้อมทำงานแล้วครับ!"))
        return

    # 4. ระบบบิดราคา (Bidding)
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
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ คุณ {name} เปียร์ไปแล้ว ให้เพื่อนรวยบ้างครับ!"))
                    return

                ref.child('auction').update({"current_price": bid, "winner_name": name, "winner_id": user_id})
                
                # [สุ่มคำพูด]: รับยอดบิด
                msg_accept = get_witty_speech("accept", {"name": name, "bid": bid})
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_accept))
                
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        else:
            # [สุ่มคำพูด]: บิดต่ำเกินไป
            msg_low = get_witty_speech("low_bid", required)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_low))
        return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)