import os
import json
import uuid
import asyncio
import psycopg2
import psycopg2.extras
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

print("RUNNING VERSION: structured-prizes-7-faq-admin")

# =========================
# ENV / SECRETS
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
guild_id_raw = os.getenv("GUILD_ID")
winner_channel_raw = os.getenv("WINNER_CHANNEL_ID")
giveaway_category_raw = os.getenv("GIVEAWAY_CATEGORY_ID")
mod_role_raw = os.getenv("MOD_ROLE_ID")
backend_log_channel_raw = os.getenv("BACKEND_LOG_CHANNEL_ID")
support_panel_channel_raw = os.getenv("SUPPORT_PANEL_CHANNEL_ID")
support_category_raw = os.getenv("SUPPORT_CATEGORY_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN:
    raise ValueError("Missing DISCORD_TOKEN secret")
if not guild_id_raw:
    raise ValueError("Missing GUILD_ID secret")
if not winner_channel_raw:
    raise ValueError("Missing WINNER_CHANNEL_ID secret")
if not giveaway_category_raw:
    raise ValueError("Missing GIVEAWAY_CATEGORY_ID secret")
if not mod_role_raw:
    raise ValueError("Missing MOD_ROLE_ID secret")
if not backend_log_channel_raw:
    raise ValueError("Missing BACKEND_LOG_CHANNEL_ID secret")
if not support_panel_channel_raw:
    raise ValueError("Missing SUPPORT_PANEL_CHANNEL_ID secret")
if not support_category_raw:
    raise ValueError("Missing SUPPORT_CATEGORY_ID secret")
if not DATABASE_URL:
    raise ValueError("Missing DATABASE_URL secret")

GUILD_ID = int(guild_id_raw)
WINNER_CHANNEL_ID = int(winner_channel_raw)
GIVEAWAY_CATEGORY_ID = int(giveaway_category_raw)
MOD_ROLE_ID = int(mod_role_raw)
BACKEND_LOG_CHANNEL_ID = int(backend_log_channel_raw)
SUPPORT_PANEL_CHANNEL_ID = int(support_panel_channel_raw)
SUPPORT_CATEGORY_ID = int(support_category_raw)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")

SHOW_OPTIONS = [
    "Big Daddy Morning Show",
    "Power Hour",
    "Happy Hour",
    "Traders and Haters Podcast",
    "Trading Wheels"
]

# =========================
# SHOW TAG LINE HELPER
# =========================
def get_show_tag_line(show: str | None) -> str:
    if not show:
        return "Tag **Max** and **Lama**."
    normalized = show.strip().lower()
    POWER_HOUR_ALIASES = {"power hour", "ph", "powerhour"}
    HAPPY_HOUR_ALIASES = {"happy hour", "hh", "happyhour"}
    if normalized in POWER_HOUR_ALIASES:
        return "Tag **Logan** and **Javi**."
    if normalized in HAPPY_HOUR_ALIASES:
        return "Tag **Koach** and **Izzy**."
    return "Tag **Max** and **Lama**."

# =========================
# DATABASE
# =========================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS winners (
                    id SERIAL PRIMARY KEY,
                    bundle_id TEXT,
                    timestamp TEXT,
                    user_name TEXT,
                    user_id TEXT,
                    source TEXT,
                    show TEXT,
                    prize TEXT,
                    code TEXT,
                    mod TEXT,
                    mod_id TEXT,
                    channel TEXT,
                    server TEXT,
                    status TEXT DEFAULT 'ticket_created',
                    type TEXT,
                    reason TEXT,
                    notes TEXT,
                    ticket_channel_id TEXT,
                    ticket_channel_name TEXT,
                    backend_message_id TEXT,
                    prompt_message_id TEXT,
                    header_message_id TEXT,
                    updated_at TEXT,
                    updated_by TEXT,
                    updated_by_id TEXT,
                    completed_at TEXT,
                    history JSONB DEFAULT '[]',
                    prize_catalog_id INTEGER,
                    prop_firm_id INTEGER,
                    account_type_id INTEGER,
                    account_size_id INTEGER,
                    custom_prize_text TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transcripts (
                    id SERIAL PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    ticket_type TEXT NOT NULL,
                    bundle_id TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    deleted_by TEXT,
                    deleted_at TIMESTAMP DEFAULT NOW(),
                    messages JSONB DEFAULT '[]'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prop_firms (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT true,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_types (
                    id SERIAL PRIMARY KEY,
                    prop_firm_id INTEGER NOT NULL REFERENCES prop_firms(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT true,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (prop_firm_id, name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_sizes (
                    id SERIAL PRIMARY KEY,
                    label TEXT NOT NULL UNIQUE,
                    numeric_size INTEGER,
                    active BOOLEAN NOT NULL DEFAULT true,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prize_catalog (
                    id SERIAL PRIMARY KEY,
                    prop_firm_id INTEGER NOT NULL REFERENCES prop_firms(id) ON DELETE CASCADE,
                    account_type_id INTEGER NOT NULL REFERENCES account_types(id) ON DELETE CASCADE,
                    account_size_id INTEGER NOT NULL REFERENCES account_sizes(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (prop_firm_id, account_type_id, account_size_id)
                )
            """)
            cur.execute("""
                ALTER TABLE winners
                    ADD COLUMN IF NOT EXISTS prize_catalog_id INTEGER,
                    ADD COLUMN IF NOT EXISTS prop_firm_id INTEGER,
                    ADD COLUMN IF NOT EXISTS account_type_id INTEGER,
                    ADD COLUMN IF NOT EXISTS account_size_id INTEGER,
                    ADD COLUMN IF NOT EXISTS custom_prize_text TEXT
            """)

            # -------------------------
            # FAQ TABLES
            # -------------------------
            # faq_categories: one row per category (TradingView, Discord, Billing, etc.)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS faq_categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_by_discord_id TEXT,
                    updated_by_discord_id TEXT
                )
            """)
            # faq_entries: one row per question/answer pair
            cur.execute("""
                CREATE TABLE IF NOT EXISTS faq_entries (
                    id SERIAL PRIMARY KEY,
                    category_id INTEGER NOT NULL REFERENCES faq_categories(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'public',
                    escalate BOOLEAN NOT NULL DEFAULT false,
                    active BOOLEAN NOT NULL DEFAULT true,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_by_discord_id TEXT,
                    updated_by_discord_id TEXT
                )
            """)

        conn.commit()
    print("[DB] Tables initialized.")
    seed_faqs()


# =========================
# FAQ SEED DATA
# =========================
# This runs once on startup. If faq_categories already has rows, it exits immediately.
# To re-seed: DELETE FROM faq_categories; (cascade deletes entries too)

_FAQ_SEED: list[dict] = [
    {
        "name": "TradingView",
        "sort_order": 0,
        "entries": [
            {
                "question": "How do I link my TradingView username?",
                "answer": "Your TradingView account is linked directly from the top-right corner of the Dashboard. You only need to do this one time. Once your TradingView username is connected, indicator access will be granted automatically to that account.",
                "sort_order": 0,
            },
            {
                "question": "Indicator missing on TradingView?",
                "answer": "If your indicator isn't showing up on TradingView, first make sure your TradingView username is linked in your Dashboard. Indicator access is granted to the username you provide. If it's already linked and you still don't see it, try refreshing TradingView or use the chat assistant to run an access check. If you are still having issues, disconnect your TradingView from the website and reconnect it.",
                "sort_order": 1,
            },
            {
                "question": "I linked the wrong TradingView username. How do I fix it?",
                "answer": "Go to your Dashboard and update your TradingView username in the top-right corner. Once updated, access will automatically transfer to the correct account. If you still experience issues, describe the problem in this ticket and a moderator will assist you.",
                "sort_order": 2,
            },
            {
                "question": "Can I use the indicators on multiple TradingView accounts?",
                "answer": "Indicator access is granted to one TradingView username per subscription. If you need access on a different account, you must update your username in the Dashboard.",
                "sort_order": 3,
            },
        ],
    },
    {
        "name": "Discord",
        "sort_order": 1,
        "entries": [
            {
                "question": "How do I link my Discord account?",
                "answer": "Your Discord account is linked directly from the top-right corner of the Dashboard. You only need to do this one time. Once connected, your Discord roles and access will sync automatically based on your active subscription.",
                "sort_order": 0,
            },
            {
                "question": "Having trouble seeing Discord channels?",
                "answer": "If you can't see certain Discord channels, make sure your Discord account is linked in your Dashboard. Once connected, your roles sync automatically based on your active purchases. If you've already linked your Discord and still don't have access, a moderator has been notified and will assist you shortly.",
                "sort_order": 1,
                "escalate": True,
            },
            {
                "question": "Why can't I type in the Discord chats?",
                "answer": "This is because you haven't completed the email verification process. While you are in the discord find the channel / section towards the top that says 'Start Here' click it. Next you'll see a drop down with different options, select '#Step Three'. You'll see a blue button that says 'Start email verification'. Complete this and you will now be able to type in the chats.",
                "sort_order": 2,
            },
            {
                "question": "Is the community active every day?",
                "answer": "Yes. The Discord community is active daily with market discussion, trade breakdowns, live shows, and member interaction. You're never trading alone — there's always structure, support, and FUN.",
                "sort_order": 3,
            },
            {
                "question": "What happens if I am banned from the Discord?",
                "answer": "If you are banned from our Discord community, the ban is final and non-negotiable. Our moderation team enforces community guidelines to maintain a respectful and productive environment for all members. As such, banned users will not be reinstated and appeals will not be considered.",
                "sort_order": 4,
            },
        ],
    },
    {
        "name": "Billing",
        "sort_order": 2,
        "entries": [
            {
                "question": "How do I upgrade my subscription?",
                "answer": "All subscription changes are handled in the Manage Billing section of the Dashboard. Navigate to Dashboard → Billing → Manage Billing to upgrade your subscription at any time.",
                "sort_order": 0,
            },
            {
                "question": "How do I cancel my subscription?",
                "answer": "You can cancel your subscription from the Manage Billing section of the Dashboard. Go to Dashboard → Billing → Manage Billing, where you can cancel your subscription directly.",
                "sort_order": 1,
            },
            {
                "question": "Do you offer refunds?",
                "answer": "Due to the digital nature of our products (indicators, courses, and community access), all purchases are final. If you believe there has been a billing error, a moderator has been notified and will assist you shortly.",
                "sort_order": 2,
                "escalate": True,
            },
            {
                "question": "What do I do if I was charged twice for an indicator subscription?",
                "answer": "A moderator has been notified and will assist you shortly.",
                "sort_order": 3,
                "escalate": True,
            },
        ],
    },
    {
        "name": "Indicators",
        "sort_order": 3,
        "entries": [
            {
                "question": "Help me pick the right indicator(s)",
                "answer": "We offer powerful trading indicators designed for different trading styles — from scalping to swing trading. Each indicator has unique features to help you spot opportunities in the market. Visit our Indicators page to browse the full collection and find the perfect fit for your strategy: https://www.maxoptionstrading.com/indicators",
                "sort_order": 0,
            },
            {
                "question": "What indicator(s) do you recommend?",
                "answer": "All of our MOT indicators are great! The best part is that you can mix and match them together for additional confluence. https://www.maxoptionstrading.com/indicators",
                "sort_order": 1,
            },
            {
                "question": "What markets do your indicators work on?",
                "answer": "Our indicators are designed to work across multiple markets including stocks, options, futures, and crypto. Many members use them for intraday trading as well as swing trading strategies.",
                "sort_order": 2,
            },
            {
                "question": "Are the indicators beginner-friendly?",
                "answer": "Yes! Our indicators are built to be powerful yet easy to understand. We also provide tutorials, live examples during shows, and community support to help you learn how to use them effectively.",
                "sort_order": 3,
            },
            {
                "question": "What makes your indicators different?",
                "answer": "Our indicators are built from real trading experience — not theory. They're designed to simplify decision-making, improve timing, and help you trade with confidence. Many members combine them for added confluence and stronger setups. You'll also see them used live during our shows so you can learn exactly how to apply them in real time.",
                "sort_order": 4,
            },
            {
                "question": "My Discord role is not showing up after purchasing an indicator.",
                "answer": "On the website remove your discord access, then reconnect your discord to the website and your role will be available.",
                "sort_order": 5,
            },
        ],
    },
    {
        "name": "Courses",
        "sort_order": 4,
        "entries": [
            {
                "question": "Can you please help me pick a course?",
                "answer": "Our courses are designed to take you from beginner to advanced trader. Whether you're just starting out or looking to refine your strategy, we have courses covering everything from the basics to advanced techniques. Visit our Courses page to explore what's available: https://www.maxoptionstrading.com/courses (also check out the testimonials posted on trustpilot: https://www.trustpilot.com/review/www.maxoptionstrading.com)",
                "sort_order": 0,
            },
            {
                "question": "How do I transfer my WHOP purchases to the website?",
                "answer": "A moderator has been notified and will assist you shortly with a redemption code.",
                "sort_order": 1,
                "escalate": True,
            },
            {
                "question": "My Discord role is not showing up after purchasing the course.",
                "answer": "On the website remove your discord access, then connect your discord back to the website and your roles will be available.",
                "sort_order": 2,
            },
            {
                "question": "I have issues watching the course content.",
                "answer": "To ensure videos play properly, please follow these recommendations:\n\nAlways use the latest version of both your operating system and browser for best compatibility.\n\nDesktop (Windows/Mac):\n- Latest version of Chrome or Firefox is recommended.\n- Edge (v129 or later) is supported on Windows 10+.\n- Safari works if FairPlay DRM is integrated.\n\nAndroid (Phone/Tablet/Chromebook):\n- Latest version of Chrome (Android 5+).\n- If Chrome does not work, try the latest version of Firefox or Edge.\n\niOS (iPhone/iPad):\n- Updated Safari is recommended.\n- iOS 11.2 or later is required.\n- Chrome may work on newer iOS versions, but Safari is the most reliable option.\n\nIf the recommended options above don't work, it may also require that you clear your cache.",
                "sort_order": 3,
            },
        ],
    },
    {
        "name": "Live Shows",
        "sort_order": 5,
        "entries": [
            {
                "question": "What time are the live shows?",
                "answer": "We have three daily live shows:\n\nBig Daddy Morning Show — Daily from 9:30 AM to 11:30 AM EST (Mon-Fri)\nPower Hour Special — Daily from 2:45 PM to 4:15 PM EST (Mon-Fri)\nHappy Hour — Daily from 5:45pm to 7:45pm EST (Sun-Thur)\n\nVisit the MOT Network to watch the shows live or catch up on previous shows posted on our Youtube channel: https://www.youtube.com/@MaxOptionsTrading",
                "sort_order": 0,
            },
            {
                "question": "Are all of the live shows recorded?",
                "answer": "Yes, all live shows are recorded. You can access previous shows on our Youtube channel: https://www.youtube.com/@MaxOptionsTrading",
                "sort_order": 1,
            },
            {
                "question": "When does 'Traders and Haters' podcast go live?",
                "answer": "Traders and Haters podcast is recorded every Tuesday evening at 5pm EST. The show gets edited, and posted on Youtube the following Monday evening.",
                "sort_order": 2,
            },
            {
                "question": "How do I access the live shows?",
                "answer": "Live shows are streamed inside the Discord community and on the MOT Network. Make sure your Discord account is linked and your subscription is active to access member-only streams.",
                "sort_order": 3,
            },
            {
                "question": "How often do you do hit bangers like this?",
                "answer": "EVERY. F-ING. DAY.",
                "sort_order": 4,
            },
        ],
    },
    {
        "name": "General",
        "sort_order": 6,
        "entries": [
            {
                "question": "How do I get the MOT tag?",
                "answer": "On a computer: Click on the MOT tag next someone's name in the discord and click Adopt tag, or:\n- Click on the server name in the upper menu on Discord\n- Go to server tag\n- Adopt the MOT tag\n\nOn a mobile device:\n- Go to profile\n- Edit profile\n- Go to server/guild tag\n- Choose the MOT tag and save",
                "sort_order": 0,
            },
            {
                "question": "How do I enter in Giveaways?",
                "answer": "General rules for giveaways in our discord:\n1. You must be a verified member in our server.\n2. Get the MOT tag in order to be eligible.\n3. Join the live tradings or podcast to get randomly picked.",
                "sort_order": 1,
            },
            {
                "question": "I previously won an account through MOT. How do I qualify for the 'double up' program?",
                "answer": "A moderator has been notified and will assist you shortly.\n\nTo qualify, please have the following ready:\n1) A screenshot of the evaluation account we gave you\n2) A screenshot of the funded account\n3) A screenshot of a payout",
                "sort_order": 2,
                "escalate": True,
            },
            {
                "question": "How long does it take for my access to activate?",
                "answer": "Access is typically granted instantly after your purchase is completed. If you don't see your Discord roles or TradingView indicators within a few minutes, try logging out and back in to your Dashboard. If the issue continues, a moderator has been notified and will assist you shortly.",
                "sort_order": 3,
                "escalate": True,
            },
            {
                "question": "What platform do you recommend for trading?",
                "answer": "Many of our members trade using platforms like TradingView for charting and Tradeovate for futures execution. However, you can use any broker or platform that fits your trading style.",
                "sort_order": 4,
            },
            {
                "question": "Is this community suitable for beginners?",
                "answer": "Absolutely. We have traders at all experience levels — from complete beginners to funded prop firm traders. Our courses, live breakdowns, and coaching options are designed to help you grow at every stage.",
                "sort_order": 5,
            },
            {
                "question": "Do you offer a free trial?",
                "answer": "At this time, we do not offer free trials. However, we provide free educational content on our YouTube channel so you can see our strategies and teaching style before purchasing.",
                "sort_order": 6,
            },
            {
                "question": "What happens after I subscribe?",
                "answer": "Immediately after subscribing:\n1. Link your Discord and TradingView accounts in your Dashboard\n2. Your roles and indicator access sync automatically\n3. Jump into the live sessions and start learning",
                "sort_order": 7,
            },
            {
                "question": "Is this just for options traders?",
                "answer": "No. Our strategies and indicators are used for futures, stocks, options, and even crypto. The principles we teach — structure, momentum, liquidity, and risk management — apply across markets.",
                "sort_order": 8,
            },
            {
                "question": "What if I don't have much time to trade?",
                "answer": "We offer multiple live sessions throughout the day, plus recorded content. Whether you trade full-time or part-time, you can plug into the sessions that fit your schedule and review recordings when needed.",
                "sort_order": 9,
            },
            {
                "question": "Can I actually become profitable using your system?",
                "answer": "Profitability depends on discipline and execution — but we give you the tools, structure, and mentorship to dramatically shorten your learning curve. Members who commit, follow the system, and manage risk properly see the biggest improvements.",
                "sort_order": 10,
            },
            {
                "question": "Why should I join Max Options Trading?",
                "answer": "We don't just live trade — we're education + entertainment + execution + accountability.\nInside Max Options Trading, you get live breakdowns, proven indicators, structured courses, and a serious community of traders focused on consistency.\nOur goal isn't hype — it's helping you build real, repeatable skills.",
                "sort_order": 11,
            },
        ],
    },
    {
        "name": "Prop Firms",
        "sort_order": 7,
        "entries": [
            {
                "question": "Which prop firms do you recommend?",
                "answer": "We've partnered with top-tier prop firms that we personally trust and use. These are vetted trading firms that offer great funding opportunities for serious traders. Check out our Partners page to see our recommended prop firms: https://www.maxoptionstrading.com/partners",
                "sort_order": 0,
            },
            {
                "question": "I've failed prop firm challenges before. Can this help?",
                "answer": "Yes. Many members join specifically to improve their evaluation performance. We focus heavily on discipline, risk management, and high-probability setups — which are critical for passing and maintaining funded accounts.",
                "sort_order": 1,
            },
        ],
    },
    {
        "name": "Coaching",
        "sort_order": 8,
        "entries": [
            {
                "question": "How can I book a trading coach?",
                "answer": "Looking for personalized guidance? Our verified trading coaches offer 1-on-1 mentorship sessions to help you level up your trading game. Get personalized feedback, strategy reviews, and accelerate your learning. Visit our Coaching page to browse available coaches and book a session: https://www.maxoptionstrading.com/coaching",
                "sort_order": 0,
            },
        ],
    },
    {
        "name": "WealthCharts",
        "sort_order": 9,
        "entries": [
            {
                "question": "How do I set up Wealthcharts for trading futures?",
                "answer": "Check out this how to video that Max made on this! https://youtu.be/BWwowJY_cho?si=2oZtFwIKUYQa3fuB",
                "sort_order": 0,
            },
            {
                "question": "How do I link my indicators to WealthCharts?",
                "answer": "https://scribehow.com/viewer/How_to_link_your_indicator_to_WealthCharts_on_the_MOT_website__ctDUpjC4R4anyEZYQvfAiw",
                "sort_order": 1,
            },
            {
                "question": "Where do I find my indicators on WealthCharts?",
                "answer": "https://scribehow.com/embed-preview/How_to_Access_MOT_Indicators_on_Wealthcharts__rrasDmTlT62JUoKS_Ju-wg?as=slides&size=flexible",
                "sort_order": 2,
            },
        ],
    },
    {
        "name": "YouTube",
        "sort_order": 10,
        "entries": [
            {
                "question": "Where can I find free content about ORB?",
                "answer": "Take advantage of all free content around ORB on Max Options trading on YouTube, starting with: https://www.youtube.com/watch?v=SunW-hRFGzY",
                "sort_order": 0,
            },
            {
                "question": "Are all of the live shows recorded?",
                "answer": "Yes, all live shows are recorded. You can access previous shows on our Youtube channel: https://www.youtube.com/@MaxOptionsTrading",
                "sort_order": 1,
            },
            {
                "question": "Where can I find Max's free beginner's course?",
                "answer": "You can find Max's free beginner's course using this link:\nhttps://www.youtube.com/playlist?list=PLVPsZWsA_88QNfmWsBgOLrrYZZoLXBJVb",
                "sort_order": 2,
            },
        ],
    },
    {
        "name": "Tradeovate",
        "sort_order": 11,
        "entries": [
            {
                "question": "How do I set up my trade copier on Tradeovate?",
                "answer": "Here is a YouTube video with Lama where he explains how to properly set up your Group Trading feature on Tradeovate:\nhttps://youtu.be/-1JRz8nC0sw?si=92LB6gljfPSXBkn2",
                "sort_order": 0,
            },
        ],
    },
    {
        "name": "Max Bucks",
        "sort_order": 12,
        "entries": [
            {
                "question": "What are Maxbucks?",
                "answer": "Max Bucks are a reward-based digital currency earned through indicator subscriptions or by purchasing a course. As you accumulate Max Bucks, they can be redeemed toward free indicators or applied toward the purchase of a course. This program is simply our way of giving back and rewarding our customers for their continued support.",
                "sort_order": 0,
            },
            {
                "question": "How do I use Max Bucks?",
                "answer": "MAX BUCKS can ONLY be applied to courses and indicators on our website. Cannot be used for private coaching sessions or any promos.",
                "sort_order": 1,
            },
        ],
    },
    {
        "name": "Affiliate",
        "sort_order": 13,
        "entries": [
            {
                "question": "How does the affiliate program work?",
                "answer": "Yes, the affiliate $$ works on recurring indicator subscriptions and courses (excludes: coaching). You will receive affiliate $$ 30 days after the customer purchase. If the customer continues to subscribe the indicator you will continue to receive the affiliate $$$.",
                "sort_order": 0,
            },
        ],
    },
]


def seed_faqs():
    """
    Insert FAQ seed data into Postgres if faq_categories is empty.
    Safe to call on every startup — exits immediately if already seeded.
    To re-seed: run DELETE FROM faq_categories CASCADE; then restart.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM faq_categories")
                count = cur.fetchone()[0]
                if count > 0:
                    print(f"[FAQ] Already seeded ({count} categories). Skipping.")
                    return
                print("[FAQ] Seeding FAQ data from hardcoded seed...")
                for cat in _FAQ_SEED:
                    cur.execute("""
                        INSERT INTO faq_categories (name, sort_order, active)
                        VALUES (%s, %s, true)
                        RETURNING id
                    """, (cat["name"], cat["sort_order"]))
                    cat_id = cur.fetchone()[0]
                    for entry in cat.get("entries", []):
                        cur.execute("""
                            INSERT INTO faq_entries
                                (category_id, question, answer, visibility, escalate, active, sort_order)
                            VALUES (%s, %s, %s, 'public', %s, true, %s)
                        """, (
                            cat_id,
                            entry["question"],
                            entry["answer"],
                            entry.get("escalate", False),
                            entry["sort_order"],
                        ))
            conn.commit()
        print("[FAQ] Seed complete.")
    except Exception as e:
        print(f"[FAQ ERROR] seed_faqs: {e}")


# =========================
# FAQ QUERY HELPERS
# =========================

def get_active_faq_categories() -> list[dict]:
    """
    Returns all active FAQ categories ordered by sort_order.
    Each dict: { id, name, sort_order }
    Used by: FaqCategoryView, /faq admin commands
    """
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, sort_order
                    FROM faq_categories
                    WHERE active = true
                    ORDER BY sort_order, name
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[FAQ ERROR] get_active_faq_categories: {e}")
        return []


def get_faq_entries_by_category(category_id: int, visibility: str = "public") -> list[dict]:
    """
    Returns active FAQ entries for a category.
    visibility: 'public' returns only public entries.
                'all' returns both public and mod_only entries.
    Each dict: { id, question, answer, escalate, visibility, sort_order }
    Used by: FaqAnswerView (public flow), future mod FAQ viewer
    """
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if visibility == "all":
                    cur.execute("""
                        SELECT id, question, answer, escalate, visibility, sort_order
                        FROM faq_entries
                        WHERE category_id = %s AND active = true
                        ORDER BY sort_order
                    """, (category_id,))
                else:
                    cur.execute("""
                        SELECT id, question, answer, escalate, visibility, sort_order
                        FROM faq_entries
                        WHERE category_id = %s AND active = true AND visibility = 'public'
                        ORDER BY sort_order
                    """, (category_id,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[FAQ ERROR] get_faq_entries_by_category: {e}")
        return []


def get_faq_entry_by_id(entry_id: int) -> dict | None:
    """
    Returns a single FAQ entry by primary key.
    Used by: future mod send-FAQ-to-ticket flow, admin edit/delete
    """
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, category_id, question, answer, escalate, visibility, active, sort_order
                    FROM faq_entries
                    WHERE id = %s
                """, (entry_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        print(f"[FAQ ERROR] get_faq_entry_by_id: {e}")
        return None


def get_faq_category_by_id(category_id: int) -> dict | None:
    """
    Returns a single FAQ category by primary key.
    Used by: future admin edit/delete commands
    """
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, sort_order, active
                    FROM faq_categories
                    WHERE id = %s
                """, (category_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        print(f"[FAQ ERROR] get_faq_category_by_id: {e}")
        return None


# =========================
# CATALOG DB HELPERS
# =========================
def resolve_prize_from_catalog(prop_firm: str, account_type: str, account_size: str) -> dict | None:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        pc.id AS prize_catalog_id,
                        pc.display_name,
                        pf.id AS prop_firm_id,
                        at.id AS account_type_id,
                        sz.id AS account_size_id
                    FROM prize_catalog pc
                    JOIN prop_firms pf ON pf.id = pc.prop_firm_id
                    JOIN account_types at ON at.id = pc.account_type_id
                    JOIN account_sizes sz ON sz.id = pc.account_size_id
                    WHERE LOWER(TRIM(pf.name)) = LOWER(TRIM(%s))
                      AND LOWER(TRIM(at.name)) = LOWER(TRIM(%s))
                      AND LOWER(TRIM(sz.label)) = LOWER(TRIM(%s))
                      AND pc.active = true
                """, (prop_firm, account_type, account_size))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        print(f"[DB ERROR] resolve_prize_from_catalog: {e}")
        return None


def get_active_prop_firms() -> list[dict]:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name FROM prop_firms
                    WHERE active = true
                    ORDER BY sort_order, name
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_active_prop_firms: {e}")
        return []


def get_account_types_for_firm(prop_firm_name: str) -> list[dict]:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT at.id, at.name FROM account_types at
                    JOIN prop_firms pf ON pf.id = at.prop_firm_id
                    WHERE LOWER(TRIM(pf.name)) = LOWER(TRIM(%s))
                      AND at.active = true
                    ORDER BY at.sort_order, at.name
                """, (prop_firm_name,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_account_types_for_firm: {e}")
        return []


def get_sizes_for_firm_and_type(prop_firm_name: str, account_type_name: str) -> list[dict]:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT sz.id, sz.label, sz.numeric_size FROM account_sizes sz
                    JOIN prize_catalog pc ON pc.account_size_id = sz.id
                    JOIN prop_firms pf ON pf.id = pc.prop_firm_id
                    JOIN account_types at ON at.id = pc.account_type_id
                    WHERE LOWER(TRIM(pf.name)) = LOWER(TRIM(%s))
                      AND LOWER(TRIM(at.name)) = LOWER(TRIM(%s))
                      AND pc.active = true
                    ORDER BY sz.sort_order, sz.numeric_size NULLS LAST
                """, (prop_firm_name, account_type_name))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_sizes_for_firm_and_type: {e}")
        return []


# =========================
# UNKNOWN PRIZE HELPER
# =========================
UNKNOWN_PRIZE_LABEL = "Unknown Prize"

def is_unknown_prize(prop_firm: str) -> bool:
    return prop_firm.strip() == UNKNOWN_PRIZE_LABEL

def make_unknown_resolved() -> dict:
    return {
        "display_name": UNKNOWN_PRIZE_LABEL,
        "prize_catalog_id": None,
        "prop_firm_id": None,
        "account_type_id": None,
        "account_size_id": None,
    }


# =========================
# PROMPT HELPERS
# =========================
def load_prompt_config():
    if not os.path.exists(PROMPTS_FILE):
        raise FileNotFoundError(f"{PROMPTS_FILE} not found.")
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


PROMPT_CONFIG = load_prompt_config()


def render_prompt(template_key: str, show: str | None = None, **kwargs) -> str:
    template = PROMPT_CONFIG.get(template_key)
    if not template:
        raise ValueError(f"Prompt template '{template_key}' not found in {PROMPTS_FILE}")
    kwargs.setdefault("tag_line", get_show_tag_line(show))
    return template.format(**kwargs)


def get_prompt_for_prize(prize: str, show: str | None = None) -> str:
    p = prize.strip()

    if p == UNKNOWN_PRIZE_LABEL:
        return render_prompt("unknown", show=show)

    if p.startswith("MOT Indicator"):
        duration = "week" if "Weekly" in p else "month"
        return render_prompt("indicator", show=show, duration=duration)

    FIRM_PROMPTS = {
        "Alpha Futures": ("alpha", True),
        "Funded Next": ("funded_next", True),
        "Tradeify": ("tradeify", True),
        "Lucid": ("lucid", True),
        "MFF": ("mff", True),
        "FFF": ("fff", True),
        "Apex": ("apex", False),
        "TPT": ("tpt", False),
    }

    for firm_prefix, (prompt_key, has_account_type) in FIRM_PROMPTS.items():
        if p.startswith(firm_prefix):
            remainder = p[len(firm_prefix):].strip()
            parts = remainder.split()
            if has_account_type and len(parts) >= 2:
                account_type = " ".join(parts[:-1])
                size = parts[-1]
                return render_prompt(prompt_key, show=show, size=size, account_type=account_type)
            elif not has_account_type and len(parts) >= 1:
                size = parts[-1]
                return render_prompt(prompt_key, show=show, size=size)

    return (
        f"🎉 **Congratulations!**\n\n"
        f"You won **{prize}**.\n\n"
        "Please read the instructions below and reply in this ticket once you've completed the required steps."
    )


# =========================
# DATABASE WINNERS
# =========================
def _row_to_entry(row: dict) -> dict:
    return {
        "timestamp": row.get("timestamp", ""),
        "bundle_id": row.get("bundle_id"),
        "user": row.get("user_name", ""),
        "user_id": row.get("user_id"),
        "source": row.get("source", "discord"),
        "show": row.get("show", "Unknown"),
        "prize": row.get("prize"),
        "code": row.get("code"),
        "mod": row.get("mod", ""),
        "mod_id": row.get("mod_id", ""),
        "channel": row.get("channel", ""),
        "server": row.get("server", ""),
        "status": row.get("status", "ticket_created"),
        "type": row.get("type"),
        "reason": row.get("reason"),
        "notes": row.get("notes"),
        "ticket_channel_id": row.get("ticket_channel_id"),
        "ticket_channel_name": row.get("ticket_channel_name"),
        "backend_message_id": row.get("backend_message_id"),
        "prompt_message_id": row.get("prompt_message_id"),
        "header_message_id": row.get("header_message_id"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "updated_by_id": row.get("updated_by_id"),
        "completed_at": row.get("completed_at"),
        "history": row.get("history") or [],
        "prize_catalog_id": row.get("prize_catalog_id"),
        "prop_firm_id": row.get("prop_firm_id"),
        "account_type_id": row.get("account_type_id"),
        "account_size_id": row.get("account_size_id"),
    }


def load_data() -> dict:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM winners ORDER BY id ASC")
                rows = cur.fetchall()
        return {"winners": [_row_to_entry(dict(r)) for r in rows]}
    except Exception as e:
        print(f"[DB ERROR] load_data: {e}")
        return {"winners": []}


def save_data(data: dict):
    try:
        entries = data.get("winners", [])
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, bundle_id, user_id, prize FROM winners")
                existing = {}
                for row in cur.fetchall():
                    key = (row["bundle_id"], row["user_id"], row["prize"])
                    existing[key] = row["id"]

                for entry in entries:
                    key = (entry.get("bundle_id"), entry.get("user_id"), entry.get("prize"))
                    history = json.dumps(entry.get("history") or [])

                    if key in existing:
                        cur.execute("""
                            UPDATE winners SET
                                status = %s,
                                updated_at = %s,
                                updated_by = %s,
                                updated_by_id = %s,
                                completed_at = %s,
                                backend_message_id = %s,
                                prompt_message_id = %s,
                                header_message_id = %s,
                                prize = %s,
                                history = %s,
                                prize_catalog_id = %s,
                                prop_firm_id = %s,
                                account_type_id = %s,
                                account_size_id = %s
                            WHERE id = %s
                        """, (
                            entry.get("status"),
                            entry.get("updated_at"),
                            entry.get("updated_by"),
                            entry.get("updated_by_id"),
                            entry.get("completed_at"),
                            entry.get("backend_message_id"),
                            entry.get("prompt_message_id"),
                            entry.get("header_message_id"),
                            entry.get("prize"),
                            history,
                            entry.get("prize_catalog_id"),
                            entry.get("prop_firm_id"),
                            entry.get("account_type_id"),
                            entry.get("account_size_id"),
                            existing[key]
                        ))
                    else:
                        cur.execute("""
                            INSERT INTO winners (
                                bundle_id, timestamp, user_name, user_id, source, show,
                                prize, code, mod, mod_id, channel, server, status, type,
                                reason, notes, ticket_channel_id, ticket_channel_name,
                                backend_message_id, prompt_message_id, header_message_id,
                                updated_at, updated_by, updated_by_id, completed_at, history,
                                prize_catalog_id, prop_firm_id, account_type_id, account_size_id
                            ) VALUES (
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                            )
                        """, (
                            entry.get("bundle_id"),
                            entry.get("timestamp"),
                            entry.get("user"),
                            entry.get("user_id"),
                            entry.get("source"),
                            entry.get("show"),
                            entry.get("prize"),
                            entry.get("code"),
                            entry.get("mod"),
                            entry.get("mod_id"),
                            entry.get("channel"),
                            entry.get("server"),
                            entry.get("status"),
                            entry.get("type"),
                            entry.get("reason"),
                            entry.get("notes"),
                            entry.get("ticket_channel_id"),
                            entry.get("ticket_channel_name"),
                            entry.get("backend_message_id"),
                            entry.get("prompt_message_id"),
                            entry.get("header_message_id"),
                            entry.get("updated_at"),
                            entry.get("updated_by"),
                            entry.get("updated_by_id"),
                            entry.get("completed_at"),
                            history,
                            entry.get("prize_catalog_id"),
                            entry.get("prop_firm_id"),
                            entry.get("account_type_id"),
                            entry.get("account_size_id"),
                        ))
            conn.commit()
    except Exception as e:
        print(f"[DB ERROR] save_data: {e}")


# =========================
# TRANSCRIPT HELPERS
# =========================
def extract_user_from_channel(channel: discord.TextChannel, guild: discord.Guild) -> tuple[str | None, str | None]:
    if channel.topic and channel.topic.startswith("user_id:"):
        raw_id = channel.topic.split("user_id:")[1].strip()
        if raw_id.isdigit():
            member = guild.get_member(int(raw_id))
            if member:
                return str(member.id), member.name
            return raw_id, None
    mod_role = guild.get_role(MOD_ROLE_ID)
    skip_ids = {guild.me.id if guild.me else None, mod_role.id if mod_role else None} - {None}
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
            continue
        if target.id in skip_ids:
            continue
        member = guild.get_member(target.id)
        if member:
            if member.bot or user_is_mod(member):
                continue
            return str(member.id), member.name
        return str(target.id), None
    return None, None


async def save_transcript(
    channel: discord.TextChannel,
    ticket_type: str,
    deleted_by: discord.Member,
    bundle_id: str | None = None,
    user_id: str | None = None,
    user_name: str | None = None
):
    try:
        if user_id and not user_name:
            guild = channel.guild
            member = guild.get_member(int(user_id))
            if member:
                user_name = member.name
            else:
                try:
                    fetched = await guild.fetch_member(int(user_id))
                    user_name = fetched.name
                except Exception:
                    user_name = f"user_{user_id}"
        messages = []
        async for msg in channel.history(limit=None, oldest_first=True):
            messages.append({
                "author": str(msg.author),
                "author_id": str(msg.author.id),
                "content": msg.content,
                "timestamp": msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "attachments": [a.url for a in msg.attachments],
                "embeds": len(msg.embeds) > 0
            })
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO transcripts (
                        channel_id, channel_name, ticket_type, bundle_id,
                        user_id, user_name, deleted_by, deleted_at, messages
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    str(channel.id),
                    channel.name,
                    ticket_type,
                    bundle_id,
                    user_id,
                    user_name,
                    str(deleted_by),
                    datetime.now(),
                    json.dumps(messages)
                ))
            conn.commit()
        print(f"[TRANSCRIPT] Saved {len(messages)} messages from #{channel.name} (user_id={user_id})")
    except Exception as e:
        print(f"[TRANSCRIPT ERROR] save_transcript: {e}")


def fetch_transcripts_for_user(user_id: str) -> list[dict]:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, channel_name, ticket_type, bundle_id,
                           user_name, deleted_by, deleted_at
                    FROM transcripts
                    WHERE user_id = %s
                    ORDER BY deleted_at DESC
                """, (user_id,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[TRANSCRIPT ERROR] fetch_transcripts_for_user: {e}")
        return []


def fetch_transcript_messages(transcript_id: int) -> dict:
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT messages, channel_name, deleted_at FROM transcripts WHERE id = %s", (transcript_id,))
                row = cur.fetchone()
                return dict(row) if row else {}
    except Exception as e:
        print(f"[TRANSCRIPT ERROR] fetch_transcript_messages: {e}")
        return {}


# =========================
# UTILS
# =========================
def safe_channel_name(prefix: str, user_name: str, label: str = "") -> str:
    parts = [prefix, user_name]
    if label:
        parts.insert(1, label)
    base = "-".join([p for p in parts if p])
    base = base.lower().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    cleaned = "".join(c for c in base if c in allowed)
    return cleaned[:90]


def is_giveaway_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if channel.category_id != GIVEAWAY_CATEGORY_ID:
        return False
    return channel.name.startswith("winner-") or channel.name.startswith("closed-winner-")


def is_manual_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if channel.category_id != SUPPORT_CATEGORY_ID:
        return False
    return channel.name.startswith("manual-") or channel.name.startswith("closed-manual-")


def is_support_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if channel.category_id != SUPPORT_CATEGORY_ID:
        return False
    return (
        channel.name.startswith("support-") or
        channel.name.startswith("closed-support-") or
        channel.name.startswith("manual-") or
        channel.name.startswith("closed-manual-") or
        channel.name.startswith("prize-") or
        channel.name.startswith("closed-prize-")
    )


def is_bot_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    return is_giveaway_ticket_channel(channel) or is_manual_ticket_channel(channel)


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def user_is_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id == MOD_ROLE_ID for role in member.roles)


async def ensure_mod(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
        if interaction.response.is_done():
            await interaction.followup.send("❌ Only mods can use this command.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Only mods can use this command.", ephemeral=True)
        return False
    return True


def get_bundle_id_from_channel(channel: discord.TextChannel) -> str | None:
    parts = channel.name.split("-")
    for part in parts:
        if len(part) == 8:
            return part
    return None


active_winner_locks: dict[int, asyncio.Lock] = {}
ticket_creation_in_progress: set[int] = set()
escalation_cooldowns: dict[tuple[int, int], datetime] = {}
ESCALATION_COOLDOWN_MINUTES = 10
claim_prize_submitted: set[tuple[int, int]] = set()
button_cooldowns: dict[int, datetime] = {}
BUTTON_COOLDOWN_SECONDS = 2


def is_button_rate_limited(user_id: int) -> bool:
    now = datetime.now()
    last = button_cooldowns.get(user_id)
    if last and (now - last).total_seconds() < BUTTON_COOLDOWN_SECONDS:
        return True
    button_cooldowns[user_id] = now
    return False


def get_winner_lock(user_id: int) -> asyncio.Lock:
    if user_id not in active_winner_locks:
        active_winner_locks[user_id] = asyncio.Lock()
    return active_winner_locks[user_id]


def find_open_ticket_for_user(guild: discord.Guild, user: discord.Member) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if not is_bot_ticket_channel(channel):
            continue
        if channel.category_id != GIVEAWAY_CATEGORY_ID:
            continue
        if channel.name.startswith("closed-"):
            continue
        if user in channel.overwrites:
            return channel
    return None


def find_open_support_ticket_for_user(guild: discord.Guild, user: discord.Member) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if not is_support_ticket_channel(channel):
            continue
        if channel.name.startswith("closed-"):
            continue
        if user in channel.overwrites:
            return channel
    return None


def find_any_open_ticket_for_user(guild: discord.Guild, user: discord.Member) -> discord.TextChannel | None:
    return find_open_ticket_for_user(guild, user) or find_open_support_ticket_for_user(guild, user)


def get_support_ticket_id() -> str:
    return uuid.uuid4().hex[:6]


async def silent_mod_ping(channel: discord.TextChannel, guild: discord.Guild, message: str):
    await channel.send(message)


def format_backend_log_line(
    winner_name: str,
    source: str,
    prize_text: str,
    code: str | None = None,
    show: str | None = None
) -> str:
    src_label = "(YT)" if source.lower() == "youtube" else source.lower()
    parts = [winner_name, src_label]
    if show:
        parts.append(show)
    parts.append(prize_text)
    if code:
        parts.append(code)
    return " - ".join(parts)


async def send_long_message(channel: discord.TextChannel, content: str, view=None):
    max_len = 2000
    if len(content) <= max_len:
        await channel.send(content, view=view)
        return
    chunks = []
    remaining = content
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1 and view is not None:
            await channel.send(chunk, view=view)
        else:
            await channel.send(chunk)


# =========================
# BACKEND LOG HELPERS
# =========================
async def post_backend_log(lines: list[str]) -> discord.Message | None:
    channel = bot.get_channel(BACKEND_LOG_CHANNEL_ID)
    if channel and isinstance(channel, discord.TextChannel):
        return await channel.send("\n".join(lines))
    return None


async def mark_backend_log_completed(bundle_id: str):
    data = load_data()
    log_channel = bot.get_channel(BACKEND_LOG_CHANNEL_ID)
    if not log_channel or not isinstance(log_channel, discord.TextChannel):
        return
    updated_any = False
    for entry in data["winners"]:
        if entry.get("bundle_id") != bundle_id:
            continue
        message_id = entry.get("backend_message_id")
        if message_id:
            try:
                message = await log_channel.fetch_message(int(message_id))
                if "COMPLETED ✅" not in message.content:
                    await message.edit(content=message.content + " | COMPLETED ✅")
            except Exception as e:
                print(f"Failed to update backend log message {message_id}: {e}")
        if entry.get("status") != "completed":
            entry["status"] = "completed"
            entry["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_any = True
    if updated_any:
        save_data(data)


async def rebuild_backend_log_for_bundle(bundle_id: str) -> tuple[bool, str | None]:
    data = load_data()
    log_channel = bot.get_channel(BACKEND_LOG_CHANNEL_ID)
    if not log_channel or not isinstance(log_channel, discord.TextChannel):
        return False, "Backend log channel not found."
    bundle_entries = [e for e in data["winners"] if e.get("bundle_id") == bundle_id]
    if not bundle_entries:
        return False, "No winner entries found for this ticket."
    first = bundle_entries[0]
    backend_message_id = first.get("backend_message_id")
    if not backend_message_id:
        return False, "No backend message ID found for this ticket."
    winner_name = first.get("user", "Unknown")
    source = first.get("source", "discord")
    code = first.get("code")
    show = first.get("show")
    prize_list = [e.get("prize", "Unknown Prize") for e in bundle_entries]
    combined_prizes = " & ".join(prize_list)
    updated_line = format_backend_log_line(winner_name, source, combined_prizes, code, show)
    notes = first.get("notes")
    if notes:
        updated_line += f" | Notes: {notes}"
    any_completed = any(e.get("status") == "completed" for e in bundle_entries)
    if any_completed:
        updated_line += " | COMPLETED ✅"
    try:
        message = await log_channel.fetch_message(int(backend_message_id))
        await message.edit(content=updated_line)
        return True, None
    except Exception as e:
        return False, str(e)


async def edit_ticket_prompt_message(
    channel: discord.TextChannel,
    bundle_entries: list[dict],
    user_mention: str
) -> tuple[bool, str | None]:
    if not bundle_entries:
        return False, "No bundle entries found."
    first = bundle_entries[0]
    prompt_message_id = first.get("prompt_message_id")
    header_message_id = first.get("header_message_id")
    if not prompt_message_id:
        return False, "No prompt_message_id found for this ticket."
    updated_prizes = [e.get("prize", "Unknown Prize") for e in bundle_entries]
    show = first.get("show")
    code = first.get("code")
    if len(updated_prizes) == 1:
        new_header = f"{user_mention}\n\n**Prize:** {updated_prizes[0]}\n"
    else:
        new_header = f"{user_mention}\n\n**Prizes Won:**\n" + "\n".join([f"- {p}" for p in updated_prizes]) + "\n"
    if show:
        new_header += f"**Show:** {show}\n"
    if code:
        new_header += f"**Code:** `{code}`\n"
    prompt_blocks = []
    for prize in updated_prizes:
        if len(updated_prizes) == 1:
            prompt_blocks.append(get_prompt_for_prize(prize, show=show))
        else:
            prompt_blocks.append(f"## {prize}\n{get_prompt_for_prize(prize, show=show)}")
    prompt_body = "\n\n---\n\n".join(prompt_blocks)
    try:
        if header_message_id:
            header_msg = await channel.fetch_message(int(header_message_id))
            await header_msg.edit(content=new_header)
        prompt_msg = await channel.fetch_message(int(prompt_message_id))
        await prompt_msg.edit(content=prompt_body[:2000])
        return True, None
    except Exception as e:
        return False, str(e)


async def send_temp_confirmation(interaction: discord.Interaction, content: str, seconds: int = 5):
    msg = await interaction.followup.send(content, ephemeral=True, wait=True)
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


# =========================
# DATA / STATUS HELPERS
# =========================
def find_entries_for_channel(channel: discord.TextChannel) -> list[dict]:
    data = load_data()
    bundle_id = get_bundle_id_from_channel(channel)
    if bundle_id:
        entries = [e for e in data["winners"] if e.get("bundle_id") == bundle_id]
        if entries:
            return entries
    entries = [
        e for e in data["winners"]
        if str(e.get("ticket_channel_id", "")) == str(channel.id)
    ]
    if entries:
        return entries
    entries = [
        e for e in data["winners"]
        if e.get("ticket_channel_name") == channel.name and e.get("status") != "completed"
    ]
    return entries


async def mark_channel_entries_completed(channel: discord.TextChannel):
    data = load_data()
    bundle_id = get_bundle_id_from_channel(channel)
    updated_any = False
    for entry in data["winners"]:
        matches_bundle = bundle_id and entry.get("bundle_id") == bundle_id
        matches_channel_id = str(entry.get("ticket_channel_id", "")) == str(channel.id)
        matches_legacy_name = entry.get("ticket_channel_name") == channel.name
        if matches_bundle or matches_channel_id or matches_legacy_name:
            if entry.get("status") != "completed":
                entry["status"] = "completed"
                entry["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated_any = True
    if updated_any:
        save_data(data)
    if bundle_id:
        await mark_backend_log_completed(bundle_id)


# =========================
# UPDATE PRIZE — CHAINED DROPDOWN
# =========================
async def apply_prize_update_to_db(
    channel: discord.TextChannel,
    old_prize: str,
    new_prize: str,
    moderator: discord.Member,
    resolved: dict | None = None
) -> tuple[bool, str | None]:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_entries = find_entries_for_channel(channel)
    target_entry = next((e for e in bundle_entries if e.get("prize") == old_prize), None)
    if not target_entry:
        return False, f"Prize **{old_prize}** not found in this ticket."
    history = target_entry.get("history") or []
    history.append({
        "action": "prize_updated",
        "old_prize": old_prize,
        "new_prize": new_prize,
        "by": moderator.name,
        "by_id": str(moderator.id),
        "timestamp": now_str
    })
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE winners SET
                        prize = %s,
                        updated_at = %s,
                        updated_by = %s,
                        updated_by_id = %s,
                        history = %s,
                        prize_catalog_id = %s,
                        prop_firm_id = %s,
                        account_type_id = %s,
                        account_size_id = %s
                    WHERE bundle_id = %s
                      AND prize = %s
                """, (
                    new_prize,
                    now_str,
                    moderator.name,
                    str(moderator.id),
                    json.dumps(history),
                    resolved.get("prize_catalog_id") if resolved else None,
                    resolved.get("prop_firm_id") if resolved else None,
                    resolved.get("account_type_id") if resolved else None,
                    resolved.get("account_size_id") if resolved else None,
                    target_entry.get("bundle_id"),
                    old_prize,
                ))
                rows_updated = cur.rowcount
            conn.commit()
        if rows_updated == 0:
            return False, "No rows matched — prize may have already been updated or bundle ID mismatch."
        return True, None
    except Exception as e:
        print(f"[DB ERROR] apply_prize_update_to_db: {e}")
        return False, str(e)


class UpdatePrizeFirmSelect(discord.ui.Select):
    def __init__(self, old_prize: str, firms: list[dict]):
        options = [
            discord.SelectOption(label=f["name"], value=f["name"])
            for f in firms[:25]
        ]
        super().__init__(placeholder="Select NEW prop firm", min_values=1, max_values=1, options=options)
        self.old_prize = old_prize

    async def callback(self, interaction: discord.Interaction):
        firm = self.values[0]
        types = get_account_types_for_firm(firm)
        if not types:
            await interaction.response.send_message("❌ No account types found for that firm.", ephemeral=True)
            return
        view = UpdatePrizeTypeView(old_prize=self.old_prize, firm=firm, types=types)
        await interaction.response.edit_message(
            content=f"**Update Prize**\nFirm: **{firm}**\n\nNow select the account type:",
            view=view
        )


class UpdatePrizeFirmView(discord.ui.View):
    def __init__(self, old_prize: str, firms: list[dict]):
        super().__init__(timeout=120)
        self.add_item(UpdatePrizeFirmSelect(old_prize=old_prize, firms=firms))


class UpdatePrizeTypeSelect(discord.ui.Select):
    def __init__(self, old_prize: str, firm: str, types: list[dict]):
        options = [
            discord.SelectOption(label=t["name"], value=t["name"])
            for t in types[:25]
        ]
        super().__init__(placeholder="Select account type", min_values=1, max_values=1, options=options)
        self.old_prize = old_prize
        self.firm = firm

    async def callback(self, interaction: discord.Interaction):
        account_type = self.values[0]
        sizes = get_sizes_for_firm_and_type(self.firm, account_type)
        if not sizes:
            await interaction.response.send_message("❌ No sizes found for that firm and account type.", ephemeral=True)
            return
        view = UpdatePrizeSizeView(
            old_prize=self.old_prize,
            firm=self.firm,
            account_type=account_type,
            sizes=sizes
        )
        await interaction.response.edit_message(
            content=f"**Update Prize**\nFirm: **{self.firm}** | Type: **{account_type}**\n\nNow select the account size:",
            view=view
        )


class UpdatePrizeTypeView(discord.ui.View):
    def __init__(self, old_prize: str, firm: str, types: list[dict]):
        super().__init__(timeout=120)
        self.add_item(UpdatePrizeTypeSelect(old_prize=old_prize, firm=firm, types=types))


class UpdatePrizeSizeSelect(discord.ui.Select):
    def __init__(self, old_prize: str, firm: str, account_type: str, sizes: list[dict]):
        options = [
            discord.SelectOption(label=s["label"], value=s["label"])
            for s in sizes[:25]
        ]
        super().__init__(placeholder="Select account size", min_values=1, max_values=1, options=options)
        self.old_prize = old_prize
        self.firm = firm
        self.account_type = account_type

    async def callback(self, interaction: discord.Interaction):
        size = self.values[0]
        resolved = resolve_prize_from_catalog(self.firm, self.account_type, size)
        if not resolved:
            await interaction.response.send_message(
                f"❌ Could not resolve prize for {self.firm} / {self.account_type} / {size}.",
                ephemeral=True
            )
            return
        new_prize = resolved["display_name"]
        view = UpdatePrizeConfirmView(
            old_prize=self.old_prize,
            new_prize=new_prize,
            resolved=resolved
        )
        await interaction.response.edit_message(
            content=(
                f"**Update Prize — Confirm**\n\n"
                f"**From:** {self.old_prize}\n"
                f"**To:** {new_prize}\n\n"
                "Click **Confirm** to apply this change."
            ),
            view=view
        )


class UpdatePrizeSizeView(discord.ui.View):
    def __init__(self, old_prize: str, firm: str, account_type: str, sizes: list[dict]):
        super().__init__(timeout=120)
        self.add_item(UpdatePrizeSizeSelect(
            old_prize=old_prize, firm=firm, account_type=account_type, sizes=sizes
        ))


class UpdatePrizeConfirmView(discord.ui.View):
    def __init__(self, old_prize: str, new_prize: str, resolved: dict):
        super().__init__(timeout=120)
        self.old_prize = old_prize
        self.new_prize = new_prize
        self.resolved = resolved

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can update prizes.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Invalid channel.", ephemeral=True)
            return
        if self.old_prize == self.new_prize:
            await interaction.response.edit_message(content="❌ Old and new prize are the same.", view=None)
            return
        await interaction.response.edit_message(content="✅ Applying update...", view=None)
        success, error = await apply_prize_update_to_db(
            channel=channel,
            old_prize=self.old_prize,
            new_prize=self.new_prize,
            moderator=interaction.user,
            resolved=self.resolved
        )
        if not success:
            await interaction.followup.send(f"❌ DB update failed: `{error}`", ephemeral=True)
            return
        bundle_entries = find_entries_for_channel(channel)
        bundle_id = bundle_entries[0].get("bundle_id") if bundle_entries else None
        log_success, log_error = await rebuild_backend_log_for_bundle(bundle_id) if bundle_id else (True, None)
        user_id = bundle_entries[0].get("user_id") if bundle_entries else None
        user_mention = f"<@{user_id}>" if user_id else "Winner"
        prompt_success, prompt_error = await edit_ticket_prompt_message(
            channel=channel,
            bundle_entries=find_entries_for_channel(channel),
            user_mention=user_mention
        )
        parts = [f"✅ Prize updated from **{self.old_prize}** to **{self.new_prize}**."]
        if not log_success:
            parts.append(f"⚠️ Backend log refresh failed: `{log_error}`")
        if not prompt_success:
            parts.append(f"⚠️ Prompt message not updated: `{prompt_error}`")
        await interaction.followup.send("\n".join(parts), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Update cancelled.", view=None)


# =========================
# BUTTON VIEWS — GIVEAWAY
# =========================
class DeleteConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can delete tickets.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_bot_ticket_channel(channel):
            await interaction.response.send_message("❌ This button only works inside this bot's ticket channels.", ephemeral=True)
            return
        await mark_channel_entries_completed(channel)
        bundle_id = get_bundle_id_from_channel(channel)
        entries = find_entries_for_channel(channel)
        uid = entries[0].get("user_id") if entries else None
        uname = entries[0].get("user") if entries else None
        ticket_type = "giveaway" if is_giveaway_ticket_channel(channel) else "manual"
        await save_transcript(channel, ticket_type, interaction.user, bundle_id, uid, uname)
        await interaction.response.send_message("🗑️ Ticket marked complete and deleting in 3 seconds.", ephemeral=False)
        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except Exception as e:
            try:
                await channel.send(f"❌ Failed to delete channel: `{e}`")
            except Exception:
                pass
            print(f"Delete failed: {e}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Delete cancelled.", view=None)


class GiveawayTicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="giveaway_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_bot_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside giveaway ticket channels.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Invalid user.", ephemeral=True)
            return
        member_can_close = user_is_mod(interaction.user) or interaction.user in channel.overwrites
        if not member_can_close:
            await interaction.response.send_message("❌ You cannot close this ticket.", ephemeral=True)
            return
        if channel.name.startswith("closed-"):
            await interaction.response.send_message("❌ This ticket is already closed.", ephemeral=True)
            return
        try:
            new_overwrites = {}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and not user_is_mod(target):
                    overwrite = discord.PermissionOverwrite.from_pair(overwrite.pair()[0], overwrite.pair()[1])
                    overwrite.send_messages = False
                new_overwrites[target] = overwrite
            new_name = f"closed-{channel.name}"
            await channel.edit(name=new_name, overwrites=new_overwrites, reason=f"Ticket closed by {interaction.user}")
            await interaction.response.send_message(
                f"🔒 Ticket closed by {interaction.user.mention}. If you need to reopen it, click **Reopen Ticket**.",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to close ticket: `{e}`", ephemeral=True)

    @discord.ui.button(label="Reopen Ticket", style=discord.ButtonStyle.success, emoji="🔓", custom_id="giveaway_reopen_ticket")
    async def reopen_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_bot_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside giveaway ticket channels.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Invalid user.", ephemeral=True)
            return
        member_can_reopen = user_is_mod(interaction.user) or interaction.user in channel.overwrites
        if not member_can_reopen:
            await interaction.response.send_message("❌ You cannot reopen this ticket.", ephemeral=True)
            return
        if not channel.name.startswith("closed-"):
            await interaction.response.send_message("❌ This ticket is not closed.", ephemeral=True)
            return
        try:
            new_overwrites = {}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and not user_is_mod(target):
                    overwrite = discord.PermissionOverwrite.from_pair(overwrite.pair()[0], overwrite.pair()[1])
                    overwrite.send_messages = True
                new_overwrites[target] = overwrite
            new_name = channel.name[len("closed-"):]
            await channel.edit(name=new_name, overwrites=new_overwrites, reason=f"Ticket reopened by {interaction.user}")
            await interaction.response.send_message(
                f"🔓 Ticket reopened by {interaction.user.mention}.",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to reopen ticket: `{e}`", ephemeral=True)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="giveaway_delete_ticket")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can delete tickets.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_bot_ticket_channel(channel):
            await interaction.response.send_message("❌ This button only works inside this bot's ticket channels.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Are you sure you want to delete this ticket? This will also mark it completed.",
            ephemeral=True,
            view=DeleteConfirmView()
        )

    @discord.ui.button(label="Update Prize", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="giveaway_update_prize")
    async def update_prize_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can update giveaway prizes.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_giveaway_ticket_channel(channel):
            await interaction.response.send_message("❌ Prize updates only work inside giveaway ticket channels.", ephemeral=True)
            return
        bundle_entries = find_entries_for_channel(channel)
        if not bundle_entries:
            await interaction.response.send_message("❌ No entries found for this ticket.", ephemeral=True)
            return
        old_prizes = dedupe_preserve_order([e.get("prize", "Unknown Prize") for e in bundle_entries])
        if len(old_prizes) == 1:
            old_prize = old_prizes[0]
            firms = get_active_prop_firms()
            if not firms:
                await interaction.response.send_message("❌ No active prop firms found in catalog.", ephemeral=True)
                return
            view = UpdatePrizeFirmView(old_prize=old_prize, firms=firms)
            await interaction.response.send_message(
                f"**Update Prize**\nReplacing: **{old_prize}**\n\nSelect the new prop firm:",
                view=view,
                ephemeral=True
            )
        else:
            view = OldPrizePickView(old_prizes=old_prizes)
            await interaction.response.send_message(
                "Select which prize to replace:",
                view=view,
                ephemeral=True
            )


class OldPrizePickSelect(discord.ui.Select):
    def __init__(self, old_prizes: list[str]):
        options = [discord.SelectOption(label=p, value=p) for p in old_prizes[:25]]
        super().__init__(placeholder="Select prize to replace", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        old_prize = self.values[0]
        firms = get_active_prop_firms()
        if not firms:
            await interaction.response.send_message("❌ No active prop firms found in catalog.", ephemeral=True)
            return
        view = UpdatePrizeFirmView(old_prize=old_prize, firms=firms)
        await interaction.response.edit_message(
            content=f"**Update Prize**\nReplacing: **{old_prize}**\n\nSelect the new prop firm:",
            view=view
        )


class OldPrizePickView(discord.ui.View):
    def __init__(self, old_prizes: list[str]):
        super().__init__(timeout=120)
        self.add_item(OldPrizePickSelect(old_prizes=old_prizes))

 # =========================
# FAQ VIEWS — MOD (send to channel)
# =========================

class ModFaqCategoryView(discord.ui.View):
    """Ephemeral category picker for the mod FAQ Tools button."""
    def __init__(self):
        super().__init__(timeout=120)
        categories = get_active_faq_categories()
        for i, cat in enumerate(categories[:20]):
            self.add_item(ModFaqCategoryButton(
                label=cat["name"],
                category_id=cat["id"],
                index=i
            ))


class ModFaqCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_id: int, index: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            row=min(index // 5, 3)
        )
        self.category_id = category_id

    async def callback(self, interaction: discord.Interaction):
        view = ModFaqAnswerView(category_id=self.category_id, category_name=self.label)
        await interaction.response.edit_message(
            content=f"**{self.label} FAQs** — click a question to send it to the ticket:",
            view=view
        )


class ModFaqAnswerView(discord.ui.View):
    """Ephemeral question picker for mods. Clicking sends the answer publicly."""
    def __init__(self, category_id: int, category_name: str):
        super().__init__(timeout=120)
        entries = get_faq_entries_by_category(category_id, visibility="public")
        for i, entry in enumerate(entries[:25]):
            label = entry["question"][:80]
            self.add_item(ModFaqSendButton(
                label=label,
                question=entry["question"],
                answer=entry["answer"],
                index=i
            ))


class ModFaqSendButton(discord.ui.Button):
    """Mod clicks → answer posts publicly as bold question + answer."""
    def __init__(self, label: str, question: str, answer: str, index: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=min(index // 5, 4)
        )
        self.question = question
        self.answer = answer

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can send FAQs to the ticket.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Must be used inside a ticket channel.", ephemeral=True)
            return
        message_content = f"**{self.question}**\n\n{self.answer}"
        await interaction.response.edit_message(
            content=f"✅ Sent **{self.question[:60]}** to the ticket.",
            view=None
        )
        if len(message_content) <= 2000:
            await channel.send(message_content)
        else:
            await channel.send(message_content[:2000])
            remaining = message_content[2000:]
            while remaining:
                await channel.send(remaining[:2000])
                remaining = remaining[2000:]       

# =========================
# FAQ VIEWS  (reads from Postgres)
# =========================

class FaqAnswerView(discord.ui.View):
    """
    Shown after a user clicks a category button.
    Loads question buttons from Postgres for that category.
    visibility='public'  → user-facing (ephemeral answer only)
    visibility='all'     → mod-facing (future: send-to-channel option)
    """
    def __init__(self, category_id: int, category_name: str, visibility: str = "public"):
        super().__init__(timeout=120)
        entries = get_faq_entries_by_category(category_id, visibility=visibility)
        for i, entry in enumerate(entries[:25]):
            label = entry["question"][:80]
            self.add_item(FaqQuestionButton(
                label=label,
                entry_id=entry["id"],
                answer=entry["answer"],
                escalate=entry["escalate"],
                index=i
            ))


class FaqQuestionButton(discord.ui.Button):
    def __init__(self, label: str, entry_id: int, answer: str, escalate: bool, index: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.danger if escalate else discord.ButtonStyle.secondary,
            row=min(index // 5, 4)
        )
        self.entry_id = entry_id
        self.answer = answer
        self.escalate = escalate

    async def callback(self, interaction: discord.Interaction):
        # Always ephemeral — user sees the answer only for themselves
        await interaction.response.send_message(self.answer[:2000], ephemeral=True)

        if self.escalate:
            channel = interaction.channel
            guild = interaction.guild
            if isinstance(channel, discord.TextChannel) and guild is not None:
                cooldown_key = (channel.id, interaction.user.id)
                last_escalation = escalation_cooldowns.get(cooldown_key)
                now = datetime.now()
                if last_escalation is not None:
                    elapsed = (now - last_escalation).total_seconds() / 60
                    remaining = ESCALATION_COOLDOWN_MINUTES - elapsed
                    if remaining > 0:
                        await channel.send(
                            f"{interaction.user.mention} A moderator has already been notified. "
                            f"Please wait **{int(remaining) + 1} more minute(s)** before escalating again.",
                            delete_after=15
                        )
                        return
                escalation_cooldowns[cooldown_key] = now
                await silent_mod_ping(
                    channel, guild,
                    f"{interaction.user.mention} needs assistance with: **{self.label}**"
                )


class NeedHelpButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="I still need help",
            style=discord.ButtonStyle.danger,
            emoji="🙋",
            custom_id="faq_need_help",
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        if is_button_rate_limited(interaction.user.id):
            await interaction.response.send_message("You're clicking too fast. Please wait a moment.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ This must be used inside a ticket channel.", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Must be used inside the server.", ephemeral=True)
            return
        cooldown_key = (channel.id, interaction.user.id)
        last_escalation = escalation_cooldowns.get(cooldown_key)
        now = datetime.now()
        if last_escalation is not None:
            elapsed = (now - last_escalation).total_seconds() / 60
            remaining = ESCALATION_COOLDOWN_MINUTES - elapsed
            if remaining > 0:
                await interaction.response.send_message(
                    f"A moderator has already been notified. "
                    f"Please wait **{int(remaining) + 1} more minute(s)** before requesting again.",
                    ephemeral=True
                )
                return
        escalation_cooldowns[cooldown_key] = now
        await interaction.response.send_message(
            "A moderator has been notified and will be with you shortly.",
            ephemeral=False
        )
        await silent_mod_ping(
            channel, guild,
            f"{interaction.user.mention} has requested assistance in this ticket."
        )


class ClaimPrizeModal(discord.ui.Modal, title="Claim My Prize"):
    where_platform = discord.ui.TextInput(
        label="Where did you win?",
        placeholder="YouTube / Instagram / TikTok / Twitter/X / Other",
        required=True,
        max_length=50
    )
    your_handle = discord.ui.TextInput(
        label="Your name or handle on that platform",
        placeholder="e.g. @yourhandle or John Smith",
        required=True,
        max_length=100
    )
    what_prize = discord.ui.TextInput(
        label="What did you win?",
        placeholder="Account / Indicator / Both / Unsure",
        required=True,
        max_length=50
    )
    extra_notes = discord.ui.TextInput(
        label=" ATTACH A SCREENSHOT after submitting!",
        placeholder="Any extra details? e.g. which show, what date, anything else",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message("❌ This must be used inside a ticket channel.", ephemeral=True)
            return
        summary = (
            f"**Prize Claim Submitted**\n\n"
            f"**Where they won:** {self.where_platform.value}\n"
            f"**Name/Handle:** {self.your_handle.value}\n"
            f"**What they won:** {self.what_prize.value}\n"
        )
        if self.extra_notes.value:
            summary += f"**Extra details:** {self.extra_notes.value}\n"
        summary += "\n\n**IMPORTANT: Please attach a screenshot of your handle/profile below. Without a screenshot your claim cannot be processed.**"
        await interaction.response.send_message(summary, ephemeral=False)
        await silent_mod_ping(
            channel, guild,
            f"{interaction.user.mention} is claiming a third-party prize. Please verify and assist."
        )


class ClaimPrizeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Claim My Prize",
            style=discord.ButtonStyle.success,
            custom_id="faq_claim_prize",
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        if is_button_rate_limited(interaction.user.id):
            await interaction.response.send_message("You're clicking too fast. Please wait a moment.", ephemeral=True)
            return
        channel = interaction.channel
        guild = interaction.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message("This must be used inside a ticket channel.", ephemeral=True)
            return
        cooldown_key = (channel.id, interaction.user.id)
        if cooldown_key in claim_prize_submitted:
            await interaction.response.send_message(
                "You have already submitted a prize claim in this ticket. A moderator will be with you shortly.",
                ephemeral=True
            )
            return
        claim_prize_submitted.add(cooldown_key)
        await interaction.response.send_modal(ClaimPrizeModal())


class FaqCategoryView(discord.ui.View):
    """
    The main FAQ embed sent into every new support ticket.
    Loads category list from Postgres. custom_id format preserved for open ticket compatibility.
    """
    def __init__(self):
        super().__init__(timeout=None)
        categories = get_active_faq_categories()
        for i, cat in enumerate(categories[:20]):
            self.add_item(FaqCategoryButton(
                label=cat["name"],
                category_id=cat["id"],
                index=i
            ))
        self.add_item(NeedHelpButton())
        self.add_item(ClaimPrizeButton())


class FaqCategoryButton(discord.ui.Button):
    """
    One button per FAQ category. custom_id uses category name (lowercase/underscored)
    to stay compatible with buttons already rendered in open tickets.
    """
    def __init__(self, label: str, category_id: int, index: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            # Keep same custom_id format as before so old ticket buttons still work
            custom_id=f"faq_cat_{label.lower().replace(' ', '_')}",
            row=min(index // 5, 3)
        )
        self.category_id = category_id

    async def callback(self, interaction: discord.Interaction):
        view = FaqAnswerView(category_id=self.category_id, category_name=self.label)
        await interaction.response.send_message(
            f"**{self.label} FAQs** — click a question to see the answer:",
            view=view,
            ephemeral=True
        )


# =========================
# BUTTON VIEWS — SUPPORT
# =========================
class SupportDeleteConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can delete support tickets.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_support_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside support tickets.", ephemeral=True)
            return
        guild = interaction.guild
        uid, uname = extract_user_from_channel(channel, guild) if guild else (None, None)
        await save_transcript(channel, "support", interaction.user, None, uid, uname)
        await interaction.response.send_message("🗑️ Deleting support ticket in 3 seconds.", ephemeral=False)
        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Support ticket deleted by {interaction.user}")
        except Exception as e:
            try:
                await channel.send(f"❌ Failed to delete support ticket: `{e}`")
            except Exception:
                pass
            print(f"Support delete failed: {e}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Delete cancelled.", view=None)


class SupportTicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="support_close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_support_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside support tickets.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Invalid user.", ephemeral=True)
            return
        member_can_close = user_is_mod(interaction.user) or interaction.user in channel.overwrites
        if not member_can_close:
            await interaction.response.send_message("❌ You cannot close this support ticket.", ephemeral=True)
            return
        try:
            new_overwrites = {}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and not user_is_mod(target):
                    overwrite = discord.PermissionOverwrite.from_pair(overwrite.pair()[0], overwrite.pair()[1])
                    overwrite.send_messages = False
                new_overwrites[target] = overwrite
            new_name = channel.name
            if not new_name.startswith("closed-"):
                new_name = f"closed-{new_name}"
            await channel.edit(name=new_name, overwrites=new_overwrites, reason=f"Support ticket closed by {interaction.user}")
            await interaction.response.send_message("This ticket has been closed.", ephemeral=False)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to close support ticket: `{e}`", ephemeral=True)

    @discord.ui.button(label="Reopen Ticket", style=discord.ButtonStyle.success, emoji="🔓", custom_id="support_reopen")
    async def reopen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_support_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside support tickets.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Invalid user.", ephemeral=True)
            return
        member_can_reopen = user_is_mod(interaction.user) or interaction.user in channel.overwrites
        if not member_can_reopen:
            await interaction.response.send_message("❌ You cannot reopen this ticket.", ephemeral=True)
            return
        if not channel.name.startswith("closed-"):
            await interaction.response.send_message("❌ This ticket is not closed.", ephemeral=True)
            return
        try:
            new_overwrites = {}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and not user_is_mod(target):
                    overwrite = discord.PermissionOverwrite.from_pair(overwrite.pair()[0], overwrite.pair()[1])
                    overwrite.send_messages = True
                new_overwrites[target] = overwrite
            new_name = channel.name[len("closed-"):]
            await channel.edit(name=new_name, overwrites=new_overwrites, reason=f"Support ticket reopened by {interaction.user}")
            await interaction.response.send_message(
                f"🔓 Ticket reopened by {interaction.user.mention}.",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to reopen ticket: `{e}`", ephemeral=True)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="support_delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not user_is_mod(interaction.user):
            await interaction.response.send_message("❌ Only mods can delete support tickets.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_support_ticket_channel(channel):
            await interaction.response.send_message("❌ This only works inside support tickets.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Are you sure you want to delete this support ticket?",
            ephemeral=True,
            view=SupportDeleteConfirmView()
        )
    @discord.ui.button(label="FAQ Tools", style=discord.ButtonStyle.primary, emoji="📋", custom_id="support_faq_tools")
    async def faq_tools_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Invalid user.", ephemeral=True)
            return
        if user_is_mod(interaction.user):
            view = ModFaqCategoryView()
            await interaction.response.send_message(
                "📋 **FAQ Tools** — Select a category, then click a question to send it to this ticket:",
                view=view,
                ephemeral=True
            )
        else:
            view = FaqCategoryView()
            await interaction.response.send_message(
                "**Frequently Asked Questions** — Select a category to browse questions:",
                view=view,
                ephemeral=True
            )


class OpenSupportTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Support Ticket", style=discord.ButtonStyle.primary, custom_id="open_support_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.response.is_done():
            return
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("This must be used inside the server.", ephemeral=True)
            return
        if is_button_rate_limited(user.id):
            await interaction.response.send_message("You're clicking too fast. Please wait a moment.", ephemeral=True)
            return
        if user.id in ticket_creation_in_progress:
            await interaction.response.send_message("Already opening a ticket for you, please wait.", ephemeral=True)
            return
        ticket_creation_in_progress.add(user.id)
        await interaction.response.send_message("Opening your ticket, one moment...", ephemeral=True)
        try:
            existing = find_any_open_ticket_for_user(guild, user)
            if existing is not None:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing.mention}\n"
                    "Please use your existing ticket or wait for it to be closed before opening a new one.",
                    ephemeral=True
                )
                return
            category = bot.get_channel(SUPPORT_CATEGORY_ID)
            if category is None:
                category = await bot.fetch_channel(SUPPORT_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                await interaction.followup.send("Support category is not configured correctly.", ephemeral=True)
                return
            mod_role = guild.get_role(MOD_ROLE_ID)
            if mod_role is None:
                await interaction.followup.send("Mod role not found. Check MOD_ROLE_ID.", ephemeral=True)
                return
            ticket_id = get_support_ticket_id()
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                mod_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            }
            if guild.me is not None:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
                )
            channel_name = safe_channel_name("support", ticket_id, user.name)
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"user_id:{user.id}",
                reason=f"Support ticket for {user}"
            )
            control_msg = await ticket_channel.send(
                "**Ticket Controls**\n\nUse the buttons below to manage this ticket.",
                view=SupportTicketControls()
            )
            try:
                await control_msg.pin()
            except Exception as e:
                print(f"Failed to pin support control message: {e}")
            await ticket_channel.send(
                f"Hello {user.mention},\n\n"
                "Thank you for reaching out. Before a moderator joins, please check if your question is already covered below — "
                "most common issues can be resolved instantly.\n\n"
                "If your question isn't listed, describe your issue in this channel and a moderator will assist you shortly."
            )
            await ticket_channel.send(
                "**Frequently Asked Questions**\n\n"
                "Select a category to browse questions. Click any question to see the answer.\n"
                "If you still need help after checking the FAQs, click **I still need help** to notify a moderator.\n"
                "If you won a prize outside of Discord, click **Claim My Prize**.",
                view=FaqCategoryView()
            )
            await interaction.edit_original_response(
                content=f"Your support ticket has been created: {ticket_channel.mention}"
            )
        finally:
            ticket_creation_in_progress.discard(user.id)


async def ensure_support_panel():
    await bot.wait_until_ready()
    channel = bot.get_channel(SUPPORT_PANEL_CHANNEL_ID)
    if not channel:
        print("Support panel channel not found.")
        return
    if not isinstance(channel, discord.TextChannel):
        print("Support panel channel is not a text channel.")
        return
    embed = discord.Embed(
        description="Open a support ticket & we will be with you shortly.\n\nBy clicking the button, a ticket will be opened for you. Please allow our team up to 24 hours to respond.",
        color=discord.Color.dark_green()
    )
    embed.set_image(url="https://media.discordapp.net/attachments/1488597507817734336/1492296608665305278/ff953ab5a9e55c01faf04c84390335eeae37f16dfb3561a63074c0ae3629c931.png?ex=69dad105&is=69d97f85&hm=3110298724a7e9bd5b1801b6da9140bea38192eef89ddced83fa87c6bc163ea0&=&format=webp&quality=lossless&width=1024&height=384")
    async for msg in channel.history(limit=20):
        if msg.author == bot.user and (msg.components or msg.embeds):
            if msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url:
                print("Support panel already up to date.")
                return
            try:
                await msg.delete()
            except Exception:
                pass
            break
    msg = await channel.send(embed=embed, view=OpenSupportTicketView())
    try:
        await msg.pin()
    except Exception as e:
        print(f"Failed to pin support panel message: {e}")
    print("Support panel created.")


# =========================
# BOT CLASS
# =========================
class GiveawayBot(commands.Bot):
    async def setup_hook(self):
        init_db()
        self.add_view(GiveawayTicketControls())
        self.add_view(SupportTicketControls())
        self.add_view(OpenSupportTicketView())
        self.add_view(FaqCategoryView())
        self.loop.create_task(self._safe_ensure_support_panel())
        self.loop.create_task(self._cleanup_loop())

    async def _safe_ensure_support_panel(self):
        try:
            await ensure_support_panel()
        except Exception as e:
            print(f"[ERROR] ensure_support_panel failed: {e}")

    async def _cleanup_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now = datetime.now()
                cutoff_buttons = 60
                cutoff_escalations = 3600
                stale = [uid for uid, t in button_cooldowns.items()
                         if (now - t).total_seconds() > cutoff_buttons]
                for uid in stale:
                    button_cooldowns.pop(uid, None)
                stale = [k for k, t in escalation_cooldowns.items()
                         if (now - t).total_seconds() > cutoff_escalations]
                for k in stale:
                    escalation_cooldowns.pop(k, None)
                stale = [uid for uid in list(active_winner_locks.keys())
                         if uid not in ticket_creation_in_progress]
                for uid in stale:
                    lock = active_winner_locks.get(uid)
                    if lock and not lock.locked():
                        active_winner_locks.pop(uid, None)
                if self.guilds:
                    existing_channel_ids = {c.id for g in self.guilds for c in g.text_channels}
                    stale = [k for k in list(claim_prize_submitted) if k[0] not in existing_channel_ids]
                    for k in stale:
                        claim_prize_submitted.discard(k)
            except Exception as e:
                print(f"[ERROR] cleanup_loop error: {e}")
            await asyncio.sleep(300)


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = GiveawayBot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# AUTOCOMPLETE
# =========================
async def prop_firm_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    if current.lower() in "unknown prize":
        choices.append(app_commands.Choice(name="Unknown Prize", value="Unknown Prize"))
    firms = get_active_prop_firms()
    choices += [
        app_commands.Choice(name=f["name"], value=f["name"])
        for f in firms
        if current.lower() in f["name"].lower()
    ]
    return choices[:25]


async def show_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=show, value=show)
        for show in SHOW_OPTIONS
        if current.lower() in show.lower()
    ][:25]


async def account_type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm or ""
    if not prop_firm or is_unknown_prize(prop_firm):
        return []
    types = get_account_types_for_firm(prop_firm)
    return [
        app_commands.Choice(name=t["name"], value=t["name"])
        for t in types
        if current.lower() in t["name"].lower()
    ][:25]


async def account_size_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm or ""
    account_type = interaction.namespace.account_type or ""
    if not prop_firm or not account_type or is_unknown_prize(prop_firm):
        return []
    sizes = get_sizes_for_firm_and_type(prop_firm, account_type)
    return [
        app_commands.Choice(name=s["label"], value=s["label"])
        for s in sizes
        if current.lower() in s["label"].lower()
    ][:25]


async def account_type_autocomplete_1(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_1 or ""
    if not prop_firm or is_unknown_prize(prop_firm):
        return []
    types = get_account_types_for_firm(prop_firm)
    return [app_commands.Choice(name=t["name"], value=t["name"]) for t in types if current.lower() in t["name"].lower()][:25]


async def account_size_autocomplete_1(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_1 or ""
    account_type = interaction.namespace.account_type_1 or ""
    if not prop_firm or not account_type or is_unknown_prize(prop_firm):
        return []
    sizes = get_sizes_for_firm_and_type(prop_firm, account_type)
    return [app_commands.Choice(name=s["label"], value=s["label"]) for s in sizes if current.lower() in s["label"].lower()][:25]


async def account_type_autocomplete_2(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_2 or ""
    if not prop_firm or is_unknown_prize(prop_firm):
        return []
    types = get_account_types_for_firm(prop_firm)
    return [app_commands.Choice(name=t["name"], value=t["name"]) for t in types if current.lower() in t["name"].lower()][:25]


async def account_size_autocomplete_2(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_2 or ""
    account_type = interaction.namespace.account_type_2 or ""
    if not prop_firm or not account_type or is_unknown_prize(prop_firm):
        return []
    sizes = get_sizes_for_firm_and_type(prop_firm, account_type)
    return [app_commands.Choice(name=s["label"], value=s["label"]) for s in sizes if current.lower() in s["label"].lower()][:25]


async def account_type_autocomplete_3(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_3 or ""
    if not prop_firm or is_unknown_prize(prop_firm):
        return []
    types = get_account_types_for_firm(prop_firm)
    return [app_commands.Choice(name=t["name"], value=t["name"]) for t in types if current.lower() in t["name"].lower()][:25]


async def account_size_autocomplete_3(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    prop_firm = interaction.namespace.prop_firm_3 or ""
    account_type = interaction.namespace.account_type_3 or ""
    if not prop_firm or not account_type or is_unknown_prize(prop_firm):
        return []
    sizes = get_sizes_for_firm_and_type(prop_firm, account_type)
    return [app_commands.Choice(name=s["label"], value=s["label"]) for s in sizes if current.lower() in s["label"].lower()][:25]


# =========================
# PRIZE RESOLUTION HELPER
# =========================
def resolve_prize(prop_firm: str, account_type: str, account_size: str) -> tuple[dict | None, str | None]:
    if is_unknown_prize(prop_firm):
        return make_unknown_resolved(), None
    resolved = resolve_prize_from_catalog(prop_firm, account_type, account_size)
    if not resolved:
        return None, (
            f"❌ No prize found for **{prop_firm} / {account_type} / {account_size}**. "
            "Make sure you selected from the autocomplete options."
        )
    return resolved, None


# =========================
# CORE TICKET / LOGGING
# =========================
async def create_giveaway_ticket_and_log(
    interaction: discord.Interaction,
    guild: discord.Guild,
    user: discord.Member,
    selected_prizes: list[str],
    quantity: int = 1,
    code: str | None = None,
    show: str | None = None,
    notes: str | None = None,
    prize_catalog_ids: list[int | None] | None = None,
    prop_firm_ids: list[int | None] | None = None,
    account_type_ids: list[int | None] | None = None,
    account_size_ids: list[int | None] | None = None,
) -> tuple[discord.TextChannel, str]:
    category = bot.get_channel(GIVEAWAY_CATEGORY_ID)
    if category is None:
        category = await bot.fetch_channel(GIVEAWAY_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError(f"Giveaway category is not a category. Got: {type(category)}")
    mod_role = guild.get_role(MOD_ROLE_ID)
    if mod_role is None:
        raise ValueError("Mod role not found. Check MOD_ROLE_ID.")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_id = uuid.uuid4().hex[:8]
    prize_text = f"{selected_prizes[0]} x{quantity}" if quantity > 1 else selected_prizes[0]
    combined_prizes = prize_text if len(selected_prizes) == 1 else " & ".join(selected_prizes)
    backend_line = format_backend_log_line(user.name, "discord", combined_prizes, code, show)
    if notes:
        backend_line += f" | Notes: {notes}"
    backend_msg = await post_backend_log([backend_line])
    backend_message_id = str(backend_msg.id) if backend_msg else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        mod_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
        )
    ticket_name = safe_channel_name("winner", bundle_id, user.name)
    ticket_channel = await guild.create_text_channel(
        name=ticket_name,
        category=category,
        overwrites=overwrites,
        reason=f"Giveaway ticket for {user} - {', '.join(selected_prizes)}"
    )
    prompt_blocks = []
    for prize in selected_prizes:
        if len(selected_prizes) == 1:
            prompt_blocks.append(get_prompt_for_prize(prize, show=show))
        else:
            prompt_blocks.append(f"## {prize}\n{get_prompt_for_prize(prize, show=show)}")
    prompt_body = "\n\n---\n\n".join(prompt_blocks)
    header = f"{user.mention}\n\n"
    if len(selected_prizes) == 1:
        qty_text = f" (x{quantity})" if quantity > 1 else ""
        header += f"**Prize:** {selected_prizes[0]}{qty_text}\n"
    else:
        header += "**Prizes Won:**\n" + "\n".join([f"- {p}" for p in selected_prizes]) + "\n"
    if show:
        header += f"**Show:** {show}\n"
    if code:
        header += f"**Code:** `{code}`\n"
    control_msg = await ticket_channel.send(
        "🎟️ **Giveaway Ticket Controls**\n\nUse the buttons below to manage this ticket.",
        view=GiveawayTicketControls()
    )
    try:
        await control_msg.pin()
    except Exception as e:
        print(f"Failed to pin giveaway control message: {e}")
    header_sent = await ticket_channel.send(header)
    header_message_id = str(header_sent.id)
    prompt_message_id = None
    if len(prompt_body) <= 2000:
        prompt_message = await ticket_channel.send(prompt_body)
        prompt_message_id = str(prompt_message.id)
    else:
        chunks = []
        remaining = prompt_body
        while len(remaining) > 2000:
            split_at = remaining.rfind("\n", 0, 2000)
            if split_at == -1:
                split_at = 2000
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        first_msg = await ticket_channel.send(chunks[0])
        prompt_message_id = str(first_msg.id)
        for chunk in chunks[1:]:
            await ticket_channel.send(chunk)
    data = load_data()
    for i, prize in enumerate(selected_prizes):
        entry = {
            "timestamp": timestamp,
            "bundle_id": bundle_id,
            "user": user.name,
            "user_id": str(user.id),
            "source": "discord",
            "show": show or "Unknown",
            "prize": prize,
            "code": code,
            "mod": interaction.user.name,
            "mod_id": str(interaction.user.id),
            "channel": interaction.channel.name if interaction.channel else "Unknown",
            "server": guild.name,
            "status": "ticket_created",
            "type": "giveaway",
            "notes": notes,
            "ticket_channel_id": str(ticket_channel.id),
            "ticket_channel_name": ticket_channel.name,
            "backend_message_id": backend_message_id,
            "prompt_message_id": prompt_message_id,
            "header_message_id": header_message_id,
            "prize_catalog_id": prize_catalog_ids[i] if prize_catalog_ids and i < len(prize_catalog_ids) else None,
            "prop_firm_id": prop_firm_ids[i] if prop_firm_ids and i < len(prop_firm_ids) else None,
            "account_type_id": account_type_ids[i] if account_type_ids and i < len(account_type_ids) else None,
            "account_size_id": account_size_ids[i] if account_size_ids and i < len(account_size_ids) else None,
        }
        data["winners"].append(entry)
    save_data(data)
    return ticket_channel, bundle_id


async def create_manual_ticket(
    interaction: discord.Interaction,
    guild: discord.Guild,
    user: discord.Member,
    reason: str
) -> discord.TextChannel:
    category = bot.get_channel(SUPPORT_CATEGORY_ID)
    if category is None:
        category = await bot.fetch_channel(SUPPORT_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError(f"Support category is not a category. Got: {type(category)}")
    mod_role = guild.get_role(MOD_ROLE_ID)
    if mod_role is None:
        raise ValueError("Mod role not found. Check MOD_ROLE_ID.")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        mod_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
        )
    ticket_name = safe_channel_name("manual", user.name)
    ticket_channel = await guild.create_text_channel(
        name=ticket_name,
        category=category,
        overwrites=overwrites,
        topic=f"user_id:{user.id}",
        reason=f"Manual ticket for {user} - {reason}"
    )
    opening_message = (
        f"Hello {user.mention}, this private ticket was opened by the moderation team.\n\n"
        f"**Reason:** {reason}\n\n"
        "Please reply here and a staff member will assist you."
    )
    control_msg = await ticket_channel.send(
        "🎟️ **Ticket Controls**\n\nUse the buttons below to manage this ticket.",
        view=SupportTicketControls()
    )
    try:
        await control_msg.pin()
    except Exception as e:
        print(f"Failed to pin manual ticket control message: {e}")
    sent_message = await ticket_channel.send(opening_message)
    data = load_data()
    entry = {
        "timestamp": timestamp,
        "bundle_id": None,
        "user": user.name,
        "user_id": str(user.id),
        "source": "manual",
        "show": "Manual Ticket",
        "prize": None,
        "code": None,
        "mod": interaction.user.name,
        "mod_id": str(interaction.user.id),
        "channel": interaction.channel.name if interaction.channel else "Unknown",
        "server": guild.name,
        "status": "ticket_created",
        "type": "manual",
        "reason": reason,
        "ticket_channel_id": str(ticket_channel.id),
        "ticket_channel_name": ticket_channel.name,
        "prompt_message_id": str(sent_message.id),
        "backend_message_id": None,
        "prize_catalog_id": None,
        "prop_firm_id": None,
        "account_type_id": None,
        "account_size_id": None,
    }
    data["winners"].append(entry)
    save_data(data)
    return ticket_channel


# =========================
# COMMANDS
# =========================
@tree.command(name="win", description="Log one Discord winner and create a ticket", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    user="Winner",
    prop_firm="Prop firm — or select 'Unknown Prize' if prize is not yet known",
    account_type="Account type (skip if Unknown Prize)",
    account_size="Account size (skip if Unknown Prize)",
    quantity="Number of this prize won (default 1)",
    code="Optional giveaway code",
    show="Show the prize was won on",
    notes="Optional notes"
)
@app_commands.autocomplete(
    prop_firm=prop_firm_autocomplete,
    account_type=account_type_autocomplete,
    account_size=account_size_autocomplete,
    show=show_autocomplete
)
async def winner(
    interaction: discord.Interaction,
    user: discord.Member,
    prop_firm: str,
    account_type: str = "",
    account_size: str = "",
    quantity: int = 1,
    code: str | None = None,
    show: str | None = None,
    notes: str | None = None
):
    if not await ensure_mod(interaction):
        return
    if quantity < 1 or quantity > 10:
        await interaction.response.send_message("❌ Quantity must be between 1 and 10.", ephemeral=True)
        return

    resolved, error = resolve_prize(prop_firm, account_type, account_size)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    prize = resolved["display_name"]
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Must be used inside the server.", ephemeral=True)
        return
    if user.id in ticket_creation_in_progress:
        await interaction.response.send_message("⏳ A ticket is already being created for this user.", ephemeral=True)
        return
    ticket_creation_in_progress.add(user.id)
    lock = get_winner_lock(user.id)
    await interaction.response.defer(ephemeral=True)
    try:
        async with lock:
            existing = find_any_open_ticket_for_user(guild, user)
            if existing is not None:
                await interaction.followup.send(f"❌ {user.mention} already has an open ticket: {existing.mention}", ephemeral=True)
                return
            try:
                ticket_channel, bundle_id = await create_giveaway_ticket_and_log(
                    interaction=interaction,
                    guild=guild,
                    user=user,
                    selected_prizes=[prize],
                    quantity=quantity,
                    code=code,
                    show=show,
                    notes=notes,
                    prize_catalog_ids=[resolved["prize_catalog_id"]],
                    prop_firm_ids=[resolved["prop_firm_id"]],
                    account_type_ids=[resolved["account_type_id"]],
                    account_size_ids=[resolved["account_size_id"]],
                )
            except Exception as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
    finally:
        ticket_creation_in_progress.discard(user.id)
    qty_text = f" (x{quantity})" if quantity > 1 else ""
    msg = f"✅ {user.mention} — {prize}{qty_text} logged.\n🎟️ Ticket created: {ticket_channel.mention}\n🧾 Bundle ID: `{bundle_id}`"
    if show:
        msg += f"\n📺 Show: **{show}**"
    if notes:
        msg += f"\n📝 Notes: {notes}"
    await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="multi", description="Log a Discord winner with multiple prizes and create one ticket", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    user="Winner",
    prop_firm_1="First prize prop firm (or 'Unknown Prize')",
    account_type_1="First prize account type (skip if Unknown Prize)",
    account_size_1="First prize account size (skip if Unknown Prize)",
    prop_firm_2="Second prize prop firm (or 'Unknown Prize')",
    account_type_2="Second prize account type (skip if Unknown Prize)",
    account_size_2="Second prize account size (skip if Unknown Prize)",
    prop_firm_3="Third prize prop firm (optional)",
    account_type_3="Third prize account type (optional)",
    account_size_3="Third prize account size (optional)",
    code="Optional giveaway code",
    show="Show the prize was won on"
)
@app_commands.autocomplete(
    prop_firm_1=prop_firm_autocomplete,
    account_type_1=account_type_autocomplete_1,
    account_size_1=account_size_autocomplete_1,
    prop_firm_2=prop_firm_autocomplete,
    account_type_2=account_type_autocomplete_2,
    account_size_2=account_size_autocomplete_2,
    prop_firm_3=prop_firm_autocomplete,
    account_type_3=account_type_autocomplete_3,
    account_size_3=account_size_autocomplete_3,
    show=show_autocomplete
)
async def multiwinner(
    interaction: discord.Interaction,
    user: discord.Member,
    prop_firm_1: str,
    prop_firm_2: str,
    account_type_1: str = "",
    account_size_1: str = "",
    account_type_2: str = "",
    account_size_2: str = "",
    prop_firm_3: str | None = None,
    account_type_3: str = "",
    account_size_3: str = "",
    code: str | None = None,
    show: str | None = None
):
    if not await ensure_mod(interaction):
        return

    raw_inputs = [
        (prop_firm_1, account_type_1, account_size_1),
        (prop_firm_2, account_type_2, account_size_2),
    ]
    if prop_firm_3:
        raw_inputs.append((prop_firm_3, account_type_3, account_size_3))

    resolved_prizes = []
    for pf, at, sz in raw_inputs:
        r, error = resolve_prize(pf, at, sz)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        resolved_prizes.append(r)

    selected_prizes = dedupe_preserve_order([r["display_name"] for r in resolved_prizes])
    if len(selected_prizes) < 2:
        await interaction.response.send_message("❌ Use /win for a single prize, or select at least two different prizes here.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Must be used inside the server.", ephemeral=True)
        return
    if user.id in ticket_creation_in_progress:
        await interaction.response.send_message("⏳ A ticket is already being created for this user.", ephemeral=True)
        return
    ticket_creation_in_progress.add(user.id)
    lock = get_winner_lock(user.id)
    await interaction.response.defer(ephemeral=True)
    try:
        async with lock:
            existing = find_any_open_ticket_for_user(guild, user)
            if existing is not None:
                await interaction.followup.send(f"❌ {user.mention} already has an open ticket: {existing.mention}", ephemeral=True)
                return
            try:
                ticket_channel, bundle_id = await create_giveaway_ticket_and_log(
                    interaction=interaction,
                    guild=guild,
                    user=user,
                    selected_prizes=selected_prizes,
                    code=code,
                    show=show,
                    prize_catalog_ids=[r["prize_catalog_id"] for r in resolved_prizes],
                    prop_firm_ids=[r["prop_firm_id"] for r in resolved_prizes],
                    account_type_ids=[r["account_type_id"] for r in resolved_prizes],
                    account_size_ids=[r["account_size_id"] for r in resolved_prizes],
                )
            except Exception as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
    finally:
        ticket_creation_in_progress.discard(user.id)
    msg = f"✅ {user.mention} logged with {len(selected_prizes)} prizes.\n🎟️ Ticket created: {ticket_channel.mention}\n🧾 Bundle ID: `{bundle_id}`"
    if show:
        msg += f"\n📺 Show: **{show}**"
    await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="yt", description="Log a YouTube winner without creating a ticket", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    youtube_name="YouTube winner name or handle",
    prop_firm="Prop firm — or select 'Unknown Prize' if prize is not yet known",
    account_type="Account type (skip if Unknown Prize)",
    account_size="Account size (skip if Unknown Prize)",
    code="Optional giveaway code",
    notes="Optional notes",
    show="Show the prize was won on"
)
@app_commands.autocomplete(
    prop_firm=prop_firm_autocomplete,
    account_type=account_type_autocomplete,
    account_size=account_size_autocomplete,
    show=show_autocomplete
)
async def youtube(
    interaction: discord.Interaction,
    youtube_name: str,
    prop_firm: str,
    account_type: str = "",
    account_size: str = "",
    code: str | None = None,
    notes: str | None = None,
    show: str | None = None
):
    if not await ensure_mod(interaction):
        return
    if len(youtube_name) > 100:
        await interaction.response.send_message("❌ YouTube name must be 100 characters or less.", ephemeral=True)
        return
    if notes and len(notes) > 500:
        await interaction.response.send_message("❌ Notes must be 500 characters or less.", ephemeral=True)
        return

    resolved, error = resolve_prize(prop_firm, account_type, account_size)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    prize = resolved["display_name"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_id = uuid.uuid4().hex[:8]
    backend_line = format_backend_log_line(youtube_name, "youtube", prize, code, show)
    if notes:
        backend_line += f" | Notes: {notes}"
    backend_msg = await post_backend_log([backend_line])
    backend_message_id = str(backend_msg.id) if backend_msg else None
    data = load_data()
    data["winners"].append({
        "timestamp": timestamp,
        "bundle_id": bundle_id,
        "user": youtube_name,
        "user_id": None,
        "source": "youtube",
        "show": show or "Unknown",
        "prize": prize,
        "code": code,
        "mod": interaction.user.name,
        "mod_id": str(interaction.user.id),
        "channel": interaction.channel.name if interaction.channel else "Unknown",
        "server": interaction.guild.name if interaction.guild else "Unknown",
        "status": "waiting_for_support_ticket",
        "notes": notes,
        "backend_message_id": backend_message_id,
        "prize_catalog_id": resolved["prize_catalog_id"],
        "prop_firm_id": resolved["prop_firm_id"],
        "account_type_id": resolved["account_type_id"],
        "account_size_id": resolved["account_size_id"],
    })
    save_data(data)
    msg = f"✅ YouTube winner logged: **{youtube_name}**\nPrize: **{prize}**\n🧾 Bundle ID: `{bundle_id}`"
    if show:
        msg += f"\n📺 Show: **{show}**"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="ytmulti", description="Log a YouTube winner with multiple prizes without creating a ticket", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    youtube_name="YouTube winner name or handle",
    prop_firm_1="First prize prop firm (or 'Unknown Prize')",
    account_type_1="First prize account type (skip if Unknown Prize)",
    account_size_1="First prize account size (skip if Unknown Prize)",
    prop_firm_2="Second prize prop firm (or 'Unknown Prize')",
    account_type_2="Second prize account type (skip if Unknown Prize)",
    account_size_2="Second prize account size (skip if Unknown Prize)",
    prop_firm_3="Third prize prop firm (optional)",
    account_type_3="Third prize account type (optional)",
    account_size_3="Third prize account size (optional)",
    code="Optional giveaway code",
    notes="Optional notes",
    show="Show the prize was won on"
)
@app_commands.autocomplete(
    prop_firm_1=prop_firm_autocomplete,
    account_type_1=account_type_autocomplete_1,
    account_size_1=account_size_autocomplete_1,
    prop_firm_2=prop_firm_autocomplete,
    account_type_2=account_type_autocomplete_2,
    account_size_2=account_size_autocomplete_2,
    prop_firm_3=prop_firm_autocomplete,
    account_type_3=account_type_autocomplete_3,
    account_size_3=account_size_autocomplete_3,
    show=show_autocomplete
)
async def ytmulti(
    interaction: discord.Interaction,
    youtube_name: str,
    prop_firm_1: str,
    prop_firm_2: str,
    account_type_1: str = "",
    account_size_1: str = "",
    account_type_2: str = "",
    account_size_2: str = "",
    prop_firm_3: str | None = None,
    account_type_3: str = "",
    account_size_3: str = "",
    code: str | None = None,
    notes: str | None = None,
    show: str | None = None
):
    if not await ensure_mod(interaction):
        return
    if len(youtube_name) > 100:
        await interaction.response.send_message("❌ YouTube name must be 100 characters or less.", ephemeral=True)
        return
    if notes and len(notes) > 500:
        await interaction.response.send_message("❌ Notes must be 500 characters or less.", ephemeral=True)
        return

    raw_inputs = [
        (prop_firm_1, account_type_1, account_size_1),
        (prop_firm_2, account_type_2, account_size_2),
    ]
    if prop_firm_3:
        raw_inputs.append((prop_firm_3, account_type_3, account_size_3))

    resolved_prizes = []
    for pf, at, sz in raw_inputs:
        r, error = resolve_prize(pf, at, sz)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        resolved_prizes.append(r)

    if len(resolved_prizes) < 2:
        await interaction.response.send_message("❌ Select at least two prizes.", ephemeral=True)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_id = uuid.uuid4().hex[:8]
    backend_lines = []
    for r in resolved_prizes:
        line = format_backend_log_line(youtube_name, "youtube", r["display_name"], code, show)
        if notes:
            line += f" | Notes: {notes}"
        backend_lines.append(line)
    backend_msg = await post_backend_log(backend_lines)
    backend_message_id = str(backend_msg.id) if backend_msg else None
    data = load_data()
    for r in resolved_prizes:
        data["winners"].append({
            "timestamp": timestamp,
            "bundle_id": bundle_id,
            "user": youtube_name,
            "user_id": None,
            "source": "youtube",
            "show": show or "Unknown",
            "prize": r["display_name"],
            "code": code,
            "mod": interaction.user.name,
            "mod_id": str(interaction.user.id),
            "channel": interaction.channel.name if interaction.channel else "Unknown",
            "server": interaction.guild.name if interaction.guild else "Unknown",
            "status": "waiting_for_support_ticket",
            "notes": notes,
            "backend_message_id": backend_message_id,
            "prize_catalog_id": r["prize_catalog_id"],
            "prop_firm_id": r["prop_firm_id"],
            "account_type_id": r["account_type_id"],
            "account_size_id": r["account_size_id"],
        })
    save_data(data)
    summary = " & ".join([r["display_name"] for r in resolved_prizes])
    msg = f"✅ YouTube winner logged: **{youtube_name}**\nPrizes: **{summary}**\n🧾 Bundle ID: `{bundle_id}`"
    if show:
        msg += f"\n📺 Show: **{show}**"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="track", description="Log a Discord winner without creating a ticket", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    user="Winner",
    prop_firm="Prop firm — or select 'Unknown Prize' if prize is not yet known",
    account_type="Account type (skip if Unknown Prize)",
    account_size="Account size (skip if Unknown Prize)",
    code="Optional giveaway code",
    notes="Optional notes",
    show="Show the prize was won on"
)
@app_commands.autocomplete(
    prop_firm=prop_firm_autocomplete,
    account_type=account_type_autocomplete,
    account_size=account_size_autocomplete,
    show=show_autocomplete
)
async def track(
    interaction: discord.Interaction,
    user: discord.Member,
    prop_firm: str,
    account_type: str = "",
    account_size: str = "",
    code: str | None = None,
    notes: str | None = None,
    show: str | None = None
):
    if not await ensure_mod(interaction):
        return

    resolved, error = resolve_prize(prop_firm, account_type, account_size)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    prize = resolved["display_name"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_id = uuid.uuid4().hex[:8]
    backend_line = format_backend_log_line(user.name, "discord", prize, code, show)
    if notes:
        backend_line += f" | Notes: {notes}"
    backend_msg = await post_backend_log([backend_line])
    backend_message_id = str(backend_msg.id) if backend_msg else None
    data = load_data()
    data["winners"].append({
        "timestamp": timestamp,
        "bundle_id": bundle_id,
        "user": user.name,
        "user_id": str(user.id),
        "source": "discord",
        "show": show or "Unknown",
        "prize": prize,
        "code": code,
        "mod": interaction.user.name,
        "mod_id": str(interaction.user.id),
        "channel": interaction.channel.name if interaction.channel else "Unknown",
        "server": interaction.guild.name if interaction.guild else "Unknown",
        "status": "tracked_no_ticket",
        "notes": notes,
        "backend_message_id": backend_message_id,
        "prize_catalog_id": resolved["prize_catalog_id"],
        "prop_firm_id": resolved["prop_firm_id"],
        "account_type_id": resolved["account_type_id"],
        "account_size_id": resolved["account_size_id"],
    })
    save_data(data)
    msg = f"✅ {user.mention} tracked without opening a ticket.\nPrize: **{prize}**\n🧾 Bundle ID: `{bundle_id}`"
    if show:
        msg += f"\n📺 Show: **{show}**"
    if notes:
        msg += f"\n📝 Notes: {notes}"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="manualticket", description="Open a manual non-giveaway ticket for a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(user="Member to open a ticket for", reason="Reason for opening the ticket")
async def manualticket(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not await ensure_mod(interaction):
        return
    if len(reason) > 500:
        await interaction.response.send_message("❌ Reason must be 500 characters or less.", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Must be used inside the server.", ephemeral=True)
        return
    if user.id in ticket_creation_in_progress:
        await interaction.response.send_message("⏳ A ticket is already being created for this user.", ephemeral=True)
        return
    ticket_creation_in_progress.add(user.id)
    lock = get_winner_lock(user.id)
    await interaction.response.defer(ephemeral=True)
    try:
        async with lock:
            existing = find_any_open_ticket_for_user(guild, user)
            if existing is not None:
                await interaction.followup.send(f"❌ {user.mention} already has an open ticket: {existing.mention}", ephemeral=True)
                return
            try:
                ticket_channel = await create_manual_ticket(
                    interaction=interaction,
                    guild=guild,
                    user=user,
                    reason=reason
                )
            except Exception as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
    finally:
        ticket_creation_in_progress.discard(user.id)
    await interaction.followup.send(
        f"✅ Manual ticket created for {user.mention}: {ticket_channel.mention}",
        ephemeral=True
    )


@tree.command(name="delete", description="Delete one of this bot's tickets and mark it completed", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(confirm="Type DELETE to confirm")
async def delete(interaction: discord.Interaction, confirm: str):
    if not await ensure_mod(interaction):
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("❌ This command can only be used inside a ticket channel.", ephemeral=True)
        return
    is_support = is_support_ticket_channel(channel)
    is_giveaway_or_manual = is_bot_ticket_channel(channel)
    if not is_support and not is_giveaway_or_manual:
        await interaction.response.send_message("❌ This command can only be used inside this bot's ticket channels.", ephemeral=True)
        return
    if confirm != "DELETE":
        await interaction.response.send_message("❌ To delete this ticket, run the command again and set confirm to: `DELETE`", ephemeral=True)
        return
    if is_support:
        guild = interaction.guild
        uid, uname = extract_user_from_channel(channel, guild) if guild else (None, None)
        await save_transcript(channel, "support", interaction.user, None, uid, uname)
    else:
        await mark_channel_entries_completed(channel)
        bundle_id = get_bundle_id_from_channel(channel)
        entries = find_entries_for_channel(channel)
        uid = entries[0].get("user_id") if entries else None
        uname = entries[0].get("user") if entries else None
        ticket_type = "giveaway" if is_giveaway_ticket_channel(channel) else "manual"
        await save_transcript(channel, ticket_type, interaction.user, bundle_id, uid, uname)
    await interaction.response.send_message("🗑️ Ticket marked complete and deleting in 3 seconds.", ephemeral=False)
    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"Ticket deleted by {interaction.user}")
    except Exception as e:
        try:
            await channel.send(f"❌ Failed to delete channel: `{e}`")
        except Exception:
            pass
        print(f"Delete failed: {e}")


@tree.command(name="wins", description="Check wins for a user", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(user="Select the user")
async def wins(interaction: discord.Interaction, user: discord.Member):
    if not await ensure_mod(interaction):
        return
    data = load_data()
    count = sum(1 for x in data["winners"] if x.get("user_id") == str(user.id))
    await interaction.response.send_message(f"{user.mention} has {count} logged win(s).", ephemeral=True)


@tree.command(name="winnerlist", description="Show last 10 logged entries", guild=discord.Object(id=GUILD_ID))
async def winnerlist(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    data = load_data()
    winners = data["winners"][-10:]
    if not winners:
        await interaction.response.send_message("No winners logged yet.", ephemeral=True)
        return
    lines = []
    for entry in reversed(winners):
        source = entry.get("source", "discord")
        show = entry.get("show", "Unknown")
        status = entry.get("status", "unknown")
        ticket_type = entry.get("type")
        type_text = f" [{ticket_type}]" if ticket_type else ""
        status_suffix = " ✅" if status == "completed" else ""
        prize_text = entry["prize"] if entry.get("prize") else "No Prize"
        lines.append(f"{entry['timestamp']} — {entry['user']} — {show} — {prize_text} ({source}){type_text}{status_suffix}")
    await interaction.response.send_message("**Last 10 entries:**\n" + "\n".join(lines), ephemeral=True)


TRANSCRIPTS_PER_PAGE = 5


def build_transcript_page(records: list[dict], page: int, user_mention: str) -> tuple[str, "TranscriptPageView"]:
    total = len(records)
    total_pages = max(1, (total + TRANSCRIPTS_PER_PAGE - 1) // TRANSCRIPTS_PER_PAGE)
    start = page * TRANSCRIPTS_PER_PAGE
    page_records = records[start:start + TRANSCRIPTS_PER_PAGE]
    lines = [f"**Transcripts for {user_mention}** ({total} found) — Page {page + 1}/{total_pages}\n"]
    for i, r in enumerate(page_records):
        deleted_at = r["deleted_at"].strftime("%b %d %Y") if r.get("deleted_at") else "Unknown"
        ticket_type = r.get("ticket_type", "unknown").capitalize()
        channel_name = r.get("channel_name", "unknown")
        lines.append(f"`{start + i + 1}.` **{ticket_type}** — #{channel_name} — {deleted_at}")
    view = TranscriptPageView(records=records, page=page, user_mention=user_mention)
    return "\n".join(lines), view


@tree.command(name="transcript", description="Look up deleted ticket transcripts for a user", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(user="The member to look up transcripts for")
async def transcript(interaction: discord.Interaction, user: discord.Member):
    if not await ensure_mod(interaction):
        return
    records = fetch_transcripts_for_user(str(user.id))
    if not records:
        await interaction.response.send_message(
            f"No saved transcripts found for {user.mention}.",
            ephemeral=True
        )
        return
    content, view = build_transcript_page(records, page=0, user_mention=user.mention)
    await interaction.response.send_message(content, view=view, ephemeral=True)


class TranscriptPageView(discord.ui.View):
    def __init__(self, records: list[dict], page: int, user_mention: str):
        super().__init__(timeout=180)
        self.records = records
        self.page = page
        self.user_mention = user_mention
        self.total_pages = max(1, (len(records) + TRANSCRIPTS_PER_PAGE - 1) // TRANSCRIPTS_PER_PAGE)
        start = page * TRANSCRIPTS_PER_PAGE
        page_records = records[start:start + TRANSCRIPTS_PER_PAGE]
        for i, record in enumerate(page_records):
            deleted_at = record["deleted_at"].strftime("%b %d") if record.get("deleted_at") else "?"
            ticket_type = record.get("ticket_type", "ticket").capitalize()
            channel_name = record.get("channel_name", "unknown")
            label = f"{ticket_type} — #{channel_name[:20]} — {deleted_at}"
            self.add_item(TranscriptViewButton(label=label, transcript_id=record["id"], row=i))
        if self.total_pages > 1:
            prev_btn = discord.ui.Button(
                label="⬅ Prev",
                style=discord.ButtonStyle.secondary,
                disabled=(page == 0),
                row=4
            )
            next_btn = discord.ui.Button(
                label="Next ➡",
                style=discord.ButtonStyle.secondary,
                disabled=(page >= self.total_pages - 1),
                row=4
            )
            prev_btn.callback = self._prev_callback
            next_btn.callback = self._next_callback
            self.add_item(prev_btn)
            self.add_item(next_btn)

    async def _prev_callback(self, interaction: discord.Interaction):
        new_page = max(0, self.page - 1)
        content, view = build_transcript_page(self.records, new_page, self.user_mention)
        await interaction.response.edit_message(content=content, view=view)

    async def _next_callback(self, interaction: discord.Interaction):
        new_page = min(self.total_pages - 1, self.page + 1)
        content, view = build_transcript_page(self.records, new_page, self.user_mention)
        await interaction.response.edit_message(content=content, view=view)


class TranscriptViewButton(discord.ui.Button):
    def __init__(self, label: str, transcript_id: int, row: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.transcript_id = transcript_id

    async def callback(self, interaction: discord.Interaction):
        data = fetch_transcript_messages(self.transcript_id)
        if not data:
            await interaction.response.send_message("❌ Could not load transcript.", ephemeral=True)
            return
        messages = data.get("messages", [])
        channel_name = data.get("channel_name", "unknown")
        deleted_at = data.get("deleted_at")
        deleted_str = deleted_at.strftime("%b %d %Y %H:%M") if deleted_at else "Unknown"
        if not messages:
            await interaction.response.send_message("No messages found in this transcript.", ephemeral=True)
            return
        header = f"**Transcript: #{channel_name}** — Deleted {deleted_str}\n\n"
        lines = []
        for msg in messages:
            ts = msg.get("timestamp", "")
            author = msg.get("author", "Unknown")
            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            line = f"[{ts}] **{author}**: {content}"
            if attachments:
                line += f" 📎 {', '.join(attachments)}"
            lines.append(line)
        full_text = header + "\n".join(lines)
        chunks = []
        remaining = full_text
        while len(remaining) > 2000:
            split_at = remaining.rfind("\n", 0, 2000)
            if split_at == -1:
                split_at = 2000
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)


@tree.command(name="stats", description="Show giveaway stats by show and prize type", guild=discord.Object(id=GUILD_ID))
async def stats(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    data = load_data()
    entries = [e for e in data["winners"] if e.get("prize")]
    if not entries:
        await interaction.response.send_message("No entries logged yet.", ephemeral=True)
        return
    total = len(entries)
    completed = sum(1 for e in entries if e.get("status") == "completed")
    show_counts: dict[str, int] = {}
    for e in entries:
        show = e.get("show") or "Unknown"
        show_counts[show] = show_counts.get(show, 0) + 1
    prize_counts: dict[str, int] = {}
    for e in entries:
        prize = e.get("prize") or "Unknown"
        prize_counts[prize] = prize_counts.get(prize, 0) + 1
    lines = [f"**Giveaway Stats**\n", f"Total logged: **{total}**", f"Completed: **{completed}**\n"]
    lines.append("**By Show:**")
    for show, count in sorted(show_counts.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {show}: {count}")
    lines.append("\n**By Prize:**")
    for prize, count in sorted(prize_counts.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {prize}: {count}")
    message = "\n".join(lines)
    if len(message) > 2000:
        await interaction.response.send_message(message[:2000], ephemeral=True)
        await interaction.followup.send(message[2000:], ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# =========================
# READY
# =========================
@bot.event
async def on_ready():
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Logged in as {bot.user}")
        print("Slash commands synced.")
        print("Button views loaded.")
    except Exception as e:
        print(f"[ERROR] on_ready failed: {e}")


@bot.event
async def on_disconnect():
    print("[WARNING] Bot disconnected from Discord gateway. Waiting for reconnect...")


@bot.event
async def on_resumed():
    print("[INFO] Bot successfully reconnected to Discord gateway.")


@bot.event
async def on_error(event: str, *args, **kwargs):
    import traceback
    print(f"[ERROR] Unhandled error in event '{event}':")
    traceback.print_exc()


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ERROR] Slash command error: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
        else:
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
    except Exception:
        pass

# =========================
# /faq ADMIN COMMANDS
# =========================
faq_group = app_commands.Group(name="faq", description="Manage FAQ entries", guild=discord.Object(id=GUILD_ID))
faq_category_group = app_commands.Group(name="category", description="Manage FAQ categories", parent=faq_group)


# ── /faq add ──────────────────────────────────────────────────────────────────

class FaqAddCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(label=c["name"], value=str(c["id"]))
            for c in categories[:25]
        ]
        super().__init__(placeholder="Select a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        category_name = next(o.label for o in self.options if o.value == self.values[0])
        await interaction.response.send_modal(FaqAddModal(category_id=category_id, category_name=category_name))


class FaqAddCategoryView(discord.ui.View):
    def __init__(self, categories: list[dict]):
        super().__init__(timeout=120)
        self.add_item(FaqAddCategorySelect(categories))


class FaqAddModal(discord.ui.Modal):
    def __init__(self, category_id: int, category_name: str):
        super().__init__(title=f"Add FAQ — {category_name[:40]}")
        self.category_id = category_id
        self.question_input = discord.ui.TextInput(
            label="Question",
            placeholder="Enter the question",
            max_length=200,
            required=True
        )
        self.answer_input = discord.ui.TextInput(
            label="Answer",
            placeholder="Enter the answer",
            max_length=1800,
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.question_input)
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COALESCE(MAX(sort_order), -1) + 1
                        FROM faq_entries
                        WHERE category_id = %s AND active = true
                    """, (self.category_id,))
                    next_sort = cur.fetchone()[0]
                    cur.execute("""
                        INSERT INTO faq_entries
                            (category_id, question, answer, visibility, escalate, active, sort_order,
                             created_by_discord_id, updated_by_discord_id)
                        VALUES (%s, %s, %s, 'public', false, true, %s, %s, %s)
                    """, (
                        self.category_id,
                        self.question_input.value.strip(),
                        self.answer_input.value.strip(),
                        next_sort,
                        str(interaction.user.id),
                        str(interaction.user.id),
                    ))
                conn.commit()
            await interaction.response.send_message(
                f"✅ FAQ added successfully.\n**Q:** {self.question_input.value.strip()[:100]}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to add FAQ: `{e}`", ephemeral=True)


@faq_group.command(name="add", description="Add a new FAQ question to a category")
async def faq_add(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    categories = get_active_faq_categories()
    if not categories:
        await interaction.response.send_message("❌ No active categories found.", ephemeral=True)
        return
    view = FaqAddCategoryView(categories)
    await interaction.response.send_message(
        "Select a category to add the FAQ to:",
        view=view,
        ephemeral=True
    )


# ── /faq edit ─────────────────────────────────────────────────────────────────

class FaqEditCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(label=c["name"], value=str(c["id"]))
            for c in categories[:25]
        ]
        super().__init__(placeholder="Select a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        entries = get_faq_entries_by_category(category_id, visibility="all")
        if not entries:
            await interaction.response.edit_message(
                content="❌ No active entries in that category.", view=None
            )
            return
        view = FaqEditQuestionView(entries=entries)
        await interaction.response.edit_message(
            content="Select a question to edit:",
            view=view
        )


class FaqEditCategoryView(discord.ui.View):
    def __init__(self, categories: list[dict]):
        super().__init__(timeout=120)
        self.add_item(FaqEditCategorySelect(categories))


class FaqEditQuestionSelect(discord.ui.Select):
    def __init__(self, entries: list[dict]):
        options = [
            discord.SelectOption(label=e["question"][:100], value=str(e["id"]))
            for e in entries[:25]
        ]
        super().__init__(placeholder="Select a question", min_values=1, max_values=1, options=options)
        self.entries = entries

    async def callback(self, interaction: discord.Interaction):
        entry_id = int(self.values[0])
        entry = get_faq_entry_by_id(entry_id)
        if not entry:
            await interaction.response.edit_message(content="❌ Entry not found.", view=None)
            return
        await interaction.response.send_modal(FaqEditModal(entry=entry))


class FaqEditQuestionView(discord.ui.View):
    def __init__(self, entries: list[dict]):
        super().__init__(timeout=120)
        self.add_item(FaqEditQuestionSelect(entries))


class FaqEditModal(discord.ui.Modal):
    def __init__(self, entry: dict):
        super().__init__(title="Edit FAQ")
        self.entry_id = entry["id"]
        self.question_input = discord.ui.TextInput(
            label="Question",
            default=entry["question"][:200],
            max_length=200,
            required=True
        )
        self.answer_input = discord.ui.TextInput(
            label="Answer",
            default=entry["answer"][:1800],
            max_length=1800,
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.question_input)
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE faq_entries
                        SET question = %s,
                            answer = %s,
                            updated_at = now(),
                            updated_by_discord_id = %s
                        WHERE id = %s
                    """, (
                        self.question_input.value.strip(),
                        self.answer_input.value.strip(),
                        str(interaction.user.id),
                        self.entry_id,
                    ))
                conn.commit()
            await interaction.response.send_message(
                f"✅ FAQ updated.\n**Q:** {self.question_input.value.strip()[:100]}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to update FAQ: `{e}`", ephemeral=True)


@faq_group.command(name="edit", description="Edit an existing FAQ question")
async def faq_edit(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    categories = get_active_faq_categories()
    if not categories:
        await interaction.response.send_message("❌ No active categories found.", ephemeral=True)
        return
    view = FaqEditCategoryView(categories)
    await interaction.response.send_message(
        "Select the category containing the FAQ to edit:",
        view=view,
        ephemeral=True
    )


# ── /faq delete ───────────────────────────────────────────────────────────────

class FaqDeleteCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(label=c["name"], value=str(c["id"]))
            for c in categories[:25]
        ]
        super().__init__(placeholder="Select a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        entries = get_faq_entries_by_category(category_id, visibility="all")
        if not entries:
            await interaction.response.edit_message(
                content="❌ No active entries in that category.", view=None
            )
            return
        view = FaqDeleteQuestionView(entries=entries)
        await interaction.response.edit_message(
            content="Select a question to delete:",
            view=view
        )


class FaqDeleteCategoryView(discord.ui.View):
    def __init__(self, categories: list[dict]):
        super().__init__(timeout=120)
        self.add_item(FaqDeleteCategorySelect(categories))


class FaqDeleteQuestionSelect(discord.ui.Select):
    def __init__(self, entries: list[dict]):
        options = [
            discord.SelectOption(label=e["question"][:100], value=str(e["id"]))
            for e in entries[:25]
        ]
        super().__init__(placeholder="Select a question to delete", min_values=1, max_values=1, options=options)
        self.entries = entries

    async def callback(self, interaction: discord.Interaction):
        entry_id = int(self.values[0])
        entry = get_faq_entry_by_id(entry_id)
        if not entry:
            await interaction.response.edit_message(content="❌ Entry not found.", view=None)
            return
        view = FaqDeleteConfirmView(entry_id=entry_id, question=entry["question"])
        await interaction.response.edit_message(
            content=(
                f"⚠️ **Confirm delete**\n\n"
                f"**Q:** {entry['question'][:200]}\n\n"
                "This will soft-delete the entry (sets active = false). It can be restored via the database."
            ),
            view=view
        )


class FaqDeleteQuestionView(discord.ui.View):
    def __init__(self, entries: list[dict]):
        super().__init__(timeout=120)
        self.add_item(FaqDeleteQuestionSelect(entries))


class FaqDeleteConfirmView(discord.ui.View):
    def __init__(self, entry_id: int, question: str):
        super().__init__(timeout=60)
        self.entry_id = entry_id
        self.question = question

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE faq_entries
                        SET active = false,
                            updated_at = now(),
                            updated_by_discord_id = %s
                        WHERE id = %s
                    """, (str(interaction.user.id), self.entry_id))
                conn.commit()
            await interaction.response.edit_message(
                content=f"🗑️ FAQ deleted: **{self.question[:100]}**",
                view=None
            )
        except Exception as e:
            await interaction.response.edit_message(
                content=f"❌ Failed to delete FAQ: `{e}`",
                view=None
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


@faq_group.command(name="delete", description="Delete a FAQ question (soft delete)")
async def faq_delete(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    categories = get_active_faq_categories()
    if not categories:
        await interaction.response.send_message("❌ No active categories found.", ephemeral=True)
        return
    view = FaqDeleteCategoryView(categories)
    await interaction.response.send_message(
        "Select the category containing the FAQ to delete:",
        view=view,
        ephemeral=True
    )


# ── /faq category add ─────────────────────────────────────────────────────────

class FaqCategoryAddModal(discord.ui.Modal, title="Add FAQ Category"):
    name_input = discord.ui.TextInput(
        label="Category name",
        placeholder="e.g. Payments, Mobile App, API",
        max_length=80,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COALESCE(MAX(sort_order), -1) + 1
                        FROM faq_categories
                        WHERE active = true
                    """)
                    next_sort = cur.fetchone()[0]
                    cur.execute("""
                        INSERT INTO faq_categories
                            (name, sort_order, active, created_by_discord_id, updated_by_discord_id)
                        VALUES (%s, %s, true, %s, %s)
                    """, (name, next_sort, str(interaction.user.id), str(interaction.user.id)))
                conn.commit()
            await interaction.response.send_message(
                f"✅ Category **{name}** added successfully.",
                ephemeral=True
            )
        except Exception as e:
            if "unique" in str(e).lower():
                await interaction.response.send_message(
                    f"❌ A category named **{name}** already exists.", ephemeral=True
                )
            else:
                await interaction.response.send_message(f"❌ Failed to add category: `{e}`", ephemeral=True)


@faq_category_group.command(name="add", description="Add a new FAQ category")
async def faq_category_add(interaction: discord.Interaction):
    if not await ensure_mod(interaction):
        return
    await interaction.response.send_modal(FaqCategoryAddModal())


# ── /faq category delete ──────────────────────────────────────────────────────

class FaqCategoryDeleteSelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(label=c["name"], value=str(c["id"]))
            for c in categories[:25]
        ]
        super().__init__(placeholder="Select a category to delete", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        category = get_faq_category_by_id(category_id)
        if not category:
            await interaction.response.edit_message(content="❌ Categ


bot.run(TOKEN)
