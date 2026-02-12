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
BOT_VERSION = "1.3.1"
LAST_UPDATE = "12/02/2026 (Fixed Auction)"

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

# --- 3. ระบบนับถอยหลัง (แก้ไขให้ชัวร์) ---
def countdown_logic(reply_to_id, bid_amount):
    # จังหวะที่ 1: รอ 30 วินาที
    time.sleep(30)
    
    # ดึงค่าล่าสุดมาเช็ค
    state = get_state()
    auction = state.get("auction", {})
    
    # เช็คว่ายังเปิดอยู่ไหม และราคายังเท่าเดิมไหม (ถ้าไม่เท่าแสดงว่ามีคนบิดเพิ่มแล้ว ให้จบ Thread นี้เงียบๆ)
    if auction.get("is_active") and auction.get("current_price") == bid_amount:
        # แจ้งเตือน 30 วิ
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"⏳ พี่รวยแง้มค้อนแล้ว! เหลือ 30 วิสุดท้าย ยอดปัจจุบัน {bid_amount} บ. มีใครสู้เพิ่มไหม?"))
        
        # จังหวะที่ 2: นับ 10 ถอยหลัง
        for i in range(10, 0, -1):
            time.sleep(3) # หน่วง 3 วินาทีก่อนนับ
            curr_state = get_state()
            curr_auction = curr_state.get("auction", {})
            
            # Anti-Sniping: เช็คทุกจังหวะ ถ้ามีคนบิดแทรก ให้หยุดนับทันที
            if not curr_auction.get("is_active") or curr_auction.get("current_price") != bid_amount:
                return 
            
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=str(i)))
        
        # เช็คครั้งสุดท้ายก่อนปิด
        final_state = get_state()
        final_auction = final_state.get("auction", {})
        if final_auction.get("is_active") and final_auction.get("current_price") == bid_amount:
            # ปิดประมูล
            winner = final_auction.get("winner_name", "ไม่ระบุ")
            now_date = get_now_str().split()[0]
            
            # อัปเดตสถานะปิด
            ref.child('auction').update({"is_active": False, "waiting_for_account": True})
            
            # บันทึกประวัติ
            history = final_state.get("winners_history", [])
            history.append({"name": winner, "date": now_date, "bid": bid_amount})
            
            # เพิ่มรายชื่อคนเปียร์แล้ว
            won_names = final_state.get("won_names", [])
            if winner not in won_names:
                won_names.append(winner)
            
            ref.update({
                "winners_history": history, 
                "won_names": won_names
            })

            # ประกาศผล
            msg = f"🏁 ปิดประมูลเรียบร้อย!\n🏆 ผู้ชนะ: คุณ {winner}\n💰 ยอดบิด: {bid_amount} บ.\n📅 วันที่ชนะ: {now_date}\n⚠️ รบกวนส่งเลขบัญชีด้วยครับ"
            line_bot_api.push_message(reply_to_id, TextSendMessage(text=msg))

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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ พี่รวยรับสลิปคุณ {name} แล้ว!\n📊 จ่ายแล้ว: {paid_count}/{total} คน"))
    except: pass

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    state = get_state()
    user_id = event.source.user_id
    reply_to_id = event.source.group_id if hasattr(event.source, 'group_id') else user_id

    # --- 1. ตั้งค่าวงแชร์ (Setup) ---
    if text == "ตั้งค่าวงแชร์":
        ref.update({"setup_step": 1, "won_names": [], "winners_history": [], "reminded": False})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 เริ่มตั้งค่าใหม่ (พี่รวย)\n1. ยอดส่งต่อคนเท่าไหร่? (ตัวเลข)"))
        return

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

    # --- 2. เปิดประมูล (Start Bid) ---
    if text == "/start_bid":
        # รีเซ็ตค่าประมูลใหม่หมด
        ref.child('auction').update({
            "is_active": True, 
            "current_price": 0, 
            "winner_name": "", 
            "winner_id": ""
        })
        # ล้างสถานะการจ่ายเงิน
        for mid in state.get("members", {}): ref.child('members').child(mid).update({"has_paid": False})
        
        # ประกาศเปิดประมูล (คำพูดพี่รวย)
        min_inc = state.get('auction',{}).get('min_increment', 0)
        date_str = get_now_str().split()[0]
        msg = f"📢 พี่รวยเปิดประมูลรอบวันที่ {date_str}!\n📈 บิดขั้นต่ำ: {min_inc} บ.\n⏳ ใครอยากรวยพิมพ์ตัวเลขบิดมาเลยครับสมาชิก!"
        line_bot_api.push_message(reply_to_id, TextSendMessage(text=f"📢 @all {msg}"))
        return

    # --- 3. ระบบบิดราคา (Bidding Logic) ---
    if text.isdigit() and state.get("auction", {}).get("is_active"):
        bid = int(text)
        curr = state["auction"].get("current_price", 0)
        min_inc = state["auction"].get("min_increment", 0)
        
        # ต้องบิดมากกว่ายอดเดิม + ขั้นต่ำ (ยกเว้นเปิดบิดแรก)
        required = curr + min_inc if curr > 0 else min_inc
        
        if bid >= required:
            try:
                # เช็คชื่อ
                profile = line_bot_api.get_group_member_profile(reply_to_id, user_id) if hasattr(event.source, 'group_id') else line_bot_api.get_profile(user_id)
                name = profile.display_name
                
                # เช็คสิทธิ์ว่าเคยเปียร์ไปยัง
                if name in state.get("won_names", []):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ คุณ {name} เปียร์ไปแล้ว ไม่มีสิทธิ์บิดครับ"))
                    return

                # อัปเดตยอด
                ref.child('auction').update({"current_price": bid, "winner_name": name, "winner_id": user_id})
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ รับยอด {bid} บ. โดยคุณ {name}"))
                
                # เริ่มนับถอยหลัง (Thread)
                threading.Thread(target=countdown_logic, args=[reply_to_id, bid]).start()
            except: pass
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ ยอดน้อยไป! ต้องบิดอย่างน้อย {required} บ. ครับ"))
        return

    # --- 4. คำสั่งจัดการอื่นๆ ---
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
        members = state.get("members", {})
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
        
    if text == "/end_share":
        ref.set({})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ล้างข้อมูลจบวงแชร์เรียบร้อยครับ"))
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

    if text == "/version":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 พี่รวย V.{BOT_VERSION}\nUpdate: {LAST_UPDATE}"))
        return

    if text == "/help":
        msg = (f"📖 คู่มือพี่รวย (V.{BOT_VERSION})\n"
               "• ตั้งค่าวงแชร์ : ตั้งค่าใหม่\n"
               "• /start_bid : เปิดประมูล (มี @all)\n"
               "• /status : ดูสถานะ/กองกลาง\n"
               "• /check_pay : เช็กคนโอน\n"
               "• /use_pot [ยอด] [เหตุผล] : ใช้เงินกองกลาง\n"
               "• /remove_winner [ชื่อ] : ลบชื่อคนชนะ\n"
               "• /end_share : จบวง (ล้างข้อมูล)\n"
               "• [ส่งรูปสลิป] : เช็กชื่ออัตโนมัติ")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)