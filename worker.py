# -*- coding: utf-8 -*-
"""
Uzeron ReplyBot — worker.py
Watchdog + Userbot auto-reply engine powered by Groq AI
"""

import os
import sys
import asyncio
import psycopg2
import json
import pytz
import requests
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyError,
    FloodWaitError,
    SessionPasswordNeededError,
)

load_dotenv()

# ============================================================
# ENV VALIDATION
# ============================================================
REQUIRED_VARS = [
    'API_ID', 'API_HASH', 'MAIN_BOT_TOKEN', 'LOGGER_BOT_TOKEN',
    'ADMIN_IDS', 'DATABASE_URL', 'GROQ_API_KEY'
]
missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
if missing:
    print(f"❌ FATAL: Missing environment variables: {', '.join(missing)}")
    sys.exit(1)

BOT_API_ID       = int(os.getenv('API_ID'))
BOT_API_HASH     = os.getenv('API_HASH')
MAIN_BOT_TOKEN   = os.getenv('MAIN_BOT_TOKEN')
LOGGER_BOT_TOKEN = os.getenv('LOGGER_BOT_TOKEN')
ADMIN_IDS        = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
DATABASE_URL     = os.getenv('DATABASE_URL')
GROQ_API_KEY     = os.getenv('GROQ_API_KEY')

IST = pytz.timezone('Asia/Kolkata')

# ── Groq model fallback chain ──
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_GREETING = (
    "Hi! 👋 Thanks for reaching out. "
    "The owner is currently unavailable but I'm here to help. "
    "What are you looking for today?"
)

MAX_HISTORY   = 10     # Keep last 10 turns per conversation
WATCHDOG_INTERVAL = 30  # Seconds between watchdog polls
KEEPALIVE_INTERVAL = 240  # 4 minutes


# ============================================================
# DATABASE (read-only helpers for worker)
# ============================================================

