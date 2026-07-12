"""
X For You Bot — Vercel Serverless Webhook Edition
Flask webhook  +  python-telegram-bot  (no polling, no app.run())

All modules merged into one file for Vercel Python serverless compatibility.

Sections
--------
1.  Imports & config
2.  Language strings  (T())
3.  SQLite DB layer   (users, grants, payments, admins, payment_methods, approval_groups, settings)
4.  URL utils + FixupX video detection
5.  Bot state & keyboard builders
6.  Bot handlers
7.  Application factory + handler wiring
8.  Flask app  (POST /webhook)
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════════
# 1.  IMPORTS & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from flask import Flask, request, render_template

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to environment variables.")

# Seed admin IDs (loaded once; managed live in DB). Approval-group management
# is restricted to exactly these three IDs, even if more admins are added later.
_SEED_ADMIN_IDS: set[int] = {8466996343, 6445257462, 8160788482}

FREE_USES: int = 5

# ═══════════════════════════════════════════════════════════════════════════════
# 2.  LANGUAGE STRINGS
# ═══════════════════════════════════════════════════════════════════════════════

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "pick_lang": "🌐 <b>Choose your language</b>\nSelect once — change anytime with /lang",
        "welcome": (
            "👋 <b>Welcome!</b>\n\nSend any <b>X (Twitter)</b> post URL and I'll give you all video download links.\n\n"
            "🎁 Free uses remaining: <b>{remaining}/{free_uses}</b>\n\n"
            "After {free_uses} free uses, tap <b>⭐ Upgrade Unlimited</b> below to unlock unlimited access."
        ),
        "welcome_paid": "✅ <b>You have unlimited access!</b>\n\nSend any X (Twitter) URL for video links.",
        "welcome_grant": "🎁 <b>You have free access</b> granted by an admin!\n\nSend any X (Twitter) URL for video links.",
        "welcome_admin": "👑 <b>Admin Panel</b>\n\nUse the menu below to manage the bot.\nSend any X (Twitter) URL to get video links.",
        "pay_prompt": "⛔ <b>Please upgrade to continue.</b>\n\nYou've used all {free_uses} free conversions. Choose a payment method below to unlock <b>unlimited access</b>:",
        "send_screenshot": "📸 Send your payment screenshot here.",
        "screenshot_received": "✅ <b>Screenshot received!</b>\n\nAn admin will review within <b>5–15 minutes</b>.\nYou'll be notified here when approved.",
        "payment_approved": "🎉 <b>Payment Approved!</b>\n\nYou now have <b>unlimited access</b>.\nSend any X (Twitter) URL to get video links!",
        "payment_rejected": "❌ <b>Payment Rejected.</b>\n\nReason: {reason}\n\nPlease check your payment details and try again, or contact admin.",
        "already_paid": "✅ You already have unlimited access!",
        "admin_only": "⛔ Admin only.",
        "no_video": "❌ No videos found in that post.",
        "error_video": "❌ Error detecting videos: {err}",
        "free_remaining": "\n\n🎁 Free uses remaining: {remaining}/{free_uses}",
        "last_free": "\n\n⚠️ That was your last free use!\nTap ⭐ Upgrade Unlimited to continue.",
        "lang_changed": "✅ Language changed to English.",
        "no_group_set": "⚠️ No approval group set. Ask an original admin to run /addgroup.",
        "no_methods_available": "⚠️ No payment methods are available right now. Please contact an admin.",
        "pm_label_type": "Type",
        "pm_label_name": "Name",
        "pm_label_number": "Number",
        "pm_label_address": "Address",
        "pm_label_blockchain": "Blockchain",
        "pm_label_amount": "Amount",
        "pm_label_link": "Link",
        "adm_pay_review": (
            "💳 <b>New Payment Request</b>\n\n👤 User: <code>{user_id}</code> {uname}\n{fields}\n🕐 {time}"
        ),
        "adm_approved_notify": "✅ Request #{req_id} approved by admin {admin_id}.",
        "adm_rejected_notify": "❌ Request #{req_id} rejected by admin {admin_id}.\nReason: {reason}",
        "adm_no_admins": "No admins found.",
        "adm_admin_list": "👮 <b>Admin IDs</b>\n\n{list}\n\n/addadmin &lt;id&gt;  |  /deladmin &lt;id&gt;",
        "adm_added_admin": "✅ Admin <code>{uid}</code> added.",
        "adm_deleted_admin": "✅ Admin <code>{uid}</code> removed.",
        "adm_cannot_del_self": "❌ You cannot remove yourself.",
        "adm_no_methods": "No payment methods added yet.\n\nUse /addpay to add one.",
        "adm_method_list": "💳 <b>Payment Methods</b>\n\n{list}",
        "adm_del_method_ok": "✅ Payment method #{id} deleted.",
        "adm_del_method_fail": "❌ Method #{id} not found.",
        "adm_addpay_type": "💳 <b>Add Payment Method</b>\n\nStep 1/8: Send the <b>payment type</b> (e.g. Bank Transfer, Crypto, KBZPay). Required.",
        "adm_addpay_name": "Step 2/8: Send a <b>payment name</b> / account holder name (or /skip)",
        "adm_addpay_number": "Step 3/8: Send the <b>payment number</b> / account number (or /skip)",
        "adm_addpay_address": "Step 4/8: Send the <b>payment address</b> (e.g. wallet address) (or /skip)",
        "adm_addpay_blockchain": "Step 5/8: Send the <b>blockchain</b> (e.g. ERC-20, TRC-20) (or /skip)",
        "adm_addpay_amount": "Step 6/8: Send the <b>amount</b> to charge (e.g. $10 USDT). Required.",
        "adm_addpay_qr": "Step 7/8: Send the <b>QR code image</b> (or /skip)",
        "adm_addpay_link": "Step 8/8: Send a <b>payment link</b> (or /skip)",
        "adm_addpay_required": "❌ This field is required and cannot be skipped.",
        "adm_addpay_done": "✅ Payment method <b>{name}</b> added (ID: {id}).",
        "adm_editpay_start": "✏️ Editing method #{id}: <b>{name}</b>\n\nSend new payment type (or /skip to keep):",
        "adm_editpay_done": "✅ Method #{id} updated.",
        "adm_setgroup_ok": "✅ Legacy admin group set to this chat (<code>{gid}</code>). Prefer /addgroup for the new Approval Group system.",
        "adm_give_help": "➕ <b>Give Free Access</b>\n\n/give &lt;user_id&gt; forever\n/give &lt;user_id&gt; month &lt;1-12&gt;\n/give &lt;user_id&gt; year &lt;1-12&gt;",
        "adm_revoke_help": "❌ <b>Revoke Access</b>\n\n/revoke &lt;user_id&gt;\n\nRemoves admin-granted access only.",
        "awaiting_reject_reason": "✏️ Send the <b>rejection reason</b> to notify the user:",
        "reject_cancelled": "❌ Rejection cancelled.",
        "seed_admin_only": "⛔ Approval Group management is restricted to the original admins only.",
        "adm_group_help": (
            "🗂 <b>Approval Group Management</b>\n\n"
            "/addgroup &lt;group_id&gt; &lt;name&gt; — add a group\n"
            "/groups — list groups\n"
            "/togglegroup &lt;id&gt; — enable/disable a group\n"
            "/delgroup &lt;id&gt; — delete a group\n\n"
            "Tip: add the bot to your approval group, then run /addgroup with that group's chat ID."
        ),
        "adm_group_added": "✅ Approval group <b>{name}</b> added (ID: {id}, chat: <code>{gid}</code>).",
        "adm_group_list": "🗂 <b>Approval Groups</b>\n\n{list}",
        "adm_no_groups": "No approval groups added yet.\n\nUse /addgroup to add one.",
        "adm_group_toggled": "✅ Group #{id} is now <b>{status}</b>.",
        "adm_group_not_found": "❌ Group #{id} not found.",
        "adm_group_deleted": "✅ Group #{id} deleted.",
    },
    "my": {
        "pick_lang": "🌐 <b>ဘာသာစကား ရွေးချယ်ပါ</b>\nတစ်ကြိမ်သာ ရွေးရ — /lang ဖြင့် အချိန်မရွေး ပြောင်းနိုင်",
        "welcome": (
            "👋 <b>ကြိုဆိုပါသည်!</b>\n\n<b>X (Twitter)</b> post URL တစ်ခုပို့ပေးပါ၊ ဗီဒီယို link များ ပြန်ပေးပါမည်။\n\n"
            "🎁 အခမဲ့ ကြိမ်ရေ: <b>{remaining}/{free_uses}</b>\n\n"
            "{free_uses} ကြိမ်ပြည့်ပါက အောက်ပါ <b>⭐ Upgrade Unlimited</b> ကိုနှိပ်၍ unlimited access ရယူနိုင်သည်။"
        ),
        "welcome_paid": "✅ <b>Unlimited access ရှိပြီးဖြစ်သည်!</b>\n\nX (Twitter) URL ပို့ပြီး ဗီဒီယို link ရယူနိုင်သည်။",
        "welcome_grant": "🎁 <b>Admin မှ အခမဲ့ access ပေးထားသည်!</b>\n\nX (Twitter) URL ပို့ပြီး ဗီဒီယို link ရယူနိုင်သည်။",
        "welcome_admin": "👑 <b>Admin Panel</b>\n\nအောက်ပါ menu ဖြင့် bot ကို စီမံနိုင်သည်။\nX (Twitter) URL ပို့ပြီး ဗီဒီယို link ရယူနိုင်သည်။",
        "pay_prompt": "⛔ <b>ဆက်လက်အသုံးပြုရန် Upgrade လုပ်ပါ။</b>\n\nအခမဲ့ {free_uses} ကြိမ် ကုန်ဆုံးသွားပါပြီ။ <b>Unlimited access</b> ရရှိရန် ငွေပေးချေမှု နည်းလမ်း ရွေးချယ်ပါ:",
        "send_screenshot": "📸 ငွေပေးချေမှု screenshot ကို ဤနေရာတွင် ပေးပို့ပါ။",
        "screenshot_received": "✅ <b>Screenshot လက်ခံရရှိပါပြီ!</b>\n\nAdmin မှ <b>မိနစ် ၅–၁၅</b> အတွင်း စစ်ဆေးပေးပါမည်။\nApproved ဖြစ်ပါက ဤနေရာမှ အသိပေးပါမည်။",
        "payment_approved": "🎉 <b>ငွေပေးချေမှု အတည်ပြုပြီးပါပြီ!</b>\n\n<b>Unlimited access</b> ရရှိပါပြီ။\nX (Twitter) URL ပို့ပြီး ဗီဒီယို link ရယူနိုင်ပါပြီ!",
        "payment_rejected": "❌ <b>ငွေပေးချေမှု ငြင်းပယ်ခံရသည်။</b>\n\nအကြောင်းပြချက်: {reason}\n\nပြန်စစ်ဆေးပြီး ထပ်မံကြိုးစားပါ သို့မဟုတ် Admin ထံ ဆက်သွယ်ပါ။",
        "already_paid": "✅ Unlimited access ရှိပြီးဖြစ်သည်!",
        "admin_only": "⛔ Admin သာ အသုံးပြုနိုင်သည်။",
        "no_video": "❌ ဤ post တွင် ဗီဒီယို မတွေ့ပါ။",
        "error_video": "❌ ဗီဒီယို ရှာဖွေရာတွင် အမှားရှိသည်: {err}",
        "free_remaining": "\n\n🎁 ကျန်ရှိသည့် အခမဲ့ ကြိမ်ရေ: {remaining}/{free_uses}",
        "last_free": "\n\n⚠️ နောက်ဆုံး အခမဲ့ ကြိမ် ကုန်ဆုံးသွားပါပြီ!\n⭐ Upgrade Unlimited ကို နှိပ်ပါ။",
        "lang_changed": "✅ ဘာသာစကား မြန်မာသို့ ပြောင်းပြီးပါပြီ။",
        "no_group_set": "⚠️ Approval group မသတ်မှတ်ရသေး။",
        "no_methods_available": "⚠️ ငွေပေးချေမှု နည်းလမ်း မရှိသေးပါ။ Admin ကို ဆက်သွယ်ပါ။",
        "pm_label_type": "အမျိုးအစား",
        "pm_label_name": "အမည်",
        "pm_label_number": "နံပါတ်",
        "pm_label_address": "လိပ်စာ",
        "pm_label_blockchain": "Blockchain",
        "pm_label_amount": "ပမာဏ",
        "pm_label_link": "Link",
    },
    "zh": {
        "pick_lang": "🌐 <b>选择语言</b>\n只需选择一次 — 随时可用 /lang 更改",
        "welcome": (
            "👋 <b>欢迎使用！</b>\n\n发送任意 <b>X (Twitter)</b> 帖子链接，我将返回所有视频下载链接。\n\n"
            "🎁 剩余免费次数: <b>{remaining}/{free_uses}</b>\n\n"
            "用完 {free_uses} 次后，点击下方 <b>⭐ Upgrade Unlimited</b> 解锁无限使用。"
        ),
        "welcome_paid": "✅ <b>您已拥有无限使用权限！</b>\n\n发送 X (Twitter) 链接即可获取视频链接。",
        "welcome_grant": "🎁 <b>管理员已授予您免费访问权限！</b>\n\n发送 X (Twitter) 链接即可获取视频链接。",
        "welcome_admin": "👑 <b>管理员面板</b>\n\n使用下方菜单管理机器人。\n发送 X (Twitter) 链接即可获取视频链接。",
        "pay_prompt": "⛔ <b>请升级以继续使用。</b>\n\n您已用完全部 {free_uses} 次免费转换。选择支付方式解锁<b>无限使用</b>：",
        "send_screenshot": "📸 请在此发送您的付款截图。",
        "screenshot_received": "✅ <b>截图已收到！</b>\n\n管理员将在 <b>5–15 分钟内</b>审核您的付款。\n审核通过后将在此通知您。",
        "payment_approved": "🎉 <b>付款已确认！</b>\n\n您现在拥有<b>无限使用权限</b>。\n发送 X (Twitter) 链接即可获取视频链接！",
        "payment_rejected": "❌ <b>付款被拒绝。</b>\n\n原因: {reason}\n\n请检查付款详情后重试，或联系管理员。",
        "already_paid": "✅ 您已拥有无限使用权限！",
        "admin_only": "⛔ 仅限管理员。",
        "no_video": "❌ 该帖子中未找到视频。",
        "error_video": "❌ 检测视频时出错: {err}",
        "free_remaining": "\n\n🎁 剩余免费次数: {remaining}/{free_uses}",
        "last_free": "\n\n⚠️ 这是您最后一次免费使用！\n点击 ⭐ Upgrade Unlimited 继续。",
        "lang_changed": "✅ 语言已切换为中文。",
        "no_group_set": "⚠️ 未设置审批群组。",
        "no_methods_available": "⚠️ 暂无可用的支付方式，请联系管理员。",
        "pm_label_type": "类型",
        "pm_label_name": "名称",
        "pm_label_number": "号码",
        "pm_label_address": "地址",
        "pm_label_blockchain": "区块链",
        "pm_label_amount": "金额",
        "pm_label_link": "链接",
    },
    "th": {
        "pick_lang": "🌐 <b>เลือกภาษาของคุณ</b>\nเลือกครั้งเดียว — เปลี่ยนได้ทุกเมื่อด้วย /lang",
        "welcome": (
            "👋 <b>ยินดีต้อนรับ!</b>\n\nส่งลิงก์โพสต์ <b>X (Twitter)</b> แล้วรับลิงก์ดาวน์โหลดวิดีโอทั้งหมด\n\n"
            "🎁 จำนวนฟรีที่เหลือ: <b>{remaining}/{free_uses}</b>\n\n"
            "หลังใช้ครบ {free_uses} ครั้ง กดปุ่ม <b>⭐ Upgrade Unlimited</b> ด้านล่างเพื่อปลดล็อกการใช้งานไม่จำกัด"
        ),
        "welcome_paid": "✅ <b>คุณมีสิทธิ์ใช้งานไม่จำกัดแล้ว!</b>\n\nส่งลิงก์ X (Twitter) เพื่อรับลิงก์วิดีโอ",
        "welcome_grant": "🎁 <b>แอดมินให้สิทธิ์ใช้งานฟรีแก่คุณ!</b>\n\nส่งลิงก์ X (Twitter) เพื่อรับลิงก์วิดีโอ",
        "welcome_admin": "👑 <b>แผงควบคุมแอดมิน</b>\n\nใช้เมนูด้านล่างเพื่อจัดการบอท\nส่งลิงก์ X (Twitter) เพื่อรับลิงก์วิดีโอ",
        "pay_prompt": "⛔ <b>กรุณาอัปเกรดเพื่อใช้งานต่อ</b>\n\nคุณใช้ครบ {free_uses} ครั้งฟรีแล้ว เลือกวิธีชำระเงินเพื่อปลดล็อก<b>การใช้งานไม่จำกัด</b>:",
        "send_screenshot": "📸 ส่งภาพหน้าจอการชำระเงินของคุณที่นี่",
        "screenshot_received": "✅ <b>ได้รับภาพหน้าจอแล้ว!</b>\n\nแอดมินจะตรวจสอบภายใน <b>5–15 นาที</b>\nคุณจะได้รับแจ้งที่นี่เมื่อได้รับการอนุมัติ",
        "payment_approved": "🎉 <b>ชำระเงินได้รับการอนุมัติ!</b>\n\nคุณมีสิทธิ์<b>ใช้งานไม่จำกัด</b>แล้ว\nส่งลิงก์ X (Twitter) เพื่อรับลิงก์วิดีโอ!",
        "payment_rejected": "❌ <b>การชำระเงินถูกปฏิเสธ</b>\n\nเหตุผล: {reason}\n\nกรุณาตรวจสอบรายละเอียดการชำระเงินแล้วลองใหม่ หรือติดต่อแอดมิน",
        "already_paid": "✅ คุณมีสิทธิ์ใช้งานไม่จำกัดแล้ว!",
        "admin_only": "⛔ สำหรับแอดมินเท่านั้น",
        "no_video": "❌ ไม่พบวิดีโอในโพสต์นั้น",
        "error_video": "❌ เกิดข้อผิดพลาดขณะตรวจหาวิดีโอ: {err}",
        "free_remaining": "\n\n🎁 จำนวนฟรีที่เหลือ: {remaining}/{free_uses}",
        "last_free": "\n\n⚠️ นี่คือการใช้งานฟรีครั้งสุดท้ายของคุณ!\nกด ⭐ Upgrade Unlimited เพื่อดำเนินการต่อ",
        "lang_changed": "✅ เปลี่ยนภาษาเป็นภาษาไทยแล้ว",
        "no_group_set": "⚠️ ยังไม่ได้ตั้งค่ากลุ่มอนุมัติ",
        "no_methods_available": "⚠️ ยังไม่มีวิธีชำระเงิน กรุณาติดต่อแอดมิน",
        "pm_label_type": "ประเภท",
        "pm_label_name": "ชื่อ",
        "pm_label_number": "หมายเลข",
        "pm_label_address": "ที่อยู่",
        "pm_label_blockchain": "บล็อกเชน",
        "pm_label_amount": "จำนวนเงิน",
        "pm_label_link": "ลิงก์",
    },
}


def T(lang: str, key: str, **kwargs) -> str:
    text = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  SQLITE DB LAYER
#     On Vercel the only writable path is /tmp — data is ephemeral per instance.
#     For persistence use a cloud DB (PlanetScale, Neon, Supabase) and swap
#     the sqlite3 calls with your chosen driver.
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH = os.environ.get("DB_PATH", "/tmp/xbot_users.db")

_conn_instance: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn_instance
    if _conn_instance is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                paid        INTEGER DEFAULT 0,
                lang        TEXT    DEFAULT 'en'
            )
        """)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'en'")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS grants (
                user_id     INTEGER PRIMARY KEY,
                grant_type  TEXT    NOT NULL,
                grant_count INTEGER,
                expiry_at   TEXT,
                granted_by  INTEGER NOT NULL,
                granted_at  TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                first_name  TEXT,
                currency    TEXT,
                amount      INTEGER,
                payload     TEXT,
                paid_at     TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_ids (
                user_id  INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TEXT NOT NULL
            )
        """)
        # Unified payment-method table (replaces the old local_payment_methods /
        # Stars / USDT-only setup with a single configurable method type).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_methods (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_type     TEXT NOT NULL,
                payment_name     TEXT,
                payment_number   TEXT,
                payment_address  TEXT,
                blockchain       TEXT,
                amount           TEXT NOT NULL,
                qr_file_id       TEXT,
                payment_link     TEXT,
                active           INTEGER DEFAULT 1,
                created_at       TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_requests (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL,
                username           TEXT,
                first_name         TEXT,
                method_id          INTEGER NOT NULL,
                screenshot_file_id TEXT,
                status             TEXT    DEFAULT 'pending',
                admin_msg_id       INTEGER,
                created_at         TEXT    NOT NULL,
                reviewed_at        TEXT,
                reviewed_by        INTEGER,
                reject_reason      TEXT
            )
        """)
        # Approval groups: chat groups that receive payment-screenshot review
        # requests. Managed only by the three seed admin IDs.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_groups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL,
                group_name TEXT,
                status     TEXT    DEFAULT 'active',
                added_by   INTEGER,
                added_at   TEXT    NOT NULL
            )
        """)
        # De-duplication for Telegram webhook retries (same update_id resent).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_updates (
                update_id    INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

        # Seed admin IDs
        for uid in _SEED_ADMIN_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO admin_ids (user_id, added_by, added_at) VALUES (?, 0, ?)",
                (uid, _now_iso()),
            )
        conn.commit()
        _conn_instance = conn
    return _conn_instance


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Language helpers ──────────────────────────────────────────────────────────

def has_language_set(user_id: int) -> bool:
    return _db().execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


def get_user_lang(user_id: int) -> str:
    row = _db().execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return (row["lang"] or "en") if row else "en"


def set_user_lang(user_id: int, lang: str) -> None:
    db = _db()
    db.execute(
        "INSERT INTO users (user_id,usage_count,paid,lang) VALUES(?,0,0,?) "
        "ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang",
        (user_id, lang),
    )
    db.commit()


# ── Users / quota ─────────────────────────────────────────────────────────────

def get_user(user_id: int) -> dict:
    row = _db().execute(
        "SELECT usage_count,paid,lang FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    return {"usage_count": row["usage_count"], "paid": bool(row["paid"]), "lang": row["lang"] or "en"} if row \
        else {"usage_count": 0, "paid": False, "lang": "en"}


def get_usage_count(user_id: int) -> int:
    row = _db().execute("SELECT usage_count FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row["usage_count"] if row else 0


def try_consume_free_use(user_id: int, free_limit: int) -> bool:
    db = _db()
    db.execute(
        "INSERT OR IGNORE INTO users(user_id,usage_count,paid,lang) VALUES(?,0,0,'en')",
        (user_id,),
    )
    db.commit()
    cur = db.execute(
        "UPDATE users SET usage_count=usage_count+1 WHERE user_id=? AND paid=0 AND usage_count<?",
        (user_id, free_limit),
    )
    db.commit()
    return cur.rowcount == 1


def mark_paid(user_id: int) -> None:
    db = _db()
    db.execute(
        "INSERT INTO users(user_id,usage_count,paid,lang) VALUES(?,0,1,'en') "
        "ON CONFLICT(user_id) DO UPDATE SET paid=1",
        (user_id,),
    )
    db.commit()


def is_paid(user_id: int) -> bool:
    row = _db().execute("SELECT paid FROM users WHERE user_id=?", (user_id,)).fetchone()
    return bool(row["paid"]) if row else False


# ── Admin IDs ─────────────────────────────────────────────────────────────────

def get_admin_ids() -> set[int]:
    return {r["user_id"] for r in _db().execute("SELECT user_id FROM admin_ids").fetchall()}


def add_admin(user_id: int, added_by: int) -> bool:
    db = _db()
    cur = db.execute(
        "INSERT OR IGNORE INTO admin_ids(user_id,added_by,added_at) VALUES(?,?,?)",
        (user_id, added_by, _now_iso()),
    )
    db.commit()
    return cur.rowcount > 0


def remove_admin(user_id: int) -> bool:
    db = _db()
    cur = db.execute("DELETE FROM admin_ids WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def list_admins() -> list[dict]:
    return [dict(r) for r in _db().execute(
        "SELECT user_id,added_by,added_at FROM admin_ids ORDER BY added_at ASC"
    ).fetchall()]


# ── Grants ────────────────────────────────────────────────────────────────────

def has_active_grant(user_id: int) -> bool:
    row = _db().execute(
        "SELECT grant_type,expiry_at FROM grants WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        return False
    if row["grant_type"] == "forever":
        return True
    if row["expiry_at"]:
        return datetime.now(timezone.utc) < datetime.fromisoformat(row["expiry_at"])
    return False


def add_grant(user_id: int, grant_type: str, grant_count: Optional[int], granted_by: int) -> dict:
    now = datetime.now(timezone.utc)
    expiry_at = None
    if grant_type == "month" and grant_count:
        expiry_at = (now + timedelta(days=30 * grant_count)).isoformat()
    elif grant_type == "year" and grant_count:
        expiry_at = (now + timedelta(days=365 * grant_count)).isoformat()
    db = _db()
    db.execute(
        "INSERT INTO grants(user_id,grant_type,grant_count,expiry_at,granted_by,granted_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
        "grant_type=excluded.grant_type,grant_count=excluded.grant_count,"
        "expiry_at=excluded.expiry_at,granted_by=excluded.granted_by,granted_at=excluded.granted_at",
        (user_id, grant_type, grant_count, expiry_at, granted_by, now.isoformat()),
    )
    db.commit()
    return {"expiry_at": expiry_at}


def revoke_grant(user_id: int) -> bool:
    db = _db()
    cur = db.execute("DELETE FROM grants WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def list_grants() -> list[dict]:
    rows = _db().execute(
        "SELECT user_id,grant_type,grant_count,expiry_at,granted_by,granted_at FROM grants ORDER BY granted_at DESC"
    ).fetchall()
    result = []
    now = datetime.now(timezone.utc)
    for row in rows:
        if row["grant_type"] == "forever":
            active, status = True, "forever"
        elif row["expiry_at"]:
            expiry = datetime.fromisoformat(row["expiry_at"])
            active = now < expiry
            status = f"until {expiry.strftime('%Y-%m-%d')} ({'active' if active else 'EXPIRED'})"
        else:
            active, status = False, "unknown"
        result.append({**dict(row), "active": active, "status": status})
    return result


# ── Payment log ───────────────────────────────────────────────────────────────

def log_payment(user_id, username, first_name, currency, amount, payload) -> None:
    db = _db()
    db.execute(
        "INSERT INTO payments(user_id,username,first_name,currency,amount,payload,paid_at) VALUES(?,?,?,?,?,?,?)",
        (user_id, username, first_name, currency, amount, payload, _now_iso()),
    )
    db.commit()


def get_recent_payments(limit: int = 15) -> list[dict]:
    return [dict(r) for r in _db().execute(
        "SELECT user_id,username,first_name,currency,amount,payload,paid_at FROM payments ORDER BY paid_at DESC LIMIT ?",
        (limit,),
    ).fetchall()]


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str) -> Optional[str]:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    db = _db()
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


# ── Unified payment methods ───────────────────────────────────────────────────

PAYMENT_METHOD_FIELDS = (
    "payment_type", "payment_name", "payment_number",
    "payment_address", "blockchain", "amount", "qr_file_id", "payment_link",
)


def add_payment_method(**fields) -> int:
    db = _db()
    cols = ", ".join(PAYMENT_METHOD_FIELDS)
    placeholders = ", ".join("?" for _ in PAYMENT_METHOD_FIELDS)
    values = [fields.get(f) for f in PAYMENT_METHOD_FIELDS]
    cur = db.execute(
        f"INSERT INTO payment_methods({cols},active,created_at) VALUES({placeholders},1,?)",
        (*values, _now_iso()),
    )
    db.commit()
    return cur.lastrowid


def edit_payment_method(method_id: int, **fields) -> bool:
    db = _db()
    row = db.execute(
        f"SELECT {', '.join(PAYMENT_METHOD_FIELDS)} FROM payment_methods WHERE id=?", (method_id,)
    ).fetchone()
    if not row:
        return False
    merged = {f: (fields[f] if fields.get(f) is not None else row[f]) for f in PAYMENT_METHOD_FIELDS}
    set_clause = ", ".join(f"{f}=?" for f in PAYMENT_METHOD_FIELDS)
    db.execute(
        f"UPDATE payment_methods SET {set_clause} WHERE id=?",
        (*[merged[f] for f in PAYMENT_METHOD_FIELDS], method_id),
    )
    db.commit()
    return True


def delete_payment_method(method_id: int) -> bool:
    db = _db()
    cur = db.execute(
        "UPDATE payment_methods SET active=0 WHERE id=? AND active=1", (method_id,)
    )
    db.commit()
    return cur.rowcount > 0


def get_payment_method(method_id: int) -> Optional[dict]:
    row = _db().execute(
        f"SELECT id, {', '.join(PAYMENT_METHOD_FIELDS)}, active FROM payment_methods WHERE id=?",
        (method_id,),
    ).fetchone()
    return dict(row) if row else None


def list_payment_methods(active_only=True) -> list[dict]:
    q = f"SELECT id, {', '.join(PAYMENT_METHOD_FIELDS)}, active FROM payment_methods"
    q += " WHERE active=1" if active_only else ""
    q += " ORDER BY id ASC"
    return [dict(r) for r in _db().execute(q).fetchall()]


# ── Payment requests ──────────────────────────────────────────────────────────

def create_payment_request(user_id, username, first_name, method_id) -> int:
    db = _db()
    cur = db.execute(
        "INSERT INTO payment_requests(user_id,username,first_name,method_id,status,created_at) VALUES(?,?,?,?,'pending',?)",
        (user_id, username, first_name, method_id, _now_iso()),
    )
    db.commit()
    return cur.lastrowid


def attach_screenshot(req_id, screenshot_file_id, admin_msg_id=None) -> None:
    db = _db()
    db.execute(
        "UPDATE payment_requests SET screenshot_file_id=?,admin_msg_id=? WHERE id=?",
        (screenshot_file_id, admin_msg_id, req_id),
    )
    db.commit()


def get_payment_request(req_id: int) -> Optional[dict]:
    row = _db().execute("SELECT * FROM payment_requests WHERE id=?", (req_id,)).fetchone()
    return dict(row) if row else None


def approve_payment_request(req_id, reviewed_by) -> Optional[dict]:
    db = _db()
    cur = db.execute(
        "UPDATE payment_requests SET status='approved',reviewed_by=?,reviewed_at=? WHERE id=? AND status='pending'",
        (reviewed_by, _now_iso(), req_id),
    )
    db.commit()
    return get_payment_request(req_id) if cur.rowcount else None


def reject_payment_request(req_id, reviewed_by, reason) -> Optional[dict]:
    db = _db()
    cur = db.execute(
        "UPDATE payment_requests SET status='rejected',reviewed_by=?,reviewed_at=?,reject_reason=? WHERE id=? AND status='pending'",
        (reviewed_by, _now_iso(), reason, req_id),
    )
    db.commit()
    return get_payment_request(req_id) if cur.rowcount else None


# ── Approval groups (restricted to the 3 seed admins) ─────────────────────────

def add_approval_group(group_id: int, group_name: str, added_by: int) -> int:
    db = _db()
    cur = db.execute(
        "INSERT INTO approval_groups(group_id,group_name,status,added_by,added_at) VALUES(?,?,?,?,?)",
        (group_id, group_name, "active", added_by, _now_iso()),
    )
    db.commit()
    return cur.lastrowid


def list_approval_groups() -> list[dict]:
    return [dict(r) for r in _db().execute(
        "SELECT id,group_id,group_name,status FROM approval_groups ORDER BY id ASC"
    ).fetchall()]


def get_approval_group(gid: int) -> Optional[dict]:
    row = _db().execute(
        "SELECT id,group_id,group_name,status FROM approval_groups WHERE id=?", (gid,)
    ).fetchone()
    return dict(row) if row else None


def toggle_approval_group(gid: int) -> Optional[str]:
    db = _db()
    row = db.execute("SELECT status FROM approval_groups WHERE id=?", (gid,)).fetchone()
    if not row:
        return None
    new_status = "disabled" if row["status"] == "active" else "active"
    db.execute("UPDATE approval_groups SET status=? WHERE id=?", (new_status, gid))
    db.commit()
    return new_status


def delete_approval_group(gid: int) -> bool:
    db = _db()
    cur = db.execute("DELETE FROM approval_groups WHERE id=?", (gid,))
    db.commit()
    return cur.rowcount > 0


def get_active_approval_group_chat_id() -> Optional[int]:
    row = _db().execute(
        "SELECT group_id FROM approval_groups WHERE status='active' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return row["group_id"] if row else None


# ── Webhook de-duplication ────────────────────────────────────────────────────

def mark_update_processed(update_id: int) -> bool:
    """Returns True if this update_id was newly recorded (not a duplicate)."""
    db = _db()
    cur = db.execute(
        "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES(?,?)",
        (update_id, _now_iso()),
    )
    db.commit()
    return cur.rowcount > 0


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_bot_stats() -> dict:
    db = _db()
    now_dt = datetime.now(timezone.utc)
    total_users       = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    paid_users        = db.execute("SELECT COUNT(*) FROM users WHERE paid=1").fetchone()[0]
    total_payments    = db.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    pending_requests  = db.execute("SELECT COUNT(*) FROM payment_requests WHERE status='pending'").fetchone()[0]
    grants = db.execute("SELECT grant_type,expiry_at FROM grants").fetchall()
    active_grants = expired_grants = 0
    for g in grants:
        if g["grant_type"] == "forever":
            active_grants += 1
        elif g["expiry_at"]:
            if datetime.fromisoformat(g["expiry_at"]) > now_dt:
                active_grants += 1
            else:
                expired_grants += 1
    return {
        "total_users": total_users, "paid_users": paid_users,
        "active_grants": active_grants, "expired_grants": expired_grants,
        "total_payments": total_payments, "pending_requests": pending_requests,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  URL UTILS + FIXUPX VIDEO DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/\S+/status/\d+",
    re.IGNORECASE,
)
_OG_VIDEO_RE = re.compile(rb'og:video" content="([^"]+)"')
_HEADERS  = {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}
_TIMEOUT  = httpx.Timeout(10.0)
MAX_VIDEOS = 20


def extract_tweet_url(text: str) -> Optional[str]:
    m = _STATUS_RE.search(text)
    return m.group(0) if m else None


def to_fixupx(url: str) -> str:
    return re.sub(
        r"https?://(?:www\.)?(?:x\.com|twitter\.com)", "https://fixupx.com",
        url, count=1, flags=re.IGNORECASE,
    ).split("?")[0]


async def _get_video_url(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, headers=_HEADERS)
        if resp.status_code != 200:
            return None
        m = _OG_VIDEO_RE.search(resp.content)
        return m.group(1).decode() if m else None
    except Exception as exc:
        logger.debug("Probe failed %s: %s", url, exc)
        return None


async def count_videos(base_url: str) -> int:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        first = await _get_video_url(client, f"{base_url}/video/1")
        if not first:
            return 0
        index = 2
        while index <= MAX_VIDEOS:
            url = await _get_video_url(client, f"{base_url}/video/{index}")
            if not url or url == first:
                break
            index += 1
    return index - 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  BOT STATE & KEYBOARD BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory conversation state (ephemeral per serverless instance)
admin_states: dict[int, dict] = {}
user_states:  dict[int, dict] = {}

BTN_GRANTS      = "📋 Grants"
BTN_PAYMENTS    = "💰 Payments"
BTN_GIVE        = "➕ Give Access"
BTN_REVOKE      = "❌ Revoke"
BTN_STATS       = "📊 Stats"
BTN_ADMINS      = "👮 Admins"
BTN_PAY_METHODS = "💳 Pay Methods"
BTN_GROUPS      = "🗂 Approval Groups"

ADMIN_BUTTONS = {BTN_GRANTS, BTN_PAYMENTS, BTN_GIVE, BTN_REVOKE,
                 BTN_STATS, BTN_ADMINS, BTN_PAY_METHODS, BTN_GROUPS}

ADMIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_GRANTS),   KeyboardButton(BTN_PAYMENTS)],
        [KeyboardButton(BTN_GIVE),     KeyboardButton(BTN_REVOKE)],
        [KeyboardButton(BTN_STATS)],
        [KeyboardButton(BTN_ADMINS),   KeyboardButton(BTN_PAY_METHODS)],
        [KeyboardButton(BTN_GROUPS)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Persistent menu buttons for regular (non-admin) users, always visible near
# the input bar.
BTN_LANGUAGE = "🌐 Language"
BTN_UPGRADE  = "⭐ Upgrade Unlimited"
USER_BUTTONS = {BTN_LANGUAGE, BTN_UPGRADE}

USER_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_LANGUAGE), KeyboardButton(BTN_UPGRADE)]],
    resize_keyboard=True,
    is_persistent=True,
)

LANG_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🇬🇧 English",  callback_data="set_lang:en"),
        InlineKeyboardButton("🇲🇲 မြန်မာ",   callback_data="set_lang:my"),
    ],
    [
        InlineKeyboardButton("🇨🇳 中文",     callback_data="set_lang:zh"),
        InlineKeyboardButton("🇹🇭 ภาษาไทย", callback_data="set_lang:th"),
    ],
])


def _is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()


def _is_seed_admin(user_id: int) -> bool:
    return user_id in _SEED_ADMIN_IDS


def _lang(user_id: int) -> str:
    return get_user_lang(user_id) or "en"


def _has_unlimited(user_id: int) -> bool:
    return _is_admin(user_id) or is_paid(user_id) or has_active_grant(user_id)


def _admin_group() -> Optional[int]:
    """Legacy single-group setting from /setgroup, used only as a fallback."""
    val = get_setting("admin_group_id")
    return int(val) if val else None


def _approval_targets() -> list[int]:
    """Where to send payment-review requests: active approval group first,
    then the legacy /setgroup chat, then all admin DMs as a last resort."""
    active = get_active_approval_group_chat_id()
    if active:
        return [active]
    legacy = _admin_group()
    if legacy:
        return [legacy]
    return list(get_admin_ids())


def _fmt_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _method_label(m: dict) -> str:
    label = m["payment_type"]
    if m.get("payment_name"):
        label += f" — {m['payment_name']}"
    if m.get("amount"):
        label += f" ({m['amount']})"
    return label


def _build_payment_kb(lang: str) -> InlineKeyboardMarkup:
    methods = list_payment_methods(active_only=True)
    rows = [[InlineKeyboardButton(_method_label(m), callback_data=f"pay_method:{m['id']}")] for m in methods]
    return InlineKeyboardMarkup(rows)


def _format_payment_info(method: dict, lang: str) -> str:
    """Render only the non-empty fields of a payment method."""
    field_to_label = [
        ("payment_type",    "pm_label_type"),
        ("payment_name",    "pm_label_name"),
        ("payment_number",  "pm_label_number"),
        ("payment_address", "pm_label_address"),
        ("blockchain",      "pm_label_blockchain"),
        ("amount",          "pm_label_amount"),
        ("payment_link",    "pm_label_link"),
    ]
    lines = []
    for field, label_key in field_to_label:
        value = method.get(field)
        if value:
            lines.append(f"<b>{T(lang, label_key)}:</b> {value}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  BOT HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

# ── /start ────────────────────────────────────────────────────────────────────

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    if _is_admin(user_id):
        await update.message.reply_text(T("en", "welcome_admin"), parse_mode=ParseMode.HTML, reply_markup=ADMIN_KB)
        return
    if not has_language_set(user_id):
        await update.message.reply_text(
            "🌐 <b>Choose your language / ဘာသာစကား ရွေးပါ / 选择语言 / เลือกภาษา</b>",
            parse_mode=ParseMode.HTML, reply_markup=LANG_KB,
        )
        return
    await _send_welcome(update.message.reply_text, user_id)


async def _send_welcome(reply_fn, user_id: int) -> None:
    lang = _lang(user_id)
    if has_active_grant(user_id):
        await reply_fn(T(lang, "welcome_grant"), parse_mode=ParseMode.HTML, reply_markup=USER_KB)
        return
    u = get_user(user_id)
    if u["paid"]:
        await reply_fn(T(lang, "welcome_paid"), parse_mode=ParseMode.HTML, reply_markup=USER_KB)
        return
    remaining = max(0, FREE_USES - u["usage_count"])
    await reply_fn(
        T(lang, "welcome", remaining=remaining, free_uses=FREE_USES),
        parse_mode=ParseMode.HTML, reply_markup=USER_KB,
    )


# ── /lang ─────────────────────────────────────────────────────────────────────

async def handle_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_admin(update.message.from_user.id):
        await update.message.reply_text("Admins always use English for the admin panel.")
        return
    await update.message.reply_text(
        "🌐 <b>Choose your language / ဘာသာစကား ရွေးပါ / 选择语言 / เลือกภาษา</b>",
        parse_mode=ParseMode.HTML, reply_markup=LANG_KB,
    )


# ── /pay ──────────────────────────────────────────────────────────────────────

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    lang    = _lang(user_id)
    if _is_admin(user_id):
        await update.message.reply_text("👑 You're an admin — no payment needed!")
        return
    if _has_unlimited(user_id):
        await update.message.reply_text(T(lang, "already_paid"), parse_mode=ParseMode.HTML)
        return
    methods = list_payment_methods(active_only=True)
    if not methods:
        await update.message.reply_text(T(lang, "no_methods_available"), parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        T(lang, "pay_prompt", free_uses=FREE_USES),
        parse_mode=ParseMode.HTML, reply_markup=_build_payment_kb(lang),
    )


# ── Callback query dispatcher ─────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    await query.answer()
    data    = query.data or ""
    user_id = query.from_user.id
    lang    = _lang(user_id)

    if data.startswith("set_lang:"):
        chosen = data.split(":", 1)[1]
        if chosen not in ("en", "my", "zh", "th"):
            return
        set_user_lang(user_id, chosen)
        lang = chosen
        try:
            await query.message.delete()
        except Exception:
            pass
        await _send_welcome(query.message.reply_text, user_id)
        await query.message.reply_text(T(lang, "lang_changed"), parse_mode=ParseMode.HTML)
        return

    if data.startswith("pay_method:"):
        try:
            method_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if _has_unlimited(user_id):
            await query.message.reply_text(T(lang, "already_paid"), parse_mode=ParseMode.HTML)
            return
        await _handle_payment_method_select(query, user_id, lang, method_id)
        return

    if data.startswith("approve_pay:"):
        if not _is_admin(user_id):
            return
        req_id = int(data.split(":", 1)[1])
        await _do_approve(query, context, user_id, req_id)
        return

    if data.startswith("reject_pay:"):
        if not _is_admin(user_id):
            return
        req_id = int(data.split(":", 1)[1])
        admin_states[user_id] = {"action": "reject", "req_id": req_id}
        await query.message.reply_text(T("en", "awaiting_reject_reason"), parse_mode=ParseMode.HTML)
        return

    if data.startswith("edit_method:"):
        if not _is_admin(user_id):
            return
        method_id = int(data.split(":", 1)[1])
        await _start_edit_pay(query.message.reply_text, user_id, method_id)
        return

    if data.startswith("del_method:"):
        if not _is_admin(user_id):
            return
        method_id = int(data.split(":", 1)[1])
        ok = delete_payment_method(method_id)
        await query.message.reply_text(
            T("en", "adm_del_method_ok", id=method_id) if ok else T("en", "adm_del_method_fail", id=method_id),
            parse_mode=ParseMode.HTML,
        )
        await _show_pay_methods(query.message.reply_text)
        return

    if data.startswith("toggle_group:"):
        if not _is_seed_admin(user_id):
            await query.message.reply_text(T("en", "seed_admin_only"), parse_mode=ParseMode.HTML)
            return
        gid = int(data.split(":", 1)[1])
        status = toggle_approval_group(gid)
        if status is None:
            await query.message.reply_text(T("en", "adm_group_not_found", id=gid), parse_mode=ParseMode.HTML)
        else:
            await query.message.reply_text(T("en", "adm_group_toggled", id=gid, status=status), parse_mode=ParseMode.HTML)
        await _show_groups(query.message.reply_text)
        return


# ── Payment method: show info, set user state ─────────────────────────────────

async def _handle_payment_method_select(query, user_id: int, lang: str, method_id: int) -> None:
    method = get_payment_method(method_id)
    if not method or not method["active"]:
        await query.message.reply_text("❌ Payment method not available.")
        return
    req_id = create_payment_request(
        user_id    = user_id,
        username   = query.from_user.username,
        first_name = query.from_user.first_name,
        method_id  = method_id,
    )
    user_states[user_id] = {"action": "awaiting_screenshot", "method_id": method_id, "req_id": req_id}
    info = _format_payment_info(method, lang) + "\n\n" + T(lang, "send_screenshot")
    if method.get("qr_file_id"):
        await query.message.reply_photo(photo=method["qr_file_id"], caption=info, parse_mode=ParseMode.HTML)
    else:
        await query.message.reply_text(info, parse_mode=ParseMode.HTML)


# ── Photo handler: screenshots + QR uploads ───────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id

    # Admin: QR upload step
    if _is_admin(user_id) and user_id in admin_states:
        state = admin_states[user_id]
        if state.get("step") == "qr":
            state["data"]["qr_file_id"] = update.message.photo[-1].file_id
            if state["action"] == "add_pay":
                await _finish_add_pay(update, user_id, state["data"])
            else:
                await _finish_edit_pay(update, user_id, state)
            return

    # Buyer: payment screenshot
    if user_id in user_states and user_states[user_id].get("action") == "awaiting_screenshot":
        state     = user_states.pop(user_id)
        req_id    = state["req_id"]
        method_id = state["method_id"]
        lang      = _lang(user_id)
        file_id   = update.message.photo[-1].file_id
        method    = get_payment_method(method_id)

        uname   = f"@{update.message.from_user.username}" if update.message.from_user.username else "N/A"
        info_fields = _format_payment_info(method, "en") if method else ""
        caption = T("en", "adm_pay_review",
                    user_id=user_id, uname=uname, fields=info_fields, time=_fmt_time())
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_pay:{req_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject_pay:{req_id}"),
        ]])

        admin_msg_id = None
        for tid in _approval_targets():
            try:
                sent = await context.bot.send_photo(
                    chat_id=tid, photo=file_id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=kb,
                )
                if not admin_msg_id:
                    admin_msg_id = sent.message_id
            except Exception as exc:
                logger.warning("Could not send to %s: %s", tid, exc)

        attach_screenshot(req_id, file_id, admin_msg_id)
        await update.message.reply_text(T(lang, "screenshot_received"), parse_mode=ParseMode.HTML)


# ── Approve / reject ──────────────────────────────────────────────────────────

async def _do_approve(query, context, admin_id: int, req_id: int) -> None:
    req = approve_payment_request(req_id, admin_id)
    if req is None:
        await query.answer("Already processed.", show_alert=True)
        return
    buyer_id = req["user_id"]
    method   = get_payment_method(req["method_id"])
    mark_paid(buyer_id)
    log_payment(buyer_id, req["username"], req["first_name"],
                method["payment_type"] if method else "UNKNOWN", 1, f"req_{req_id}")
    try:
        await context.bot.send_message(
            chat_id=buyer_id, text=T(_lang(buyer_id), "payment_approved"), parse_mode=ParseMode.HTML
        )
    except Exception as exc:
        logger.warning("Could not notify buyer %d: %s", buyer_id, exc)
    try:
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ <b>APPROVED</b> by admin {admin_id}",
            parse_mode=ParseMode.HTML, reply_markup=None,
        )
    except Exception:
        pass


async def _do_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id: int) -> None:
    state = admin_states.pop(admin_id, None)
    if not state or state.get("action") != "reject":
        return
    reason = update.message.text.strip()
    req    = reject_payment_request(state["req_id"], admin_id, reason)
    if req is None:
        await update.message.reply_text("ℹ️ Request has already been processed by another admin.")
        return
    buyer_id = req["user_id"]
    try:
        await context.bot.send_message(
            chat_id=buyer_id, text=T(_lang(buyer_id), "payment_rejected", reason=reason),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("Could not notify buyer %d: %s", buyer_id, exc)
    await update.message.reply_text(
        T("en", "adm_rejected_notify", req_id=state["req_id"], admin_id=admin_id, reason=reason),
        parse_mode=ParseMode.HTML,
    )


# ── Admin-only decorators ──────────────────────────────────────────────────────

def _admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id):
            await update.message.reply_text(T("en", "admin_only"))
            return
        return await fn(update, context)
    return wrapper


def _seed_admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_seed_admin(update.effective_user.id):
            await update.message.reply_text(T("en", "seed_admin_only"), parse_mode=ParseMode.HTML)
            return
        return await fn(update, context)
    return wrapper


# ── /give  /revoke ─────────────────────────────────────────────────────────────

@_admin_only
async def handle_give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args  = context.args or []
    usage = "📝 /give &lt;id&gt; forever | month &lt;1-12&gt; | year &lt;1-12&gt;"
    if len(args) < 2:
        await update.message.reply_text(usage, parse_mode=ParseMode.HTML)
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    gtype = args[1].lower()
    if gtype == "forever":
        add_grant(target, "forever", None, update.effective_user.id)
        await update.message.reply_text(f"✅ <b>Forever</b> access → <code>{target}</code>.", parse_mode=ParseMode.HTML)
    elif gtype in ("month", "year"):
        if len(args) < 3:
            await update.message.reply_text(usage, parse_mode=ParseMode.HTML)
            return
        try:
            count = int(args[2])
        except ValueError:
            await update.message.reply_text("❌ Count must be 1-12.")
            return
        if not (1 <= count <= 12):
            await update.message.reply_text("❌ Count 1-12.")
            return
        result = add_grant(target, gtype, count, update.effective_user.id)
        unit   = "month(s)" if gtype == "month" else "year(s)"
        exp    = ""
        if result["expiry_at"]:
            exp = f"\n📅 Expires: {datetime.fromisoformat(result['expiry_at']).strftime('%Y-%m-%d %H:%M UTC')}"
        await update.message.reply_text(
            f"✅ <b>{count} {unit}</b> access → <code>{target}</code>.{exp}", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"❌ Unknown type '{gtype}'.\n\n{usage}", parse_mode=ParseMode.HTML)


@_admin_only
async def handle_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /revoke &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    if revoke_grant(target):
        await update.message.reply_text(f"✅ Grant revoked for <code>{target}</code>.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ No grant for <code>{target}</code>.", parse_mode=ParseMode.HTML)


# ── Admin ID management ───────────────────────────────────────────────────────

@_admin_only
async def handle_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /addadmin &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    if add_admin(uid, update.effective_user.id):
        await update.message.reply_text(T("en", "adm_added_admin", uid=uid), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ <code>{uid}</code> is already an admin.", parse_mode=ParseMode.HTML)


@_admin_only
async def handle_deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /deladmin &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    if uid == update.effective_user.id:
        await update.message.reply_text(T("en", "adm_cannot_del_self"), parse_mode=ParseMode.HTML)
        return
    if remove_admin(uid):
        await update.message.reply_text(T("en", "adm_deleted_admin", uid=uid), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ <code>{uid}</code> is not an admin.", parse_mode=ParseMode.HTML)


# ── Unified payment method management ─────────────────────────────────────────

ADD_PAY_STEPS  = ["payment_type", "payment_name", "payment_number", "payment_address", "blockchain", "amount", "qr", "payment_link"]
REQUIRED_STEPS = {"payment_type", "amount"}
STEP_PROMPT_KEY = {
    "payment_type":    "adm_addpay_type",
    "payment_name":    "adm_addpay_name",
    "payment_number":  "adm_addpay_number",
    "payment_address": "adm_addpay_address",
    "blockchain":      "adm_addpay_blockchain",
    "amount":          "adm_addpay_amount",
    "qr":              "adm_addpay_qr",
    "payment_link":    "adm_addpay_link",
}


def _next_step(step: str) -> Optional[str]:
    idx = ADD_PAY_STEPS.index(step)
    return ADD_PAY_STEPS[idx + 1] if idx + 1 < len(ADD_PAY_STEPS) else None


@_admin_only
async def handle_addpay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    admin_states[admin_id] = {"action": "add_pay", "step": ADD_PAY_STEPS[0], "data": {}}
    await update.message.reply_text(T("en", "adm_addpay_type"), parse_mode=ParseMode.HTML)


@_admin_only
async def handle_editpay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /editpay &lt;method_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        method_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ method_id must be a number.")
        return
    await _start_edit_pay(update.message.reply_text, update.effective_user.id, method_id)


async def _start_edit_pay(reply_fn, admin_id: int, method_id: int) -> None:
    method = get_payment_method(method_id)
    if not method:
        await reply_fn(T("en", "adm_del_method_fail", id=method_id), parse_mode=ParseMode.HTML)
        return
    admin_states[admin_id] = {
        "action": "edit_pay", "step": ADD_PAY_STEPS[0], "method_id": method_id,
        "data": {},
    }
    await reply_fn(
        T("en", "adm_editpay_start", id=method_id, name=_method_label(method)),
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def handle_delpay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /delpay &lt;method_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        method_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ method_id must be a number.")
        return
    ok = delete_payment_method(method_id)
    await update.message.reply_text(
        T("en", "adm_del_method_ok", id=method_id) if ok else T("en", "adm_del_method_fail", id=method_id),
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def handle_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Legacy single-group setting, kept only as a fallback if no Approval
    # Group has been configured via /addgroup.
    gid = update.message.chat.id
    set_setting("admin_group_id", str(gid))
    await update.message.reply_text(T("en", "adm_setgroup_ok", gid=gid), parse_mode=ParseMode.HTML)


async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not _is_admin(admin_id):
        return
    state = admin_states.get(admin_id)
    if state and state.get("action") in ("add_pay", "edit_pay"):
        await _advance_admin_state(update, context, admin_id, state, text=None)


# ── Multi-step admin conversation (unified payment method wizard) ─────────────

async def _advance_admin_state(update, context, admin_id, state, text):
    action = state["action"]
    step   = state["step"]
    value  = text.strip() if text and text.strip() and text.strip() != "/skip" else None

    if step == "qr":
        # Handled by handle_photo when a photo arrives; a text message here
        # (typically /skip) just moves past this step.
        pass
    elif value is None and step in REQUIRED_STEPS:
        await update.message.reply_text(T("en", "adm_addpay_required"), parse_mode=ParseMode.HTML)
        return
    else:
        state["data"][step] = value

    nxt = _next_step(step)
    if nxt is None:
        if action == "add_pay":
            await _finish_add_pay(update, admin_id, state["data"])
        else:
            await _finish_edit_pay(update, admin_id, state)
        return

    state["step"] = nxt
    await update.message.reply_text(T("en", STEP_PROMPT_KEY[nxt]), parse_mode=ParseMode.HTML)


async def _finish_add_pay(update, admin_id, data):
    admin_states.pop(admin_id, None)
    method_id = add_payment_method(**data)
    label = data.get("payment_type", "Method")
    if data.get("payment_name"):
        label += f" — {data['payment_name']}"
    await update.message.reply_text(T("en", "adm_addpay_done", name=label, id=method_id), parse_mode=ParseMode.HTML)


async def _finish_edit_pay(update, admin_id, state):
    admin_states.pop(admin_id, None)
    ok = edit_payment_method(state["method_id"], **state["data"])
    await update.message.reply_text(
        T("en", "adm_editpay_done", id=state["method_id"]) if ok
        else T("en", "adm_del_method_fail", id=state["method_id"]),
        parse_mode=ParseMode.HTML,
    )


# ── Approval Group management (seed admins only) ──────────────────────────────

@_seed_admin_only
async def handle_addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(T("en", "adm_group_help"), parse_mode=ParseMode.HTML)
        return
    try:
        group_chat_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ group_id must be a number.")
        return
    name = " ".join(args[1:])
    new_id = add_approval_group(group_chat_id, name, update.effective_user.id)
    await update.message.reply_text(
        T("en", "adm_group_added", name=name, id=new_id, gid=group_chat_id), parse_mode=ParseMode.HTML
    )


@_seed_admin_only
async def handle_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_groups(update.message.reply_text)


async def _show_groups(reply_fn):
    groups = list_approval_groups()
    if not groups:
        await reply_fn(T("en", "adm_no_groups"), parse_mode=ParseMode.HTML)
        return
    lines   = []
    buttons = []
    for g in groups:
        st = "✅" if g["status"] == "active" else "❌"
        lines.append(f"{st} [{g['id']}] <b>{g['group_name']}</b> — chat <code>{g['group_id']}</code> ({g['status']})")
        buttons.append([InlineKeyboardButton(f"Toggle #{g['id']}", callback_data=f"toggle_group:{g['id']}")])
    await reply_fn(
        T("en", "adm_group_list", list="\n".join(lines)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@_seed_admin_only
async def handle_togglegroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /togglegroup &lt;id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        gid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ id must be a number.")
        return
    status = toggle_approval_group(gid)
    if status is None:
        await update.message.reply_text(T("en", "adm_group_not_found", id=gid), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(T("en", "adm_group_toggled", id=gid, status=status), parse_mode=ParseMode.HTML)


@_seed_admin_only
async def handle_delgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 /delgroup &lt;id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        gid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ id must be a number.")
        return
    ok = delete_approval_group(gid)
    await update.message.reply_text(
        T("en", "adm_group_deleted", id=gid) if ok else T("en", "adm_group_not_found", id=gid),
        parse_mode=ParseMode.HTML,
    )


# ── Display helpers (menu buttons + /commands) ────────────────────────────────

@_admin_only
async def handle_grants_cmd(update, context): await _show_grants(update)

@_admin_only
async def handle_payments_cmd(update, context): await _show_payments(update)

@_admin_only
async def handle_stats_cmd(update, context): await _show_stats(update)


async def _show_grants(update):
    grants = list_grants()
    if not grants:
        await update.message.reply_text("📋 No grants found.")
        return
    lines = ["📋 <b>All Grants</b>\n"]
    for g in grants:
        lines.append(
            f"{'✅' if g['active'] else '❌'} <code>{g['user_id']}</code> — {g['grant_type']}"
            + (f" ×{g['grant_count']}" if g["grant_count"] else "")
            + f"\n    └ {g['status']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _show_payments(update):
    payments = get_recent_payments(15)
    if not payments:
        await update.message.reply_text("💰 No payments recorded yet.")
        return
    lines = [f"💰 <b>Recent Payments</b> (last {len(payments)})\n"]
    for p in payments:
        lines.append(
            f"• <code>{p['user_id']}</code> @{p['username'] or 'N/A'}\n"
            f"  └ {p['amount']} {p['currency']} — {p['paid_at'][:16].replace('T',' ')} UTC"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _show_stats(update):
    s = get_bot_stats()
    await update.message.reply_text(
        f"📊 <b>Bot Stats</b>\n\n"
        f"👥 Total users:       <b>{s['total_users']}</b>\n"
        f"✅ Paid:              <b>{s['paid_users']}</b>\n"
        f"⏳ Pending reviews:   <b>{s['pending_requests']}</b>\n"
        f"🎁 Active grants:     <b>{s['active_grants']}</b>\n"
        f"❌ Expired grants:    <b>{s['expired_grants']}</b>\n"
        f"💰 Total payments:    <b>{s['total_payments']}</b>",
        parse_mode=ParseMode.HTML,
    )


async def _show_admins(update):
    admins = list_admins()
    if not admins:
        await update.message.reply_text(T("en", "adm_no_admins"))
        return
    def _admin_line(a):
        added_by_part = " (seeded)" if not a["added_by"] else " by " + str(a["added_by"])
        date_part = (a["added_at"] or "")[:10]
        return f"• <code>{a['user_id']}</code>{added_by_part} — {date_part}"
    lines = [_admin_line(a) for a in admins]
    await update.message.reply_text(T("en", "adm_admin_list", list="\n".join(lines)), parse_mode=ParseMode.HTML)


async def _show_pay_methods(reply_fn):
    methods = list_payment_methods(active_only=False)
    if not methods:
        await reply_fn(T("en", "adm_no_methods"), parse_mode=ParseMode.HTML)
        return
    lines   = []
    buttons = []
    for m in methods:
        st = "✅" if m["active"] else "❌"
        lines.append(f"{st} [{m['id']}] <b>{_method_label(m)}</b>" + (" 🖼" if m["qr_file_id"] else ""))
        if m["active"]:
            buttons.append([
                InlineKeyboardButton(f"✏️ Edit {m['id']}", callback_data=f"edit_method:{m['id']}"),
                InlineKeyboardButton(f"🗑 Del {m['id']}",  callback_data=f"del_method:{m['id']}"),
            ])
    await reply_fn(
        T("en", "adm_method_list", list="\n".join(lines)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


# ── Main text message handler ─────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text    = (update.message.text or "").strip()
    user_id = update.message.from_user.id
    lang    = _lang(user_id)

    # Admin multi-step: reject reason / payment method wizard
    if _is_admin(user_id) and user_id in admin_states:
        state = admin_states.get(user_id, {})
        if state.get("action") == "reject":
            await _do_reject_reason(update, context, user_id)
            return
        if state.get("action") in ("add_pay", "edit_pay"):
            await _advance_admin_state(update, context, user_id, state, text)
            return

    # Admin menu buttons
    if _is_admin(user_id) and text in ADMIN_BUTTONS:
        if text == BTN_GRANTS:       await _show_grants(update)
        elif text == BTN_PAYMENTS:   await _show_payments(update)
        elif text == BTN_GIVE:       await update.message.reply_text(T("en", "adm_give_help"),   parse_mode=ParseMode.HTML)
        elif text == BTN_REVOKE:     await update.message.reply_text(T("en", "adm_revoke_help"), parse_mode=ParseMode.HTML)
        elif text == BTN_STATS:      await _show_stats(update)
        elif text == BTN_ADMINS:     await _show_admins(update)
        elif text == BTN_PAY_METHODS: await _show_pay_methods(update.message.reply_text)
        elif text == BTN_GROUPS:
            if _is_seed_admin(user_id):
                await update.message.reply_text(T("en", "adm_group_help"), parse_mode=ParseMode.HTML)
                await _show_groups(update.message.reply_text)
            else:
                await update.message.reply_text(T("en", "seed_admin_only"), parse_mode=ParseMode.HTML)
        return

    # Persistent user menu buttons
    if not _is_admin(user_id) and text in USER_BUTTONS:
        if text == BTN_LANGUAGE:
            await handle_lang(update, context)
        elif text == BTN_UPGRADE:
            await handle_pay(update, context)
        return

    # URL processing
    url = extract_tweet_url(text)
    if url is None:
        return

    if not _has_unlimited(user_id):
        if not try_consume_free_use(user_id, FREE_USES):
            methods = list_payment_methods(active_only=True)
            reply_markup = _build_payment_kb(lang) if methods else USER_KB
            await update.message.reply_text(
                T(lang, "pay_prompt", free_uses=FREE_USES),
                parse_mode=ParseMode.HTML, reply_markup=reply_markup,
            )
            return

    base = to_fixupx(url)
    try:
        n = await count_videos(base)
    except Exception as exc:
        await update.message.reply_text(T(lang, "error_video", err=exc), parse_mode=ParseMode.HTML)
        return

    if n == 0:
        await update.message.reply_text(T(lang, "no_video"), parse_mode=ParseMode.HTML)
        return

    footer = ""
    if not _has_unlimited(user_id):
        remaining = FREE_USES - get_usage_count(user_id)
        footer = T(lang, "free_remaining", remaining=max(0, remaining), free_uses=FREE_USES) \
            if remaining > 0 else T(lang, "last_free")

    for i in range(1, n + 1):
        link = f"{base}/video/{i}"
        await update.message.reply_text(link if i < n else link + footer)


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

_ptb_app: Optional[Application] = None


def _get_ptb_app() -> Application:
    global _ptb_app
    if _ptb_app is None:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start",       handle_start))
        app.add_handler(CommandHandler("lang",        handle_lang))
        app.add_handler(CommandHandler("pay",         handle_pay))
        app.add_handler(CommandHandler("give",        handle_give))
        app.add_handler(CommandHandler("revoke",      handle_revoke))
        app.add_handler(CommandHandler("grants",      handle_grants_cmd))
        app.add_handler(CommandHandler("payments",    handle_payments_cmd))
        app.add_handler(CommandHandler("stats",       handle_stats_cmd))
        app.add_handler(CommandHandler("addadmin",    handle_addadmin))
        app.add_handler(CommandHandler("deladmin",    handle_deladmin))
        app.add_handler(CommandHandler("addpay",      handle_addpay))
        app.add_handler(CommandHandler("editpay",     handle_editpay))
        app.add_handler(CommandHandler("delpay",      handle_delpay))
        app.add_handler(CommandHandler("setgroup",    handle_setgroup))
        app.add_handler(CommandHandler("addgroup",    handle_addgroup))
        app.add_handler(CommandHandler("groups",      handle_groups))
        app.add_handler(CommandHandler("togglegroup", handle_togglegroup))
        app.add_handler(CommandHandler("delgroup",    handle_delgroup))
        app.add_handler(CommandHandler("skip",        handle_skip))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        asyncio.run(app.initialize())
        _ptb_app = app
        logger.info("PTB Application initialized.")
    return _ptb_app


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  FLASK APP  (Vercel entry-point — exports `app`)
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if not data:
        return "bad request", 400

    update_id = data.get("update_id")
    if update_id is not None and not mark_update_processed(update_id):
        # Telegram already sent this update once (retry after a slow/failed
        # response) — acknowledge immediately without reprocessing it.
        logger.info("Duplicate update_id %s ignored.", update_id)
        return "ok", 200

    try:
        ptb = _get_ptb_app()
        update = Update.de_json(data, ptb.bot)
        asyncio.run(ptb.process_update(update))
    except Exception as exc:
        logger.exception("Error processing update: %s", exc)
        return "error", 500
    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    """Landing page with Vercel Analytics"""
    return render_template("index.html")


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    """
    One-time setup helper. Visit:
      https://your-project.vercel.app/set_webhook?url=https://your-project.vercel.app/webhook
    """
    webhook_url = request.args.get("url", "")
    if not webhook_url:
        return "Pass ?url=https://your-domain.vercel.app/webhook", 400
    import urllib.request, json as _json
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = _json.dumps({"url": webhook_url}).encode()
    req     = urllib.request.Request(api, data=payload, headers={"Content-Type": "application/json"})
    resp    = urllib.request.urlopen(req)
    return resp.read().decode(), 200

