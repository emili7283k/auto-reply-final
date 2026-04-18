# -*- coding: utf-8 -*-
"""
Uzeron ReplyBot — main_bot.py
Dashboard bot: onboarding, channel gate, login flow, settings, admin commands
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
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
)

load_dotenv()

# ============================================================
# ENV VALIDATION — fail fast with clear errors
# ============================================================
REQUIRED_VARS = [
    'API_ID', 'API_HASH', 'MAIN_BOT_TOKEN', 'LOGGER_BOT_TOKEN',
    'ADMIN_IDS', 'DATABASE_URL', 'GROQ_API_KEY',
    'UPDATES_CHANNEL', 'COMMUNITY_GROUP', 'SUPPORT_LINK', 'CONTACT_USERNAME'
]

missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
if missing:
    print(f"❌ FATAL: Missing environment variables: {', '.join(missing)}")
    sys.exit(1)

BOT_API_ID        = int(os.getenv('API_ID'))
BOT_API_HASH      = os.getenv('API_HASH')
MAIN_BOT_TOKEN    = os.getenv('MAIN_BOT_TOKEN')
LOGGER_BOT_TOKEN  = os.getenv('LOGGER_BOT_TOKEN')
ADMIN_IDS         = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
DATABASE_URL      = os.getenv('DATABASE_URL')
UPDATES_CHANNEL   = os.getenv('UPDATES_CHANNEL')   # e.g. @Uzeron_AdsBot
COMMUNITY_GROUP   = os.getenv('COMMUNITY_GROUP')    # e.g. @Uzeron_Ads_support
SUPPORT_LINK      = os.getenv('SUPPORT_LINK')
CONTACT_USERNAME  = os.getenv('CONTACT_USERNAME')   # @Pandaysubscription

IST = pytz.timezone('Asia/Kolkata')

DEFAULT_GREETING = (
    "Hi! 👋 Thanks for reaching out. "
    "The owner is currently unavailable but I'm here to help. "
    "What are you looking for today?"
)

# ============================================================
# KEYBOARD HELPERS
# ============================================================

def make_keyboard(buttons: list) -> dict:
    return {"inline_keyboard": buttons}


def join_gate_keyboard(not_joined: list) -> dict:
    rows = []
    if 'channel' in not_joined:
        rows.append([{"text": "📢 Join Updates Channel", "url": f"https://t.me/{UPDATES_CHANNEL.lstrip('@')}"}])
    if 'group' in not_joined:
        rows.append([{"text": "👥 Join Community", "url": f"https://t.me/{COMMUNITY_GROUP.lstrip('@')}"}])
    rows.append([{"text": "✅ I've Joined — Continue", "callback_data": "check_join"}])
    return make_keyboard(rows)


def welcome_keyboard() -> dict:
    return make_keyboard([
        [{"text": "🎟️ Activate Premium", "callback_data": "activate_premium"},
         {"text": "💰 Get Premium", "callback_data": "get_premium"}]
    ])


def dashboard_keyboard(auto_reply: int = 1) -> dict:
    toggle_text = "🟢 Bot ON — Tap to turn OFF" if auto_reply else "🔴 Bot OFF — Tap to turn ON"
    return make_keyboard([
        [{"text": "👤 My Account",    "callback_data": "account"},
         {"text": "📊 Status",        "callback_data": "status"}],
        [{"text": "📋 Set Price List","callback_data": "set_pricelist"},
         {"text": "🏪 Business Name", "callback_data": "set_business"}],
        [{"text": "🤖 AI Greeting Msg","callback_data": "set_greeting"},
         {"text": "📩 My Leads",      "callback_data": "my_leads"}],
        [{"text": toggle_text,        "callback_data": "toggle_bot"}],
        [{"text": "🔑 Login Account", "callback_data": "login"},
         {"text": "🚪 Logout",        "callback_data": "logout"}],
        [{"text": "💎 Subscription",  "callback_data": "subscription"},
         {"text": "🔔 Updates",       "url": "https://t.me/Uzeron_AdsBot"}],
        [{"text": "❓ How to Use",    "url": "https://t.me/Uzeron_Ads"}]
    ])


def back_keyboard() -> dict:
    return make_keyboard([[{"text": "🏠 Dashboard", "callback_data": "dashboard"}]])


def account_keyboard() -> dict:
    return make_keyboard([
        [{"text": "🔑 Login",  "callback_data": "login"},
         {"text": "🚪 Logout", "callback_data": "logout"}],
        [{"text": "🏠 Dashboard", "callback_data": "dashboard"}]
    ])


# ============================================================
# MESSAGE TEMPLATES
# ============================================================

def join_gate_text(not_joined: list) -> str:
    missing_lines = ""
    if 'channel' in not_joined:
        missing_lines += f"• 📢 Updates Channel\n"
    if 'group' in not_joined:
        missing_lines += f"• 👥 Community Group\n"
    return (
        "👋 <b>Welcome to Uzeron ReplyBot!</b>\n\n"
        "To unlock the bot, please join both:\n\n"
        f"📢 <b>Updates Channel</b> — latest news &amp; updates\n"
        f"👥 <b>Community Group</b> — support &amp; discussion\n\n"
        "<i>After joining both, tap the button below.</i>\n\n"
        f"❌ <b>Still not joined:</b>\n{missing_lines}"
    )


def welcome_text() -> str:
    return (
        "⚡ <b>Welcome to Uzeron ReplyBot!</b>\n\n"
        "╔══════════════════════╗\n"
        "║ 🤖 AI Auto-Reply 24/7\n"
        "║ 💬 Trained on your price list\n"
        "║ 📩 Lead capture & notifications\n"
        "║ 🌐 Replies in buyer's language\n"
        "║ 💰 Never miss a sale again\n"
        "╚══════════════════════╝\n\n"
        "💎 <b>Activate your premium subscription below.</b>\n\n"
        "Don't have a code? Contact us to get one!"
    )


def dashboard_text(user: dict) -> str:
    biz     = user.get('business_name') or "❌ Not set"
    phone   = user.get('phone') or "❌ Not connected"
    pl      = "✅ Set" if user.get('price_list') else "❌ Not set"
    greet   = "✅ Set" if user.get('greeting_msg') else "⚙️ Default"
    status  = "🟢 ON" if user.get('auto_reply') else "🔴 OFF"
    expiry  = user.get('subscription_expiry', '')
    days_left = 0
    if expiry:
        try:
            exp_dt = datetime.strptime(expiry, '%Y-%m-%d')
            days_left = max(0, (exp_dt - datetime.now()).days)
        except Exception:
            days_left = 0
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


def account_text(user: dict) -> str:
    phone   = user.get('phone') or "Not connected"
    conn    = "✅ Connected" if user.get('session_string') else "❌ Not connected"
    leads   = user.get('total_leads', 0)
    expiry  = user.get('subscription_expiry', 'N/A')
    return (
        "👤 <b>My Account</b>\n\n"
        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
        f"🔗 <b>Status:</b> {conn}\n"
        f"📩 <b>Total Leads:</b> {leads}\n"
        f"💎 <b>Subscription Expiry:</b> {expiry}"
    )


def status_text(user: dict) -> str:
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


def subscription_text(user: dict) -> str:
    expiry = user.get('subscription_expiry', 'N/A')
    days_left = 0
    if expiry and expiry != 'N/A':
        try:
            exp_dt = datetime.strptime(expiry, '%Y-%m-%d')
            days_left = max(0, (exp_dt - datetime.now()).days)
        except Exception:
            pass
    return (
        "💎 <b>Subscription Details</b>\n\n"
        f"📅 <b>Expiry Date:</b> {expiry}\n"
        f"⏳ <b>Days Remaining:</b> {days_left}\n\n"
        "To renew your subscription, contact us below."
    )


# ============================================================
# BOT API HELPERS
# ============================================================

def bot_api(method: str, data: dict = None):
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/{method}"
    try:
        processed = {
            k: json.dumps(v) if isinstance(v, (dict, list)) else v
            for k, v in (data or {}).items()
        }
        r = requests.post(url, data=processed, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Bot API error [{method}]: {e}")
        return {}


def send_msg(chat_id: int, text: str, keyboard: dict = None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    if keyboard:
        data["reply_markup"] = keyboard
    bot_api("sendMessage", data)


def edit_msg(chat_id: int, msg_id: int, text: str, keyboard: dict = None):
    data = {"chat_id": chat_id, "message_id": msg_id,
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    if keyboard:
        data["reply_markup"] = keyboard
    bot_api("editMessageText", data)


# ============================================================
# DATABASE
# ============================================================

class Database:
    def get_conn(self):
        return psycopg2.connect(DATABASE_URL, sslmode='require')

    def init_db(self):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS reply_users (
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
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS reply_codes (
                code      TEXT PRIMARY KEY,
                days      INTEGER,
                used      INTEGER DEFAULT 0,
                used_by   BIGINT,
                used_at   TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS reply_leads (
                id                SERIAL PRIMARY KEY,
                seller_id         BIGINT,
                customer_id       BIGINT,
                customer_name     TEXT,
                customer_username TEXT,
                message           TEXT,
                bot_reply         TEXT,
                created_at        TEXT
            )
        ''')
        conn.commit()
        conn.close()
        print("✓ Database tables verified")

    def get_user(self, user_id: int) -> dict | None:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            SELECT user_id, username, phone, api_id, api_hash,
                   session_string, business_name, price_list, greeting_msg,
                   auto_reply, subscription_expiry, total_leads, created_at
            FROM reply_users WHERE user_id=%s
        ''', (user_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        keys = ['user_id','username','phone','api_id','api_hash','session_string',
                'business_name','price_list','greeting_msg','auto_reply',
                'subscription_expiry','total_leads','created_at']
        return dict(zip(keys, row))

    def register_user(self, user_id: int, username: str):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT user_id FROM reply_users WHERE user_id=%s', (user_id,))
        if not c.fetchone():
            c.execute('''
                INSERT INTO reply_users (user_id, username, created_at)
                VALUES (%s, %s, %s)
            ''', (user_id, username, datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
        conn.close()

    def is_premium(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        if not user or not user.get('subscription_expiry'):
            return False
        try:
            exp = datetime.strptime(user['subscription_expiry'], '%Y-%m-%d')
            return exp > datetime.now()
        except Exception:
            return False

    def redeem_code(self, user_id: int, code: str) -> dict:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT code, days, used FROM reply_codes WHERE code=%s', (code,))
        row = c.fetchone()
        if not row:
            conn.close()
            return {'ok': False, 'error': 'Code not found'}
        if row[2]:
            conn.close()
            return {'ok': False, 'error': 'Code already used'}

        days = row[1]
        # Calculate new expiry (extend if already premium)
        user = self.get_user(user_id)
        try:
            current_expiry = datetime.strptime(user['subscription_expiry'], '%Y-%m-%d') \
                if user and user.get('subscription_expiry') else datetime.now()
            if current_expiry < datetime.now():
                current_expiry = datetime.now()
        except Exception:
            current_expiry = datetime.now()

        new_expiry = (current_expiry + timedelta(days=days)).strftime('%Y-%m-%d')

        c.execute('''
            UPDATE reply_codes SET used=1, used_by=%s, used_at=%s WHERE code=%s
        ''', (user_id, datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'), code))
        c.execute('''
            UPDATE reply_users SET subscription_expiry=%s WHERE user_id=%s
        ''', (new_expiry, user_id))
        conn.commit()
        conn.close()
        return {'ok': True, 'days': days, 'expiry': new_expiry}

    def update_field(self, user_id: int, field: str, value):
        allowed = ['business_name','price_list','greeting_msg','auto_reply',
                   'phone','api_id','api_hash','session_string']
        if field not in allowed:
            return
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(f'UPDATE reply_users SET {field}=%s WHERE user_id=%s', (value, user_id))
        conn.commit()
        conn.close()

    def save_session(self, user_id: int, phone: str, api_id: int,
                     api_hash: str, session_string: str):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            UPDATE reply_users
            SET phone=%s, api_id=%s, api_hash=%s, session_string=%s, auto_reply=1
            WHERE user_id=%s
        ''', (phone, api_id, api_hash, session_string, user_id))
        conn.commit()
        conn.close()

    def logout_user(self, user_id: int):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            UPDATE reply_users
            SET phone=NULL, api_id=NULL, api_hash=NULL,
                session_string=NULL, auto_reply=0
            WHERE user_id=%s
        ''', (user_id,))
        conn.commit()
        conn.close()

    def toggle_auto_reply(self, user_id: int) -> int:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT auto_reply FROM reply_users WHERE user_id=%s', (user_id,))
        row = c.fetchone()
        new_val = 0 if (row and row[0]) else 1
        c.execute('UPDATE reply_users SET auto_reply=%s WHERE user_id=%s',
                  (new_val, user_id))
        conn.commit()
        conn.close()
        return new_val

    def get_leads(self, seller_id: int, limit: int = 8) -> list:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            SELECT customer_name, customer_username, message, created_at
            FROM reply_leads WHERE seller_id=%s
            ORDER BY id DESC LIMIT %s
        ''', (seller_id, limit))
        rows = c.fetchall()
        conn.close()
        return rows

    # ── ADMIN methods ──

    def add_code(self, code: str, days: int):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO reply_codes (code, days) VALUES (%s, %s)
                   ON CONFLICT (code) DO NOTHING', (code, days))
        conn.commit()
        conn.close()

    def list_codes(self) -> list:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT code, days, used, used_by FROM reply_codes WHERE used=0')
        rows = c.fetchall()
        conn.close()
        return rows

    def list_premium_users(self) -> list:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            SELECT user_id, username, phone, subscription_expiry
            FROM reply_users
            WHERE subscription_expiry IS NOT NULL
            ORDER BY subscription_expiry DESC
        ''')
        rows = c.fetchall()
        conn.close()
        return rows

    def revoke_premium(self, user_id: int):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            UPDATE reply_users
            SET subscription_expiry=NULL, auto_reply=0
            WHERE user_id=%s
        ''', (user_id,))
        conn.commit()
        conn.close()

    def get_stats(self) -> dict:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM reply_users')
        total = c.fetchone()[0]
        c.execute('''SELECT COUNT(*) FROM reply_users
                     WHERE subscription_expiry > %s''',
                  (datetime.now().strftime('%Y-%m-%d'),))
        active_subs = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM reply_users WHERE session_string IS NOT NULL')
        connected = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM reply_leads')
        leads = c.fetchone()[0]
        conn.close()
        return {
            'total': total,
            'active_subs': active_subs,
            'connected': connected,
            'leads': leads
        }

    def get_all_premium_ids(self) -> list:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''SELECT user_id FROM reply_users
                     WHERE subscription_expiry > %s''',
                  (datetime.now().strftime('%Y-%m-%d'),))
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows]


# ============================================================
# LOGGER
# ============================================================

class Logger:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"

    def log(self, message: str):
        for admin_id in ADMIN_IDS:
            try:
                requests.post(self.url, data={
                    'chat_id': admin_id,
                    'text': message,
                    'parse_mode': 'HTML'
                }, timeout=10)
            except Exception as e:
                print(f"Logger error: {e}")


# ============================================================
# CHANNEL MEMBERSHIP CHECK (via Bot API)
# ============================================================

def check_membership(user_id: int) -> list:
    """Returns list of 'channel' and/or 'group' that user hasn't joined."""
    not_joined = []
    for key, handle in [('channel', UPDATES_CHANNEL), ('group', COMMUNITY_GROUP)]:
        url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/getChatMember"
        try:
            r = requests.post(url, data={
                'chat_id': handle,
                'user_id': user_id
            }, timeout=10).json()
            status = r.get('result', {}).get('status', 'left')
            if status in ('left', 'kicked', 'banned', ''):
                not_joined.append(key)
        except Exception as e:
            print(f"Membership check error [{handle}]: {e}")
            not_joined.append(key)  # Assume not joined on error
    return not_joined


