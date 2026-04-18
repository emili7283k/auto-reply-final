# -*- coding: utf-8 -*-
"""
Uzeron ReplyBot — main_bot.py
Dashboard bot: onboarding, channel gate, login flow, settings, admin commands
OTP login uses inline numpad buttons (copied from working free_bot reference)
"""

import os
import sys
import asyncio
import psycopg2
import json
import pytz
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
)

load_dotenv()

# ============================================================
# ENV VALIDATION
# ============================================================
REQUIRED_VARS = [
    'API_ID', 'API_HASH', 'MAIN_BOT_TOKEN', 'LOGGER_BOT_TOKEN',
    'ADMIN_IDS', 'DATABASE_URL', 'GROQ_API_KEY',
    'UPDATES_CHANNEL', 'COMMUNITY_GROUP', 'SUPPORT_LINK', 'CONTACT_USERNAME'
]
missing_vars = [v for v in REQUIRED_VARS if not os.getenv(v)]
if missing_vars:
    print(f"❌ FATAL: Missing environment variables: {', '.join(missing_vars)}")
    sys.exit(1)

BOT_API_ID       = int(os.getenv('API_ID'))
BOT_API_HASH     = os.getenv('API_HASH')
MAIN_BOT_TOKEN   = os.getenv('MAIN_BOT_TOKEN')
LOGGER_BOT_TOKEN = os.getenv('LOGGER_BOT_TOKEN')
ADMIN_IDS        = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
DATABASE_URL     = os.getenv('DATABASE_URL')
UPDATES_CHANNEL  = os.getenv('UPDATES_CHANNEL')
COMMUNITY_GROUP  = os.getenv('COMMUNITY_GROUP')
SUPPORT_LINK     = os.getenv('SUPPORT_LINK')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

IST = pytz.timezone('Asia/Kolkata')

DEFAULT_GREETING = (
    "Hi! 👋 Thanks for reaching out. "
    "The owner is currently unavailable but I'm here to help. "
    "What are you looking for today?"
)