class Database:
    def get_conn(self):
        return psycopg2.connect(DATABASE_URL, sslmode='require')

    def get_active_sellers(self) -> list:
        """Return all premium sellers with a session string and auto_reply ON."""
        conn = self.get_conn()
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute('''
            SELECT user_id, api_id, api_hash, session_string,
                   business_name, price_list, greeting_msg, auto_reply,
                   subscription_expiry, phone
            FROM reply_users
            WHERE session_string IS NOT NULL
              AND auto_reply = 1
              AND subscription_expiry > %s
        ''', (today,))
        rows = c.fetchall()
        conn.close()
        keys = ['user_id','api_id','api_hash','session_string',
                'business_name','price_list','greeting_msg','auto_reply',
                'subscription_expiry','phone']
        return [dict(zip(keys, r)) for r in rows]

    def get_seller(self, user_id: int) -> dict | None:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            SELECT user_id, api_id, api_hash, session_string,
                   business_name, price_list, greeting_msg, auto_reply,
                   subscription_expiry, phone, username
            FROM reply_users WHERE user_id=%s
        ''', (user_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        keys = ['user_id','api_id','api_hash','session_string',
                'business_name','price_list','greeting_msg','auto_reply',
                'subscription_expiry','phone','username']
        return dict(zip(keys, row))

    def is_subscription_valid(self, user_id: int) -> bool:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT subscription_expiry FROM reply_users WHERE user_id=%s',
                  (user_id,))
        row = c.fetchone()
        conn.close()
        if not row or not row[0]:
            return False
        try:
            return datetime.strptime(row[0], '%Y-%m-%d') > datetime.now()
        except Exception:
            return False

    def is_auto_reply_on(self, user_id: int) -> bool:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT auto_reply FROM reply_users WHERE user_id=%s', (user_id,))
        row = c.fetchone()
        conn.close()
        return bool(row and row[0])

    def save_lead(self, seller_id: int, customer_id: int, customer_name: str,
                  customer_username: str, message: str, bot_reply: str):
        conn = self.get_conn()
        c = conn.cursor()
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''
            INSERT INTO reply_leads
            (seller_id, customer_id, customer_name, customer_username,
             message, bot_reply, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (seller_id, customer_id, customer_name, customer_username,
              message, bot_reply, ts))
        c.execute('''
            UPDATE reply_users SET total_leads = COALESCE(total_leads, 0) + 1
            WHERE user_id=%s
        ''', (seller_id,))
        conn.commit()
        conn.close()


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


def notify_seller(seller_id: int, text: str):
    """Send a notification to seller via main bot."""
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            'chat_id': seller_id,
            'text': text,
            'parse_mode': 'HTML'
        }, timeout=10)
    except Exception as e:
        print(f"Seller notify error [{seller_id}]: {e}")


# ============================================================
# GROQ AI
# ============================================================

def call_groq(messages: list, business_name: str) -> str | None:
    """Try each model in the fallback chain. Return reply text or None."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    for model in GROQ_MODELS:
        try:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": 300,
                "temperature": 0.7
            }
            r = requests.post(GROQ_URL, headers=headers,
                              json=payload, timeout=20)
            data = r.json()
            if 'choices' in data and data['choices']:
                reply = data['choices'][0]['message']['content'].strip()
                print(f"  [Groq/{model}] ✓ reply generated")
                return reply
        except Exception as e:
            print(f"  [Groq/{model}] ✗ {e}")
            continue
    return None


def build_system_prompt(seller: dict) -> str:
    biz_name  = seller.get('business_name') or 'this business'
    greeting  = seller.get('greeting_msg') or DEFAULT_GREETING
    price_list = seller.get('price_list') or 'Price list not available — ask the owner for details.'
    return (
        f"You are a professional sales assistant for {biz_name}. "
        "The owner is currently unavailable. You handle all customer inquiries.\n\n"
        f"FIRST MESSAGE GREETING:\n{greeting}\n\n"
        f"YOUR PRICE LIST (ONLY quote these prices, never invent):\n{price_list}\n\n"
        "YOUR BEHAVIOUR:\n"
        "- Be warm, friendly, and professional\n"
        "- Answer pricing questions using ONLY the price list above\n"
        "- For warranty/returns: be reassuring and positive\n"
        "- If customer hesitates on price: highlight quality and value, offer to help them choose\n"
        "- If something is not in the price list: say 'I'll have the owner follow up on that'\n"
        "- Never reveal you are AI unless directly asked\n"
        "- Reply in the same language the customer uses\n"
        "- Keep replies concise — max 80 words\n"
        "- Use 1-2 emojis naturally"
    )


# ============================================================
# WORKER — per-seller userbot runner
# ============================================================

class SellerWorker:
    def __init__(self, seller: dict, db: Database, logger: Logger):
        self.seller      = seller
        self.seller_id   = seller['user_id']
        self.db          = db
        self.logger      = logger
        self.client: TelegramClient | None = None
        # chat_histories: (seller_id, customer_id) → list of {role, content}
        self.chat_histories: dict = {}

    def _get_history(self, customer_id: int) -> list:
        key = (self.seller_id, customer_id)
        return self.chat_histories.get(key, [])

    def _save_history(self, customer_id: int, history: list):
        key = (self.seller_id, customer_id)
        # Cap at MAX_HISTORY * 2 messages (10 turns = 20 messages)
        if len(history) > MAX_HISTORY * 2:
            history = history[-(MAX_HISTORY * 2):]
        self.chat_histories[key] = history

    async def run(self):
        seller_id = self.seller_id
        phone     = self.seller.get('phone', 'unknown')
        retry_count = 0
        max_retries = 10

        while retry_count < max_retries:
            try:
                print(f"[Worker {seller_id}] Starting client for {phone}")
                self.client = TelegramClient(
                    StringSession(self.seller['session_string']),
                    self.seller['api_id'],
                    self.seller['api_hash']
                )
                await self.client.connect()

                if not await self.client.is_user_authorized():
                    print(f"[Worker {seller_id}] Session invalid — stopping")
                    notify_seller(
                        seller_id,
                        "⚠️ <b>Session Expired!</b>\n\n"
                        "Your Telegram session is no longer valid.\n"
                        "Please go to 🔑 Login Account to reconnect."
                    )
                    return

                print(f"[Worker {seller_id}] ✓ Authorized — listening for messages")
                retry_count = 0  # Reset on successful connection

                # ── Register message handler ──
                @self.client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
                async def handle_message(event):
                    await self._on_private_message(event)

                # ── Keep-alive loop (ping every 4 min) ──
                async def keepalive():
                    while True:
                        await asyncio.sleep(KEEPALIVE_INTERVAL)
                        try:
                            await self.client.get_me()
                        except Exception as e:
                            print(f"[Worker {seller_id}] Keepalive error: {e}")
                            break

                await asyncio.gather(
                    self.client.run_until_disconnected(),
                    keepalive()
                )

            except AuthKeyDuplicatedError:
                print(f"[Worker {seller_id}] AuthKeyDuplicated — Railway restart race. Waiting 60s...")
                await asyncio.sleep(60)
                retry_count += 1

            except AuthKeyError:
                print(f"[Worker {seller_id}] AuthKeyError — session permanently invalid. Stopping.")
                notify_seller(
                    seller_id,
                    "❌ <b>Session Invalid!</b>\n\n"
                    "Your Telegram session has become invalid.\n"
                    "Please go to 🔑 Login Account to reconnect."
                )
                return

            except FloodWaitError as e:
                wait = e.seconds
                print(f"[Worker {seller_id}] FloodWait {wait}s")
                await asyncio.sleep(wait)
                retry_count += 1

            except Exception as e:
                backoff = min(60 * (2 ** retry_count), 600)
                print(f"[Worker {seller_id}] Error: {e} — retry {retry_count+1}/{max_retries} in {backoff}s")
                retry_count += 1
                await asyncio.sleep(backoff)

            finally:
                if self.client:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None

        print(f"[Worker {seller_id}] Max retries reached — giving up")

    async def _on_private_message(self, event):
        """Handle an incoming private message on the seller's account."""
        seller_id  = self.seller_id
        customer   = await event.get_sender()
        customer_id = customer.id if customer else None

        if not customer_id:
            return

        # Ignore messages from self
        me = await self.client.get_me()
        if customer_id == me.id:
            return

        # ── Refresh seller state from DB ──
        seller = self.db.get_seller(seller_id)
        if not seller:
            return

        if not seller.get('auto_reply'):
            return

        if not self.db.is_subscription_valid(seller_id):
            print(f"[Worker {seller_id}] Subscription expired — skipping reply")
            return

        msg_text = event.message.text
        if not msg_text or not msg_text.strip():
            return

        customer_name     = f"{getattr(customer, 'first_name', '') or ''} {getattr(customer, 'last_name', '') or ''}".strip() or "Unknown"
        customer_username = getattr(customer, 'username', None)

        print(f"[Worker {seller_id}] Message from {customer_name} (@{customer_username}): {msg_text[:60]}")

        # ── Build conversation history ──
        history = self._get_history(customer_id)
        history.append({"role": "user", "content": msg_text})

        system_prompt = build_system_prompt(seller)
        messages_for_groq = [{"role": "system", "content": system_prompt}] + history

        # ── Call Groq ──
        ai_reply = call_groq(messages_for_groq, seller.get('business_name', ''))

        if not ai_reply:
            # Fallback message
            biz = seller.get('business_name') or 'us'
            ai_reply = (
                f"Hi! Thanks for contacting {biz}. "
                "The owner will be with you shortly! 😊"
            )
            print(f"[Worker {seller_id}] Groq failed — using fallback reply")

        # ── Save to history ──
        history.append({"role": "assistant", "content": ai_reply})
        self._save_history(customer_id, history)

        # ── Send reply ──
        try:
            await event.reply(ai_reply)
        except Exception as e:
            print(f"[Worker {seller_id}] Failed to send reply: {e}")
            return

        # ── Save lead to DB ──
        try:
            self.db.save_lead(
                seller_id, customer_id, customer_name,
                customer_username, msg_text, ai_reply
            )
        except Exception as e:
            print(f"[Worker {seller_id}] Failed to save lead: {e}")

        # ── Notify seller ──
        msg_preview = (msg_text[:100] + '...') if len(msg_text) > 100 else msg_text
        reply_preview = (ai_reply[:100] + '...') if len(ai_reply) > 100 else ai_reply
        uname_str = f"@{customer_username}" if customer_username else "No username"
        notify_seller(
            seller_id,
            f"📩 <b>New Lead!</b>\n\n"
            f"👤 <b>{customer_name}</b> ({uname_str})\n"
            f"💬 <b>Customer:</b> {msg_preview}\n"
            f"🤖 <b>Bot replied:</b> {reply_preview}"
        )


# ============================================================
# WATCHDOG — polls DB, starts/stops workers
# ============================================================

class Watchdog:
    def __init__(self):
        self.db      = Database()
        self.logger  = Logger(LOGGER_BOT_TOKEN)
        # active_workers: seller_id → asyncio.Task
        self.active_workers: dict = {}

    async def run(self):
        print("✓ Watchdog started — polling every 30s")
        while True:
            try:
                await self._tick()
            except Exception as e:
                print(f"[Watchdog] Error in tick: {e}")
            await asyncio.sleep(WATCHDOG_INTERVAL)

    async def _tick(self):
        active_sellers = self.db.get_active_sellers()
        active_ids = {s['user_id'] for s in active_sellers}

        # ── Stop workers for sellers no longer active ──
        to_stop = [sid for sid in self.active_workers if sid not in active_ids]
        for sid in to_stop:
            print(f"[Watchdog] Stopping worker for seller {sid} (inactive/expired)")
            task = self.active_workers.pop(sid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # ── Start workers for new active sellers ──
        for seller in active_sellers:
            sid = seller['user_id']
            if sid not in self.active_workers:
                print(f"[Watchdog] Starting worker for seller {sid}")
                worker = SellerWorker(seller, self.db, self.logger)
                task = asyncio.create_task(worker.run())
                self.active_workers[sid] = task

        if active_sellers:
            print(f"[Watchdog] {len(active_sellers)} active seller(s) | {len(self.active_workers)} worker(s) running")


# ============================================================
# MAIN
# ============================================================

async def main():
    print("=" * 55)
    print("  🤖 UZERON REPLYBOT — Worker (Auto-Reply Engine)")
    print("=" * 55)
    print("✓ All environment variables loaded")
    await Watchdog().run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🤖 Worker stopped")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