# ============================================================
# MAIN BOT CLASS
# ============================================================

class UzeronReplyBot:
    def __init__(self):
        self.bot = TelegramClient(
            StringSession(), BOT_API_ID, BOT_API_HASH
        )
        self.db = Database()
        self.logger = Logger(LOGGER_BOT_TOKEN)
        # login_states: uid → {step, phone, api_id, api_hash, client}
        self.login_states: dict = {}
        # pending_input: uid → field name ('price_list', 'business_name', 'greeting_msg')
        self.pending_input: dict = {}

    async def start(self):
        self.db.init_db()
        await self.bot.start(bot_token=MAIN_BOT_TOKEN)
        print("✓ Uzeron ReplyBot — main_bot started")
        self._register_handlers()
        print("✓ Handlers registered — bot is live!")
        await self.bot.run_until_disconnected()

    # ──────────────────────────────────────────────────────
    # HANDLER REGISTRATION
    # ──────────────────────────────────────────────────────

    def _register_handlers(self):

        # ── /start ──
        @self.bot.on(events.NewMessage(pattern='/start'))
        async def cmd_start(event):
            uid = event.sender_id
            uname = event.sender.username or ''
            self.db.register_user(uid, uname)
            await self._handle_start(uid)

        # ── /redeem ──
        @self.bot.on(events.NewMessage(pattern=r'/redeem(?:\s+(.+))?'))
        async def cmd_redeem(event):
            uid = event.sender_id
            match = event.pattern_match.group(1)
            if not match:
                send_msg(uid, "❌ Usage: <code>/redeem YOUR_CODE</code>")
                return
            code = match.strip()
            result = self.db.redeem_code(uid, code)
            if result['ok']:
                send_msg(
                    uid,
                    f"✅ <b>Premium Activated!</b>\n\n"
                    f"🎟️ Code: <code>{code}</code>\n"
                    f"📅 Days granted: <b>{result['days']}</b>\n"
                    f"⏳ Expiry: <b>{result['expiry']}</b>\n\n"
                    "Your dashboard is ready!",
                    make_keyboard([[
                        {"text": "⚡ Open Dashboard", "callback_data": "dashboard"}
                    ]])
                )
                self.logger.log(
                    f"✅ New premium activation\n"
                    f"👤 User: {uid} (@{self.db.get_user(uid).get('username','')})\n"
                    f"🎟️ Code: {code} | Days: {result['days']}"
                )
            else:
                send_msg(
                    uid,
                    f"❌ <b>Invalid Code</b>\n\n{result['error']}\n\n"
                    "Need a code? Contact us:",
                    make_keyboard([[
                        {"text": f"💬 Contact {CONTACT_USERNAME}",
                         "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}
                    ]])
                )

        # ── /cancel ──
        @self.bot.on(events.NewMessage(pattern='/cancel'))
        async def cmd_cancel(event):
            uid = event.sender_id
            await self._cancel_state(uid)

        # ── ADMIN: /addcode CODE DAYS ──
        @self.bot.on(events.NewMessage(pattern=r'/addcode\s+(\S+)\s+(\d+)'))
        async def cmd_addcode(event):
            if event.sender_id not in ADMIN_IDS:
                return
            code = event.pattern_match.group(1).strip()
            days = int(event.pattern_match.group(2))
            self.db.add_code(code, days)
            await event.reply(
                f"✅ Code created!\n\n"
                f"🎟️ Code: <code>{code}</code>\n"
                f"📅 Days: <b>{days}</b>",
                parse_mode='html'
            )

        # ── ADMIN: /codes ──
        @self.bot.on(events.NewMessage(pattern='/codes'))
        async def cmd_codes(event):
            if event.sender_id not in ADMIN_IDS:
                return
            codes = self.db.list_codes()
            if not codes:
                await event.reply("📭 No unused codes found.")
                return
            lines = "\n".join(
                f"• <code>{c[0]}</code> — {c[1]} days"
                for c in codes
            )
            await event.reply(
                f"🎟️ <b>Unused Codes ({len(codes)}):</b>\n\n{lines}",
                parse_mode='html'
            )

        # ── ADMIN: /users ──
        @self.bot.on(events.NewMessage(pattern='/users'))
        async def cmd_users(event):
            if event.sender_id not in ADMIN_IDS:
                return
            users = self.db.list_premium_users()
            if not users:
                await event.reply("👥 No premium users yet.")
                return
            lines = "\n".join(
                f"• {uid} (@{uname or 'N/A'}) | {phone or 'No phone'} | Exp: {exp}"
                for uid, uname, phone, exp in users
            )
            await event.reply(
                f"👥 <b>Premium Users ({len(users)}):</b>\n\n{lines}",
                parse_mode='html'
            )

        # ── ADMIN: /revoke USER_ID ──
        @self.bot.on(events.NewMessage(pattern=r'/revoke\s+(\d+)'))
        async def cmd_revoke(event):
            if event.sender_id not in ADMIN_IDS:
                return
            uid = int(event.pattern_match.group(1))
            self.db.revoke_premium(uid)
            await event.reply(f"✅ Premium revoked for user {uid}")
            try:
                send_msg(
                    uid,
                    "⚠️ <b>Your premium subscription has been revoked.</b>\n\n"
                    f"Contact {CONTACT_USERNAME} for more information."
                )
            except Exception:
                pass

        # ── ADMIN: /stats ──
        @self.bot.on(events.NewMessage(pattern='/stats'))
        async def cmd_stats(event):
            if event.sender_id not in ADMIN_IDS:
                return
            s = self.db.get_stats()
            await event.reply(
                f"📊 <b>Uzeron ReplyBot Stats</b>\n\n"
                f"👥 Total Users: {s['total']}\n"
                f"💎 Active Subscriptions: {s['active_subs']}\n"
                f"🔗 Connected Accounts: {s['connected']}\n"
                f"📩 Total Leads: {s['leads']}",
                parse_mode='html'
            )

        # ── ADMIN: /broadcast ──
        @self.bot.on(events.NewMessage(pattern=r'/broadcast\s+(.+)'))
        async def cmd_broadcast(event):
            if event.sender_id not in ADMIN_IDS:
                return
            msg = event.pattern_match.group(1).strip()
            ids = self.db.get_all_premium_ids()
            sent, failed = 0, 0
            for uid in ids:
                try:
                    send_msg(uid, f"📢 <b>Broadcast from Uzeron:</b>\n\n{msg}")
                    sent += 1
                    await asyncio.sleep(0.1)
                except Exception:
                    failed += 1
            await event.reply(
                f"📢 <b>Broadcast complete!</b>\n✅ Sent: {sent}\n❌ Failed: {failed}",
                parse_mode='html'
            )

        # ── CALLBACK QUERIES ──
        @self.bot.on(events.CallbackQuery())
        async def callbacks(event):
            uid = event.sender_id
            data = event.data.decode('utf-8')
            await event.answer()
            mid = event.query.msg_id
            await self._handle_callback(uid, mid, data)

        # ── GLOBAL TEXT HANDLER (settings input + login steps) ──
        @self.bot.on(events.NewMessage())
        async def global_text(event):
            uid = event.sender_id
            text = event.message.text
            if not text or text.startswith('/'):
                return
            # Pending text input for settings
            if uid in self.pending_input:
                await self._handle_pending_input(uid, text)
                return
            # Login flow
            if uid in self.login_states:
                await self._handle_login_step(uid, text)

    # ──────────────────────────────────────────────────────
    # CORE FLOW HANDLERS
    # ──────────────────────────────────────────────────────

    async def _handle_start(self, uid: int):
        """Entry point — channel gate → premium check → dashboard."""
        not_joined = check_membership(uid)
        if not_joined:
            send_msg(uid, join_gate_text(not_joined), join_gate_keyboard(not_joined))
            return

        if not self.db.is_premium(uid):
            send_msg(uid, welcome_text(), welcome_keyboard())
            return

        user = self.db.get_user(uid)
        send_msg(uid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))

    async def _cancel_state(self, uid: int):
        """Clean up any pending state and return to dashboard."""
        if uid in self.login_states:
            try:
                c = self.login_states[uid].get('client')
                if c:
                    await c.disconnect()
            except Exception:
                pass
            del self.login_states[uid]
        if uid in self.pending_input:
            del self.pending_input[uid]
        user = self.db.get_user(uid)
        if user:
            send_msg(uid, dashboard_text(user), dashboard_keyboard(user.get('auto_reply', 1)))
        else:
            await self._handle_start(uid)

    async def _handle_callback(self, uid: int, mid: int, data: str):
        """Route all inline button callbacks."""

        # ── Channel join re-check ──
        if data == 'check_join':
            not_joined = check_membership(uid)
            if not_joined:
                edit_msg(uid, mid,
                         join_gate_text(not_joined) + "\n⚠️ <i>Still not joined! Please join and try again.</i>",
                         join_gate_keyboard(not_joined))
            else:
                if not self.db.is_premium(uid):
                    edit_msg(uid, mid, welcome_text(), welcome_keyboard())
                else:
                    user = self.db.get_user(uid)
                    edit_msg(uid, mid, dashboard_text(user),
                             dashboard_keyboard(user.get('auto_reply', 1)))
            return

        # ── Welcome screen ──
        if data == 'activate_premium':
            edit_msg(uid, mid,
                     "🎟️ <b>Activate Premium</b>\n\n"
                     "Send your redeem code using:\n"
                     "<code>/redeem YOUR_CODE</code>",
                     back_keyboard())
            return

        if data == 'get_premium':
            edit_msg(uid, mid,
                     f"💰 <b>Get Premium</b>\n\n"
                     "📦 <b>Plans Available:</b>\n"
                     "🥉 Starter — 7 Days\n"
                     "🥈 Growth — 15 Days\n"
                     "🥇 Pro — 30 Days\n\n"
                     f"👤 Contact: <b>{CONTACT_USERNAME}</b>",
                     make_keyboard([
                         [{"text": f"💬 {CONTACT_USERNAME}",
                           "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}],
                         [{"text": "🔙 Back", "callback_data": "dashboard"}]
                     ]))
            return

        # ── All below require premium ──
        if not self.db.is_premium(uid):
            await self._handle_start(uid)
            return

        user = self.db.get_user(uid)

        if data == 'dashboard':
            edit_msg(uid, mid, dashboard_text(user),
                     dashboard_keyboard(user.get('auto_reply', 1)))

        elif data == 'account':
            edit_msg(uid, mid, account_text(user), account_keyboard())

        elif data == 'status':
            edit_msg(uid, mid, status_text(user), back_keyboard())

        elif data == 'set_pricelist':
            current = user.get('price_list') or 'Not set'
            self.pending_input[uid] = 'price_list'
            edit_msg(uid, mid,
                     f"📋 <b>Set Price List</b>\n\n"
                     f"<b>Current:</b>\n<code>{current[:300]}</code>\n\n"
                     "Send your new price list (multi-line supported):\n\n"
                     "<i>Example:</i>\n"
                     "<code>🎨 Logo Design — ₹2,500\n"
                     "📱 Social Media Post — ₹500\n"
                     "🌐 Landing Page — ₹8,000</code>\n\n"
                     "<i>Type /cancel to go back.</i>",
                     make_keyboard([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

        elif data == 'set_business':
            current = user.get('business_name') or 'Not set'
            self.pending_input[uid] = 'business_name'
            edit_msg(uid, mid,
                     f"🏪 <b>Business Name</b>\n\n"
                     f"<b>Current:</b> {current}\n\n"
                     "Send your new business name:\n\n"
                     "<i>Type /cancel to go back.</i>",
                     make_keyboard([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

        elif data == 'set_greeting':
            current = user.get('greeting_msg') or f'<i>Default:</i> {DEFAULT_GREETING}'
            self.pending_input[uid] = 'greeting_msg'
            edit_msg(uid, mid,
                     f"🤖 <b>AI Greeting Message</b>\n\n"
                     f"<b>Current:</b> {current[:200]}\n\n"
                     "Send your custom greeting (what AI says when a buyer first messages):\n\n"
                     "<i>Type /cancel to go back.</i>",
                     make_keyboard([[{"text": "❌ Cancel", "callback_data": "dashboard"}]]))

        elif data == 'my_leads':
            leads = self.db.get_leads(uid)
            total = user.get('total_leads', 0)
            if not leads:
                edit_msg(uid, mid,
                         "📩 <b>My Leads</b>\n\nNo leads captured yet.\n"
                         "Make sure your bot is ON and account is connected!",
                         back_keyboard())
            else:
                lines = []
                for name, username, msg, ts in leads:
                    uname_str = f"@{username}" if username else "No username"
                    msg_preview = (msg[:50] + '...') if msg and len(msg) > 50 else (msg or '')
                    lines.append(
                        f"👤 <b>{name or 'Unknown'}</b> ({uname_str})\n"
                        f"💬 {msg_preview}\n"
                        f"🕐 {ts}"
                    )
                leads_text = "\n\n".join(lines)
                edit_msg(uid, mid,
                         f"📩 <b>My Leads</b> (Total: {total})\n\n{leads_text}",
                         back_keyboard())

        elif data == 'toggle_bot':
            if not user.get('session_string'):
                send_msg(uid,
                         "❌ <b>No account connected!</b>\n\n"
                         "Please login first using 🔑 Login Account.")
                return
            new_val = self.db.toggle_auto_reply(uid)
            state_text = "🟢 ON" if new_val else "🔴 OFF"
            user = self.db.get_user(uid)
            edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(new_val))
            send_msg(uid, f"🤖 Auto-Reply is now <b>{state_text}</b>")

        elif data == 'login':
            if user.get('session_string'):
                send_msg(uid,
                         "✅ <b>Already logged in!</b>\n\n"
                         f"📱 Account: <code>{user.get('phone', 'Unknown')}</code>\n\n"
                         "Use 🚪 Logout first to switch accounts.")
                return
            await self._start_login_flow(uid, mid)

        elif data == 'logout':
            self.db.logout_user(uid)
            user = self.db.get_user(uid)
            edit_msg(uid, mid, dashboard_text(user), dashboard_keyboard(0))
            send_msg(uid, "✅ <b>Logged out successfully!</b>\n\nAuto-reply has been paused.")

        elif data == 'subscription':
            edit_msg(uid, mid, subscription_text(user),
                     make_keyboard([
                         [{"text": f"🔄 Renew — {CONTACT_USERNAME}",
                           "url": f"https://t.me/{CONTACT_USERNAME.lstrip('@')}"}],
                         [{"text": "🔙 Back", "callback_data": "dashboard"}]
                     ]))

        elif data == 'cancel_login':
            if uid in self.login_states:
                try:
                    c = self.login_states[uid].get('client')
                    if c:
                        await c.disconnect()
                except Exception:
                    pass
                del self.login_states[uid]
            user = self.db.get_user(uid)
            edit_msg(uid, mid, dashboard_text(user),
                     dashboard_keyboard(user.get('auto_reply', 1)))

    # ──────────────────────────────────────────────────────
    # LOGIN FLOW
    # ──────────────────────────────────────────────────────

    async def _start_login_flow(self, uid: int, mid: int):
        """Step 1 — Ask for API credentials."""
        self.login_states[uid] = {'step': 'waiting_api'}
        edit_msg(uid, mid,
                 "🔑 <b>Login Your Telegram Account</b>\n\n"
                 "<b>Step 1 of 3 — API Credentials</b>\n\n"
                 "You need your own API ID and API Hash from Telegram.\n\n"
                 "📋 <b>How to get them:</b>\n"
                 "1. Go to https://my.telegram.org/apps\n"
                 "2. Login with your phone number\n"
                 "3. Create a new app (any name)\n"
                 "4. Copy your <b>API ID</b> and <b>API Hash</b>\n\n"
                 "✍️ Send them here as:\n"
                 "<code>API_ID API_HASH</code>\n"
                 "(space-separated in one message)\n\n"
                 "<i>Type /cancel to go back.</i>",
                 make_keyboard([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))

    async def _handle_login_step(self, uid: int, text: str):
        state = self.login_states.get(uid)
        if not state:
            return
        step = state.get('step')

        # ── Step 1: API ID + HASH ──
        if step == 'waiting_api':
            parts = text.strip().split()
            if len(parts) != 2 or not parts[0].isdigit():
                send_msg(uid,
                         "❌ Invalid format.\n\n"
                         "Send as: <code>API_ID API_HASH</code>\n"
                         "Example: <code>12345678 abcdef1234567890abcdef</code>",
                         make_keyboard([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))
                return
            state['api_id']   = int(parts[0])
            state['api_hash'] = parts[1]
            state['step']     = 'waiting_phone'
            send_msg(uid,
                     "✅ <b>Credentials saved!</b>\n\n"
                     "<b>Step 2 of 3 — Phone Number</b>\n\n"
                     "Send your Telegram phone number with country code:\n"
                     "Example: <code>+917239879045</code>\n\n"
                     "<i>Type /cancel to go back.</i>",
                     make_keyboard([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))

        # ── Step 2: Phone number — send OTP ──
        elif step == 'waiting_phone':
            if not text.strip().startswith('+'):
                send_msg(uid,
                         "❌ Must start with country code.\n"
                         "Example: <code>+917239879045</code>")
                return
            phone = text.strip()
            try:
                user_client = TelegramClient(
                    StringSession(), state['api_id'], state['api_hash']
                )
                await user_client.connect()
                await user_client.send_code_request(phone)
                state['client'] = user_client
                state['phone']  = phone
                state['step']   = 'waiting_otp'
                send_msg(uid,
                         "📨 <b>OTP Sent!</b>\n\n"
                         "<b>Step 3 of 3 — Verification Code</b>\n\n"
                         "Enter the OTP you received on Telegram:\n"
                         "<i>(Enter digits only, e.g. 12345)</i>\n\n"
                         "<i>Type /cancel to abort.</i>",
                         make_keyboard([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))
            except FloodWaitError as e:
                send_msg(uid,
                         f"⚠️ <b>FloodWait!</b> Please wait {e.seconds} seconds before trying again.")
                del self.login_states[uid]
            except Exception as e:
                send_msg(uid,
                         f"❌ <b>Error sending OTP:</b>\n<code>{e}</code>\n\n"
                         "Check your API credentials and try again.")
                del self.login_states[uid]

        # ── Step 3: OTP ──
        elif step == 'waiting_otp':
            code = text.replace('-', '').replace(' ', '').strip()
            if not code.isdigit():
                send_msg(uid, "❌ Enter only the numeric OTP code.")
                return
            try:
                await state['client'].sign_in(state['phone'], code)
                await self._complete_login(uid, state)
            except SessionPasswordNeededError:
                state['step'] = 'waiting_2fa'
                send_msg(uid,
                         "🔐 <b>2FA Enabled</b>\n\n"
                         "Your account has two-factor authentication.\n"
                         "Send your 2FA password:\n\n"
                         "<i>Type /cancel to abort.</i>",
                         make_keyboard([[{"text": "❌ Cancel", "callback_data": "cancel_login"}]]))
            except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                send_msg(uid,
                         "❌ <b>Invalid or expired OTP.</b>\n\n"
                         "Please use /start and try logging in again.")
                try:
                    await state['client'].disconnect()
                except Exception:
                    pass
                del self.login_states[uid]
            except Exception as e:
                send_msg(uid,
                         f"❌ <b>Sign-in failed:</b>\n<code>{e}</code>")
                try:
                    await state['client'].disconnect()
                except Exception:
                    pass
                del self.login_states[uid]

        # ── Step 3b: 2FA password ──
        elif step == 'waiting_2fa':
            try:
                await state['client'].sign_in(password=text.strip())
                await self._complete_login(uid, state)
            except Exception as e:
                send_msg(uid,
                         f"❌ <b>2FA failed:</b>\n<code>{e}</code>\n\n"
                         "Try again or /cancel.")

    async def _complete_login(self, uid: int, state: dict):
        """Save session, notify seller and admin, show dashboard."""
        try:
            me = await state['client'].get_me()
            session_str = state['client'].session.save()
            phone = state['phone']

            await state['client'].disconnect()

            self.db.save_session(
                uid, phone,
                state['api_id'], state['api_hash'],
                session_str
            )
            if uid in self.login_states:
                del self.login_states[uid]

            user = self.db.get_user(uid)
            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()

            send_msg(uid,
                     f"✅ <b>Logged in as {full_name}!</b>\n\n"
                     f"📱 Account: <code>{phone}</code>\n"
                     "🟢 <b>Auto-Reply is now ACTIVE</b>\n\n"
                     "Your AI assistant is ready to handle buyer messages 24/7!",
                     dashboard_keyboard(1))

            self.logger.log(
                f"🔑 New account connected\n"
                f"👤 Seller: {uid} (@{user.get('username', 'N/A')})\n"
                f"📱 Phone: {phone}\n"
                f"🙍 TG Name: {full_name}"
            )
        except Exception as e:
            send_msg(uid, f"❌ <b>Login completion error:</b>\n<code>{e}</code>")
            try:
                await state['client'].disconnect()
            except Exception:
                pass
            if uid in self.login_states:
                del self.login_states[uid]

    # ──────────────────────────────────────────────────────
    # PENDING TEXT INPUT (settings)
    # ──────────────────────────────────────────────────────

    async def _handle_pending_input(self, uid: int, text: str):
        field = self.pending_input.pop(uid)
        self.db.update_field(uid, field, text.strip())
        labels = {
            'price_list':    ('📋 Price List', '✅ Price list saved!'),
            'business_name': ('🏪 Business Name', '✅ Business name saved!'),
            'greeting_msg':  ('🤖 AI Greeting', '✅ Greeting saved! AI will use this for first messages.'),
        }
        label, confirm_msg = labels.get(field, ('Setting', '✅ Saved!'))
        preview = text[:200] + ('...' if len(text) > 200 else '')
        user = self.db.get_user(uid)
        send_msg(uid,
                 f"{confirm_msg}\n\n"
                 f"<b>{label}:</b>\n<code>{preview}</code>",
                 dashboard_keyboard(user.get('auto_reply', 1)))


# ============================================================
# MAIN
# ============================================================

async def main():
    print("=" * 55)
    print("  ⚡ UZERON REPLYBOT — Main Bot (Dashboard)")
    print("=" * 55)
    print("✓ All environment variables loaded")
    await UzeronReplyBot().start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚡ Main bot stopped")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