# ============================================================
# BOT API HELPERS
# ============================================================
def _bot(method, data=None):
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/{method}"
    try:
        processed = {k: json.dumps(v) if isinstance(v, (dict, list)) else v
                     for k, v in (data or {}).items()}
        r = requests.post(url, data=processed, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Bot API [{method}]: {e}")
        return {}

def send_msg(chat_id, text, keyboard=None):
    d = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if keyboard:
        d["reply_markup"] = json.dumps(keyboard)
    return _bot("sendMessage", d)

def edit_msg(chat_id, msg_id, text, keyboard=None):
    d = {"chat_id": chat_id, "message_id": msg_id,
         "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if keyboard:
        d["reply_markup"] = json.dumps(keyboard)
    _bot("editMessageText", d)

def kb(buttons):
    return {"inline_keyboard": buttons}

# ============================================================
# NUMPAD KEYBOARD — exact same pattern as working free_bot
# ============================================================
def numpad_keyboard(prefix, entered=""):
    display = entered if entered else "—"
    rows = [
        [{"text": f"📟  {display}  ", "callback_data": f"{prefix}_display"}],
        [{"text": "1", "callback_data": f"{prefix}_1"},
         {"text": "2", "callback_data": f"{prefix}_2"},
         {"text": "3", "callback_data": f"{prefix}_3"}],
        [{"text": "4", "callback_data": f"{prefix}_4"},
         {"text": "5", "callback_data": f"{prefix}_5"},
         {"text": "6", "callback_data": f"{prefix}_6"}],
        [{"text": "7", "callback_data": f"{prefix}_7"},
         {"text": "8", "callback_data": f"{prefix}_8"},
         {"text": "9", "callback_data": f"{prefix}_9"}],
        [{"text": "⌫ Del",     "callback_data": f"{prefix}_del"},
         {"text": "0",         "callback_data": f"{prefix}_0"},
         {"text": "✅ Submit", "callback_data": f"{prefix}_submit"}],
        [{"text": "❌ Cancel Login", "callback_data": "cancel_login"}],
    ]
    return kb(rows)

# ============================================================
# MEMBERSHIP CHECK
# ============================================================
def check_member(user_id, chat_username):
    r = _bot("getChatMember", {
        "chat_id": f"@{chat_username}",
        "user_id": user_id
    })
    if not r.get("ok"):
        err = r.get("description", "unknown error")
        return False, err
    status = r.get("result", {}).get("status", "left")
    is_member = status in ("member", "administrator", "creator", "restricted")
    return is_member, None

def user_has_joined(user_id):
    ch_ok,  ch_err  = check_member(user_id, UPDATES_CHANNEL.lstrip('@'))
    com_ok, com_err = check_member(user_id, COMMUNITY_GROUP.lstrip('@'))
    return ch_ok, com_ok, ch_err, com_err

# ============================================================
# KEYBOARDS
# ============================================================
def join_gate_keyboard():
    return kb([
        [{"text": "📢 Join Updates Channel",
          "url": f"https://t.me/{UPDATES_CHANNEL.lstrip('@')}"}],
        [{"text": "👥 Join Community",
          "url": f"https://t.me/{COMMUNITY_GROUP.lstrip('@')}"}],
        [{"text": "✅ I've Joined — Continue", "callback_data": "check_join"}],
    ])

def welcome_keyboard():
    return kb([
        [{"text": "🎟️ Activate Premium", "callback_data": "activate_premium"},
         {"text": "💰 Get Premium",      "callback_data": "get_premium"}]
    ])

def dashboard_keyboard(auto_reply=1):
    toggle = "🟢 Bot ON — Tap to turn OFF" if auto_reply else "🔴 Bot OFF — Tap to turn ON"
    return kb([
        [{"text": "👤 My Account",     "callback_data": "account"},
         {"text": "📊 Status",         "callback_data": "status"}],
        [{"text": "📋 Set Price List", "callback_data": "set_pricelist"},
         {"text": "🏪 Business Name",  "callback_data": "set_business"}],
        [{"text": "🤖 AI Greeting Msg","callback_data": "set_greeting"},
         {"text": "📩 My Leads",       "callback_data": "my_leads"}],
        [{"text": toggle,              "callback_data": "toggle_bot"}],
        [{"text": "🔑 Login Account",  "callback_data": "login"},
         {"text": "🚪 Logout",         "callback_data": "logout"}],
        [{"text": "💎 Subscription",   "callback_data": "subscription"},
         {"text": "🔔 Updates",        "url": "https://t.me/Uzeron_AdsBot"}],
        [{"text": "❓ How to Use",     "url": "https://t.me/Uzeron_Ads"}],
    ])

def back_keyboard():
    return kb([[{"text": "🏠 Dashboard", "callback_data": "dashboard"}]])

def account_keyboard():
    return kb([
        [{"text": "🔑 Login",  "callback_data": "login"},
         {"text": "🚪 Logout", "callback_data": "logout"}],
        [{"text": "🏠 Dashboard", "callback_data": "dashboard"}],
    ])

# ============================================================
# MESSAGE TEXTS
# ============================================================
def join_gate_text(missing=None):
    base = (
        "👋 <b>Welcome to Uzeron ReplyBot!</b>\n\n"
        "To unlock the bot, please join both:\n\n"
        "📢 <b>Updates Channel</b> — latest news &amp; updates\n"
        "👥 <b>Community Group</b> — support &amp; discussion\n\n"
        "<i>After joining both, tap the button below.</i>"
    )
    if missing:
        base += "\n\n❌ <b>Still not joined:</b>\n" + "\n".join(f"• {m}" for m in missing)
    return base

def welcome_text():
    return (
        "⚡ <b>Welcome to Uzeron ReplyBot!</b>\n\n"
        "╔══════════════════════╗\n"
        "║ 🤖 AI Auto-Reply 24/7\n"
        "║ 💬 Trained on your price list\n"
        "║ 📩 Lead capture & notifications\n"
        "║ 🌐 Replies in buyer's language\n"
        "║ 💰 Never miss a sale again\n"
        "╚══════════════════════╝\n\n"
        "💎 <b>Activate your premium subscription below.</b>"
    )

def dashboard_text(user):
    biz    = user.get('business_name') or "❌ Not set"
    phone  = user.get('phone') or "❌ Not connected"
    pl     = "✅ Set" if user.get('price_list') else "❌ Not set"
    greet  = "✅ Set" if user.get('greeting_msg') else "⚙️ Default"
    status = "🟢 ON" if user.get('auto_reply') else "🔴 OFF"
    expiry = user.get('subscription_expiry', '')
    days_left = 0
    if expiry:
        try:
            days_left = max(0, (datetime.strptime(expiry, '%Y-%m-%d') - datetime.now()).days)
        except Exception:
            pass
    return (
        "⚡ <b>UZERON REPLYBOT — Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏪 <b>Business:</b> {biz}\n"
        f"📱 <b>Account:</b> <code>{phone}</code>\n"
        f"📋 <b>Price List:</b> {pl}\n"
        f"👋 <b>Greeting:</b> {greet}\n"
        f"🤖 <b>Auto-Reply:</b> {status}\n"
        f"💎 <b>Premium:</b> {days_left} days left\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

def account_text(user):
    phone  = user.get('phone') or "Not connected"
    conn   = "✅ Connected" if user.get('session_string') else "❌ Not connected"
    leads  = user.get('total_leads', 0)
    expiry = user.get('subscription_expiry', 'N/A')
    return (
        "👤 <b>My Account</b>\n\n"
        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
        f"🔗 <b>Status:</b> {conn}\n"
        f"📩 <b>Total Leads:</b> {leads}\n"
        f"💎 <b>Subscription Expiry:</b> {expiry}"
    )

def status_text(user):
    biz    = user.get('business_name') or "Not set"
    phone  = user.get('phone') or "Not connected"
    pl     = "✅ Set" if user.get('price_list') else "❌ Not set"
    status = "🟢 ON" if user.get('auto_reply') else "🔴 OFF"
    leads  = user.get('total_leads', 0)
    return (
        "📊 <b>System Status</b>\n\n"
        f"🏪 <b>Business:</b> {biz}\n"
        f"📱 <b>Account:</b> <code>{phone}</code>\n"
        f"📋 <b>Price List:</b> {pl}\n"
        f"🤖 <b>Bot:</b> {status}\n"
        f"📩 <b>Leads Captured:</b> {leads}"
    )

def subscription_text(user):
    expiry = user.get('subscription_expiry', 'N/A')
    days_left = 0
    if expiry and expiry != 'N/A':
        try:
            days_left = max(0, (datetime.strptime(expiry, '%Y-%m-%d') - datetime.now()).days)
        except Exception:
            pass
    return (
        "💎 <b>Subscription Details</b>\n\n"
        f"📅 <b>Expiry Date:</b> {expiry}\n"
        f"⏳ <b>Days Remaining:</b> {days_left}\n\n"
        "To renew your subscription, contact us below."
    )

# ============================================================
# DATABASE
# ============================================================
class Database:
    def get_conn(self):
        return psycopg2.connect(DATABASE_URL, sslmode='require')

    def init_db(self):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS reply_users (
            user_id             BIGINT PRIMARY KEY,
            username            TEXT,
            phone               TEXT,
            api_id              INTEGER,
            api_hash            TEXT,
            session_string      TEXT,
            business_name       TEXT,
            price_list          TEXT,
            greeting_msg        TEXT,
            auto_reply          INTEGER DEFAULT 1,
            subscription_expiry TEXT,
            total_leads         INTEGER DEFAULT 0,
            created_at          TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS reply_codes (
            code      TEXT PRIMARY KEY,
            days      INTEGER,
            used      INTEGER DEFAULT 0,
            used_by   BIGINT,
            used_at   TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS reply_leads (
            id                SERIAL PRIMARY KEY,
            seller_id         BIGINT,
            customer_id       BIGINT,
            customer_name     TEXT,
            customer_username TEXT,
            message           TEXT,
            bot_reply         TEXT,
            created_at        TEXT
        )''')
        conn.commit(); conn.close()
        print("✓ Database tables verified")

    def get_user(self, user_id):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''SELECT user_id,username,phone,api_id,api_hash,session_string,
                     business_name,price_list,greeting_msg,auto_reply,
                     subscription_expiry,total_leads,created_at
                     FROM reply_users WHERE user_id=%s''', (user_id,))
        row = c.fetchone(); conn.close()
        if not row: return None
        keys = ['user_id','username','phone','api_id','api_hash','session_string',
                'business_name','price_list','greeting_msg','auto_reply',
                'subscription_expiry','total_leads','created_at']
        return dict(zip(keys, row))

    def register_user(self, user_id, username):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('SELECT user_id FROM reply_users WHERE user_id=%s', (user_id,))
        if not c.fetchone():
            c.execute('INSERT INTO reply_users(user_id,username,created_at) VALUES(%s,%s,%s)',
                      (user_id, username, datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
        conn.close()

    def is_premium(self, user_id):
        user = self.get_user(user_id)
        if not user or not user.get('subscription_expiry'): return False
        try:
            return datetime.strptime(user['subscription_expiry'], '%Y-%m-%d') > datetime.now()
        except Exception:
            return False

    def redeem_code(self, user_id, code):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('SELECT code,days,used FROM reply_codes WHERE code=%s', (code,))
        row = c.fetchone()
        if not row:
            conn.close(); return {'ok': False, 'error': 'Code not found'}
        if row[2]:
            conn.close(); return {'ok': False, 'error': 'Code already used'}
        days = row[1]
        user = self.get_user(user_id)
        try:
            cur = datetime.strptime(user['subscription_expiry'], '%Y-%m-%d') \
                  if user and user.get('subscription_expiry') else datetime.now()
            if cur < datetime.now(): cur = datetime.now()
        except Exception:
            cur = datetime.now()
        new_expiry = (cur + timedelta(days=days)).strftime('%Y-%m-%d')
        c.execute('UPDATE reply_codes SET used=1,used_by=%s,used_at=%s WHERE code=%s',
                  (user_id, datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'), code))
        c.execute('UPDATE reply_users SET subscription_expiry=%s WHERE user_id=%s',
                  (new_expiry, user_id))
        conn.commit(); conn.close()
        return {'ok': True, 'days': days, 'expiry': new_expiry}

    def update_field(self, user_id, field, value):
        allowed = ['business_name','price_list','greeting_msg','auto_reply',
                   'phone','api_id','api_hash','session_string']
        if field not in allowed: return
        conn = self.get_conn(); c = conn.cursor()
        c.execute(f'UPDATE reply_users SET {field}=%s WHERE user_id=%s', (value, user_id))
        conn.commit(); conn.close()

    def save_session(self, user_id, phone, api_id, api_hash, session_string):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''UPDATE reply_users SET phone=%s,api_id=%s,api_hash=%s,
                     session_string=%s,auto_reply=1 WHERE user_id=%s''',
                  (phone, api_id, api_hash, session_string, user_id))
        conn.commit(); conn.close()

    def logout_user(self, user_id):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''UPDATE reply_users SET phone=NULL,api_id=NULL,api_hash=NULL,
                     session_string=NULL,auto_reply=0 WHERE user_id=%s''', (user_id,))
        conn.commit(); conn.close()

    def toggle_auto_reply(self, user_id):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('SELECT auto_reply FROM reply_users WHERE user_id=%s', (user_id,))
        row = c.fetchone()
        new_val = 0 if (row and row[0]) else 1
        c.execute('UPDATE reply_users SET auto_reply=%s WHERE user_id=%s', (new_val, user_id))
        conn.commit(); conn.close()
        return new_val

    def get_leads(self, seller_id, limit=8):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''SELECT customer_name,customer_username,message,created_at
                     FROM reply_leads WHERE seller_id=%s ORDER BY id DESC LIMIT %s''',
                  (seller_id, limit))
        rows = c.fetchall(); conn.close()
        return rows

    def add_code(self, code, days):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('INSERT INTO reply_codes(code,days) VALUES(%s,%s) ON CONFLICT(code) DO NOTHING',
                  (code, days))
        conn.commit(); conn.close()

    def list_codes(self):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('SELECT code,days,used,used_by FROM reply_codes WHERE used=0')
        rows = c.fetchall(); conn.close(); return rows

    def list_premium_users(self):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('''SELECT user_id,username,phone,subscription_expiry FROM reply_users
                     WHERE subscription_expiry IS NOT NULL ORDER BY subscription_expiry DESC''')
        rows = c.fetchall(); conn.close(); return rows

    def revoke_premium(self, user_id):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('UPDATE reply_users SET subscription_expiry=NULL,auto_reply=0 WHERE user_id=%s',
                  (user_id,))
        conn.commit(); conn.close()

    def get_stats(self):
        conn = self.get_conn(); c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM reply_users')
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM reply_users WHERE subscription_expiry > %s",
                  (datetime.now().strftime('%Y-%m-%d'),))
        active_subs = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM reply_users WHERE session_string IS NOT NULL')
        connected = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM reply_leads')
        leads = c.fetchone()[0]
        conn.close()
        return {'total': total, 'active_subs': active_subs, 'connected': connected, 'leads': leads}

    def get_all_premium_ids(self):
        conn = self.get_conn(); c = conn.cursor()
        c.execute("SELECT user_id FROM reply_users WHERE subscription_expiry > %s",
                  (datetime.now().strftime('%Y-%m-%d'),))
        rows = c.fetchall(); conn.close()
        return [r[0] for r in rows]

# ============================================================
# LOGGER
# ============================================================
class Logger:
    def __init__(self, token):
        self.token = token
    def log(self, text):
        for admin_id in ADMIN_IDS:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    data={'chat_id': admin_id, 'text': text, 'parse_mode': 'HTML'},
                    timeout=10
                )
            except Exception as e:
                print(f"Logger error: {e}")

# ============================================================
# MAIN BOT
# ============================================================
class UzeronReplyBot:
    def __init__(self):
        bot_session      = os.getenv('BOT_SESSION_STRING', '')
        self.bot         = TelegramClient(StringSession(bot_session), BOT_API_ID, BOT_API_HASH)
        self.db          = Database()
        self.logger      = Logger(LOGGER_BOT_TOKEN)
        self.login_states   = {}   # uid → {step, api_id, api_hash, phone, client, otp_digits, otp_msg_id, phone_code_hash}
        self.pending_input  = {}   # uid → field name
        self._join_cache    = {}   # uid → True (joined)

    async def start(self):
        self.db.init_db()
        await self.bot.start(bot_token=MAIN_BOT_TOKEN)
        session_str = self.bot.session.save()
        if not os.getenv('BOT_SESSION_STRING'):
            print("="*60)
            print("Add to Railway env → BOT_SESSION_STRING:")
            print(session_str)
            print("="*60)
        self._register_handlers()
        print("✓ Uzeron ReplyBot — main_bot live!")
        await self.bot.run_until_disconnected()

    # ── JOIN GATE ──────────────────────────────────────────────
    def _check_join(self, uid):
        if uid in ADMIN_IDS:
            return True, []
        if self._join_cache.get(uid):
            return True, []
        missing = []
        ch_ok,  ch_err  = check_member(uid, UPDATES_CHANNEL.lstrip('@'))
        com_ok, com_err = check_member(uid, COMMUNITY_GROUP.lstrip('@'))
        # If both API calls fail, fail open (don't block user due to API issue)
        if ch_err and com_err:
            self._join_cache[uid] = True
            return True, []
        if not ch_ok and not ch_err:
            missing.append("📢 Updates Channel")
        if not com_ok and not com_err:
            missing.append("👥 Community Group")
        if not missing:
            self._join_cache[uid] = True
        return len(missing) == 0, missing

    # ── HANDLERS ───────────────────────────────────────────────
    def _register_handlers(self):

        # ── /start ──
        @self.bot.on(events.NewMessage(pattern='/start'))
        async def h_start(event):
            uid = event.sender_id
            self.db.register_user(uid, event.sender.username or '')
            all_joined, missing = self._check_join(uid)
            if not all_joined:
                send_msg(uid, join_gate_text(missing), join_gate_keyboard()); return
            if not self.db.is_premium(uid):
                send_msg(uid, welcome_text(), welcome_keyboard()); return
            user = self.db.get_user(uid)
            send_msg(uid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))

        # ── /redeem ──
        @self.bot.on(events.NewMessage(pattern=r'/redeem(?:\s+(.+))?'))
        async def h_redeem(event):
            uid   = event.sender_id
            match = event.pattern_match.group(1)
            if not match:
                send_msg(uid, "❌ Usage: <code>/redeem YOUR_CODE</code>"); return
            code   = match.strip()
            result = self.db.redeem_code(uid, code)
            if result['ok']:
                send_msg(uid,
                    f"✅ <b>Premium Activated!</b>\n\n"
                    f"🎟️ Code: <code>{code}</code>\n"
                    f"📅 Days granted: <b>{result['days']}</b>\n"
                    f"⏳ Expiry: <b>{result['expiry']}</b>",
                    kb([[{"text": "⚡ Open Dashboard", "callback_data": "dashboard"}]]))
                self.logger.log(
                    f"✅ Premium activated\n"
                    f"👤 {uid} (@{self.db.get_user(uid).get('username','')})\n"
                    f"🎟️ Code: {code} | Days: {result['days']}")
            else:
                send_msg(uid,
                    f"❌ <b>Invalid Code:</b> {result['error']}",
                    kb([[{"text": f"💬 Contact {CONTACT_USERNAME}",
                          "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}]]))

        # ── /cancel ──
        @self.bot.on(events.NewMessage(pattern='/cancel'))
        async def h_cancel(event):
            uid = event.sender_id
            await self._cleanup_login(uid)
            if uid in self.pending_input: del self.pending_input[uid]
            user = self.db.get_user(uid)
            if user and self.db.is_premium(uid):
                send_msg(uid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))
            else:
                await h_start(event)

        # ── ADMIN: /addcode ──
        @self.bot.on(events.NewMessage(pattern=r'/addcode\s+(\S+)\s+(\d+)'))
        async def h_addcode(event):
            if event.sender_id not in ADMIN_IDS: return
            code = event.pattern_match.group(1).strip()
            days = int(event.pattern_match.group(2))
            self.db.add_code(code, days)
            await event.reply(f"✅ Code created!\n🎟️ <code>{code}</code> — {days} days", parse_mode='html')

        # ── ADMIN: /codes ──
        @self.bot.on(events.NewMessage(pattern='/codes'))
        async def h_codes(event):
            if event.sender_id not in ADMIN_IDS: return
            codes = self.db.list_codes()
            if not codes:
                await event.reply("📭 No unused codes."); return
            lines = "\n".join(f"• <code>{c[0]}</code> — {c[1]} days" for c in codes)
            await event.reply(f"🎟️ <b>Unused Codes ({len(codes)}):</b>\n\n{lines}", parse_mode='html')

        # ── ADMIN: /users ──
        @self.bot.on(events.NewMessage(pattern='/users'))
        async def h_users(event):
            if event.sender_id not in ADMIN_IDS: return
            users = self.db.list_premium_users()
            if not users:
                await event.reply("👥 No premium users yet."); return
            lines = "\n".join(
                f"• {uid} (@{uname or 'N/A'}) | {phone or 'No phone'} | {exp}"
                for uid, uname, phone, exp in users)
            await event.reply(f"👥 <b>Premium Users ({len(users)}):</b>\n\n{lines}", parse_mode='html')

        # ── ADMIN: /revoke ──
        @self.bot.on(events.NewMessage(pattern=r'/revoke\s+(\d+)'))
        async def h_revoke(event):
            if event.sender_id not in ADMIN_IDS: return
            uid = int(event.pattern_match.group(1))
            self.db.revoke_premium(uid)
            await event.reply(f"✅ Premium revoked for {uid}")
            try:
                send_msg(uid, f"⚠️ <b>Your premium has been revoked.</b>\n\nContact {CONTACT_USERNAME}.")
            except Exception: pass

        # ── ADMIN: /stats ──
        @self.bot.on(events.NewMessage(pattern='/stats'))
        async def h_stats(event):
            if event.sender_id not in ADMIN_IDS: return
            s = self.db.get_stats()
            await event.reply(
                f"📊 <b>Uzeron ReplyBot Stats</b>\n\n"
                f"👥 Total Users: {s['total']}\n"
                f"💎 Active Subscriptions: {s['active_subs']}\n"
                f"🔗 Connected Accounts: {s['connected']}\n"
                f"📩 Total Leads: {s['leads']}", parse_mode='html')

        # ── ADMIN: /broadcast ──
        @self.bot.on(events.NewMessage(pattern=r'/broadcast\s+(.+)'))
        async def h_broadcast(event):
            if event.sender_id not in ADMIN_IDS: return
            msg = event.pattern_match.group(1).strip()
            ids = self.db.get_all_premium_ids()
            sent = failed = 0
            for uid in ids:
                try:
                    send_msg(uid, f"📢 <b>Broadcast:</b>\n\n{msg}")
                    sent += 1
                    await asyncio.sleep(0.1)
                except Exception:
                    failed += 1
            await event.reply(f"📢 Done! ✅ {sent} ❌ {failed}", parse_mode='html')

        # ── CALLBACK QUERIES ──
        @self.bot.on(events.CallbackQuery())
        async def h_cb(event):
            uid  = event.sender_id
            data = event.data.decode()
            mid  = event.query.msg_id

            # ── OTP numpad — handle first, before any join/premium checks ──
            if data.startswith('otp_'):
                await event.answer()
                await self._handle_numpad(uid, mid, data, 'otp')
                return

            await event.answer()

            # ── Channel join check ──
            if data == 'check_join':
                self._join_cache.pop(uid, None)
                all_joined, missing = self._check_join(uid)
                if all_joined:
                    if not self.db.is_premium(uid):
                        edit_msg(uid, mid, welcome_text(), welcome_keyboard())
                    else:
                        user = self.db.get_user(uid)
                        edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))
                else:
                    edit_msg(uid, mid, join_gate_text(missing), join_gate_keyboard())
                return

            all_joined, missing = self._check_join(uid)
            if not all_joined:
                edit_msg(uid, mid, join_gate_text(missing), join_gate_keyboard()); return

            # ── Welcome screen ──
            if data == 'activate_premium':
                edit_msg(uid, mid,
                    "🎟️ <b>Activate Premium</b>\n\n"
                    "Send your redeem code:\n<code>/redeem YOUR_CODE</code>",
                    back_keyboard()); return

            if data == 'get_premium':
                edit_msg(uid, mid,
                    f"💰 <b>Get Premium</b>\n\n"
                    "🥉 Starter — 7 Days\n🥈 Growth — 15 Days\n🥇 Pro — 30 Days\n\n"
                    f"👤 Contact: <b>{CONTACT_USERNAME}</b>",
                    kb([[{"text": f"💬 {CONTACT_USERNAME}",
                          "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}],
                        [{"text": "🔙 Back", "callback_data": "dashboard"}]])); return

            # ── Premium required for all below ──
            if not self.db.is_premium(uid):
                edit_msg(uid, mid, welcome_text(), welcome_keyboard()); return

            user = self.db.get_user(uid)

            if data == 'dashboard':
                edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))

            elif data == 'account':
                edit_msg(uid, mid, account_text(user), account_keyboard())

            elif data == 'status':
                edit_msg(uid, mid, status_text(user), back_keyboard())

            elif data == 'set_pricelist':
                cur = user.get('price_list') or 'Not set'
                self.pending_input[uid] = 'price_list'
                edit_msg(uid, mid,
                    f"📋 <b>Set Price List</b>\n\n"
                    f"<b>Current:</b>\n<code>{cur[:300]}</code>\n\n"
                    "Send your new price list (multi-line supported):\n\n"
                    "<i>Example:</i>\n"
                    "<code>🎨 Logo Design — ₹2,500\n📱 Social Media Post — ₹500</code>\n\n"
                    "<i>/cancel to go back</i>",
                    kb([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

            elif data == 'set_business':
                cur = user.get('business_name') or 'Not set'
                self.pending_input[uid] = 'business_name'
                edit_msg(uid, mid,
                    f"🏪 <b>Business Name</b>\n\nCurrent: {cur}\n\nSend new name:\n<i>/cancel to go back</i>",
                    kb([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

            elif data == 'set_greeting':
                cur = user.get('greeting_msg') or f'Default: {DEFAULT_GREETING}'
                self.pending_input[uid] = 'greeting_msg'
                edit_msg(uid, mid,
                    f"🤖 <b>AI Greeting Message</b>\n\nCurrent: {cur[:200]}\n\n"
                    "Send your custom greeting:\n<i>/cancel to go back</i>",
                    kb([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

            elif data == 'my_leads':
                leads = self.db.get_leads(uid)
                total = user.get('total_leads', 0)
                if not leads:
                    edit_msg(uid, mid,
                        "📩 <b>My Leads</b>\n\nNo leads yet. Make sure bot is ON and account is connected!",
                        back_keyboard())
                else:
                    lines = []
                    for name, uname, msg, ts in leads:
                        u_str = f"@{uname}" if uname else "No username"
                        preview = (msg[:50] + '…') if msg and len(msg) > 50 else (msg or '')
                        lines.append(f"👤 <b>{name or 'Unknown'}</b> ({u_str})\n💬 {preview}\n🕐 {ts}")
                    edit_msg(uid, mid,
                        f"📩 <b>My Leads</b> (Total: {total})\n\n" + "\n\n".join(lines),
                        back_keyboard())

            elif data == 'toggle_bot':
                if not user.get('session_string'):
                    send_msg(uid, "❌ No account connected! Please login first."); return
                new_val = self.db.toggle_auto_reply(uid)
                state = "🟢 ON" if new_val else "🔴 OFF"
                user = self.db.get_user(uid)
                edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(new_val))
                send_msg(uid, f"🤖 Auto-Reply is now <b>{state}</b>")

            elif data == 'login':
                if user.get('session_string'):
                    send_msg(uid,
                        f"✅ Already logged in!\n📱 <code>{user.get('phone','?')}</code>\n\n"
                        "Logout first to switch accounts."); return
                await self._start_login(uid, mid)

            elif data == 'cancel_login':
                await self._cleanup_login(uid)
                user = self.db.get_user(uid)
                edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))

            elif data == 'logout':
                self.db.logout_user(uid)
                user = self.db.get_user(uid)
                edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(0))
                send_msg(uid, "✅ Logged out. Auto-reply paused.")

            elif data == 'subscription':
                edit_msg(uid, mid, subscription_text(user),
                    kb([[{"text": f"🔄 Renew — {CONTACT_USERNAME}",
                          "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}],
                        [{"text": "🔙 Back", "callback_data": "dashboard"}]]))

        # ── TEXT MESSAGES ──
        @self.bot.on(events.NewMessage())
        async def h_text(event):
            uid  = event.sender_id
            text = (event.message.text or '').strip()
            if not text or text.startswith('/'): return

            # Pending settings input
            if uid in self.pending_input:
                await self._handle_pending_input(uid, text); return

            # Login flow: only API and phone steps are text-based
            if uid in self.login_states:
                state = self.login_states[uid]
                if state['step'] == 'api':
                    await self._login_got_api(uid, text)
                elif state['step'] == 'phone':
                    await self._login_got_phone(uid, text)
                elif state['step'] == '2fa':
                    await self._login_got_2fa(uid, text)

    # ============================================================
    # LOGIN FLOW — exact pattern from working free_bot reference
    # ============================================================

    async def _cleanup_login(self, uid):
        if uid in self.login_states:
            try:
                c = self.login_states[uid].get('client')
                if c: await c.disconnect()
            except Exception: pass
            del self.login_states[uid]

    async def _start_login(self, uid, mid):
        """Step 1 — ask for API ID + HASH as text."""
        self.login_states[uid] = {'step': 'api'}
        edit_msg(uid, mid,
            "🔑 <b>Login — Step 1/3: API Credentials</b>\n\n"
            "Send your credentials in <b>one message</b>:\n"
            "<code>API_ID API_HASH</code>\n\n"
            "📌 Get from: <a href='https://my.telegram.org/apps'>my.telegram.org/apps</a>\n"
            "Example: <code>12345678 abc123def456</code>\n\n"
            "<i>/cancel to go back</i>",
            kb([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))

    async def _login_got_api(self, uid, text):
        """Received API_ID API_HASH — save and ask for phone."""
        parts = text.strip().split()
        if len(parts) != 2 or not parts[0].isdigit():
            send_msg(uid,
                "❌ Wrong format.\n\nSend: <code>API_ID API_HASH</code>\n"
                "Example: <code>12345678 abcdef1234567890</code>",
                kb([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]])); return
        self.login_states[uid].update({
            'api_id':   int(parts[0]),
            'api_hash': parts[1],
            'step':     'phone'
        })
        send_msg(uid,
            "✅ <b>API credentials saved!</b>\n\n"
            "🔑 <b>Step 2/3: Phone Number</b>\n\n"
            "📱 Send your phone number with country code:\n"
            "Example: <code>+917239879045</code>\n\n"
            "<i>/cancel to go back</i>",
            kb([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))

    async def _login_got_phone(self, uid, text):
        """Received phone number — send OTP and show numpad."""
        if not text.strip().startswith('+'):
            send_msg(uid, "❌ Must include country code. Example: <code>+917239879045</code>"); return
        state = self.login_states[uid]
        send_msg(uid, "⏳ <b>Sending code to your Telegram…</b>")
        try:
            client = TelegramClient(StringSession(), state['api_id'], state['api_hash'])
            await client.connect()
            sent = await client.send_code_request(text.strip())
            state.update({
                'client':          client,
                'phone':           text.strip(),
                'phone_code_hash': sent.phone_code_hash,
                'step':            'otp',
                'otp_digits':      ''
            })
            r = send_msg(uid,
                "📨 <b>Code sent to your Telegram!</b>\n\n"
                "🔢 <b>Step 3/3: Enter OTP using the buttons below:</b>",
                numpad_keyboard('otp', ''))
            try:
                state['otp_msg_id'] = r['result']['message_id']
            except Exception:
                state['otp_msg_id'] = None
        except FloodWaitError as e:
            send_msg(uid, f"⚠️ FloodWait! Please wait {e.seconds}s and try again.")
            await self._cleanup_login(uid)
        except Exception as e:
            send_msg(uid,
                f"❌ <b>Failed to send code:</b>\n<code>{e}</code>\n\n"
                "Check your API_ID / API_HASH and try again.",
                kb([[{"text": "🔑 Try Again", "callback_data": "login"},
                     {"text": "🏠 Dashboard", "callback_data": "dashboard"}]]))
            await self._cleanup_login(uid)

    async def _handle_numpad(self, uid, mid, data, prefix):
        """Handle every numpad button press — exact clone of free_bot logic."""
        if uid not in self.login_states:
            return
        state  = self.login_states[uid]
        action = data[len(prefix) + 1:]   # e.g. "otp_5" → "5"
        key    = 'otp_digits'
        if key not in state: state[key] = ''

        if action == 'display':
            return  # tapping the display row does nothing
        elif action == 'del':
            state[key] = state[key][:-1]
        elif action == 'submit':
            if not state[key]:
                return
            await self._submit_otp(uid, mid, state[key])
            return
        elif action.isdigit():
            if len(state[key]) < 10:
                state[key] += action
        else:
            return

        digits = state[key]
        try:
            edit_msg(uid, mid,
                f"📨 <b>Enter OTP:</b>\n\nCode so far: <code>{digits or '—'}</code>",
                numpad_keyboard(prefix, digits))
        except Exception:
            pass

    async def _submit_otp(self, uid, mid, code):
        """Submit the OTP and sign in."""
        state = self.login_states.get(uid)
        if not state: return
        try:
            await state['client'].sign_in(
                state['phone'], code,
                phone_code_hash=state['phone_code_hash']
            )
            await self._complete_login(uid, state, mid)
        except SessionPasswordNeededError:
            state['step'] = '2fa'
            edit_msg(uid, mid,
                "🔐 <b>2FA Enabled!</b>\n\n"
                "✍️ Type your 2FA password and send it as a message.\n"
                "<i>/cancel to go back</i>",
                kb([[{"text": "❌ Cancel Login", "callback_data": "cancel_login"}]]))
        except Exception as e:
            edit_msg(uid, mid,
                f"❌ <b>Wrong code:</b> <code>{e}</code>\n\nTap Login again for a fresh code.",
                kb([[{"text": "🔑 Try Again", "callback_data": "login"},
                     {"text": "🏠 Dashboard", "callback_data": "dashboard"}]]))
            await self._cleanup_login(uid)

    async def _login_got_2fa(self, uid, text):
        """Received 2FA password as text message."""
        state = self.login_states.get(uid)
        if not state: return
        try:
            await state['client'].sign_in(password=text.strip())
            await self._complete_login(uid, state, mid=None)
        except Exception as e:
            send_msg(uid,
                f"❌ <b>Wrong 2FA password:</b> <code>{e}</code>\n\nType and send again:",
                kb([[{"text": "❌ Cancel Login", "callback_data": "cancel_login"}]]))

    async def _complete_login(self, uid, state, mid):
        """Save session, notify, show dashboard."""
        try:
            me       = await state['client'].get_me()
            session  = state['client'].session.save()
            phone    = state['phone']
            api_id   = state['api_id']
            api_hash = state['api_hash']

            self.db.save_session(uid, phone, api_id, api_hash, session)
            try: await state['client'].disconnect()
            except Exception: pass
            del self.login_states[uid]

            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            notify = (
                f"✅ <b>Logged in as {full_name}!</b>\n\n"
                f"📱 Account: <code>{phone}</code>\n"
                "🟢 <b>Auto-Reply is now ACTIVE</b>"
            )
            user = self.db.get_user(uid)
            if mid:
                edit_msg(uid, mid, notify, dashboard_keyboard(1))
            else:
                send_msg(uid, notify, dashboard_keyboard(1))

            self.logger.log(
                f"🔑 New account connected\n"
                f"👤 Seller: {uid} (@{user.get('username','N/A')})\n"
                f"📱 Phone: {phone}\n🙍 Name: {full_name}")

        except Exception as e:
            send_msg(uid, f"❌ Login completion error: <code>{e}</code>")
            try: await state['client'].disconnect()
            except Exception: pass
            if uid in self.login_states: del self.login_states[uid]

    # ── PENDING SETTINGS INPUT ──────────────────────────────────
    async def _handle_pending_input(self, uid, text):
        field = self.pending_input.pop(uid)
        self.db.update_field(uid, field, text.strip())
        labels = {
            'price_list':    '📋 Price list saved!',
            'business_name': '🏪 Business name saved!',
            'greeting_msg':  '🤖 Greeting saved!',
        }
        confirm = labels.get(field, '✅ Saved!')
        user = self.db.get_user(uid)
        send_msg(uid, confirm, dashboard_keyboard(user.get('auto_reply', 1)))

# ============================================================
# MAIN
# ============================================================
async def main():
    print("="*55 + "\n  ⚡ UZERON REPLYBOT — Main Bot\n" + "="*55)
    print("✓ All env vars loaded")
    await UzeronReplyBot().start()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚡ Stopped")
    except Exception as e:
        print(f"Fatal: {e}"); sys.exit(1)
