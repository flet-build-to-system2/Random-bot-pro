import random
import sqlite3
import os
import threading
from flask import Flask, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==============================
#      الإعدادات وقاعدة البيانات
# ==============================
TOKEN = "8777038264:AAGr6TwS2mXccJqE-bI2QTGJ-QAGmw_pNbA"
DB_PATH = "db.sqlite"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS inventory (user_id INTEGER, item TEXT)")
conn.commit()

# ذاكرة الألعاب
solo_games = {}
quiz_games = {}
active_guess_games = {}
active_xo_games = {}

# ==============================
#     دوال الـ Utilities المدمجة
# ==============================

def draw_xo_keyboard(board):
    keyboard = []
    for i in range(0, 9, 3):
        row = []
        for j in range(i, i + 3):
            label = board[j] if board[j] != " " else "⬜"
            row.append(InlineKeyboardButton(label, callback_data=f"xo_play_{j}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def check_xo_win(board):
    win_cond = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]]
    for cond in win_cond:
        if board[cond[0]] == board[cond[1]] == board[cond[2]] and board[cond[0]] != " ":
            return board[cond[0]]
    return "Draw" if " " not in board else None

def format_leaderboard(cursor, limit=10):
    cursor.execute("SELECT user_id, points FROM users ORDER BY points DESC LIMIT ?", (limit,))
    top = cursor.fetchall()
    txt = "🏆 **قائمة المتصدرين (Top 10):**\n\n"
    for i, u in enumerate(top, 1):
        txt += f"{i}. `ID: {u[0]}` — **{u[1]}** pts\n"
    return txt

def buy_item(cursor, conn, user_id, item, cost):
    cursor.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] < cost: return False
    cursor.execute("UPDATE users SET points = points - ? WHERE user_id=?", (cost, user_id))
    cursor.execute("INSERT INTO inventory(user_id, item) VALUES(?, ?)", (user_id, item))
    conn.commit()
    return True

# ==============================
#          منطق البوت
# ==============================

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "القائمة الرئيسية 🏠"),
        BotCommand("challenge", "تحدي تخمين (ID الخصم) 👥"),
        BotCommand("xo", "تحدي اكس او (ID الخصم) ❌⭕"),
    ])

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 لعب فردي", callback_data="solo"), InlineKeyboardButton("🧠 كويز سريع", callback_data="quiz")],
        [InlineKeyboardButton("👥 تحدي تخمين", callback_data="challenge_info"), InlineKeyboardButton("❌⭕ تحدي XO", callback_data="xo_info")],
        [InlineKeyboardButton("💰 المتجر", callback_data="shop"), InlineKeyboardButton("🏆 رصيدي", callback_data="points")],
        [InlineKeyboardButton("🥇 المتصدرين", callback_data="leader")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cursor.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (uid,))
    conn.commit()
    await update.message.reply_text("🔥 مرحباً بك في SuperGameBot PRO!\nاختر لعبة للبدء:", reply_markup=main_menu())

async def challenge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("⚠️ استعمل: `/challenge ID_الخصم`", parse_mode="Markdown")
        return
    try:
        opponent = int(context.args[0])
        if opponent == uid: return await update.message.reply_text("❌ لا يمكنك تحدي نفسك!")
        kb = [[InlineKeyboardButton("✅ قبول", callback_data=f"guess_acc_{uid}"), InlineKeyboardButton("❌ رفض", callback_data=f"guess_rej_{uid}")]]
        await context.bot.send_message(opponent, f"👥 تحدي تخمين جديد من {uid}!", reply_markup=InlineKeyboardMarkup(kb))
        await update.message.reply_text("📨 تم إرسال طلب التحدي بنجاح.")
    except: await update.message.reply_text("❌ تعذر إرسال الطلب.")

async def xo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("⚠️ استعمل: `/xo ID_الخصم`")
        return
    try:
        opponent = int(context.args[0])
        kb = [[InlineKeyboardButton("✅ قبول", callback_data=f"xo_acc_{uid}"), InlineKeyboardButton("❌ رفض", callback_data=f"xo_rej_{uid}")]]
        await context.bot.send_message(opponent, f"❌⭕ تحدي XO من {uid}!", reply_markup=InlineKeyboardMarkup(kb))
        await update.message.reply_text("📨 تم إرسال طلب التحدي.")
    except: await update.message.reply_text("❌ خطأ في الإرسال.")

async def menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    if q.data == "solo":
        solo_games[uid] = {"num": random.randint(1, 100), "tries": 5}
        await q.message.reply_text("🎮 خمن رقم بين 1 و 100 (5 محاولات):")
    elif q.data == "quiz":
        question = random.choice([("عاصمة فرنسا؟", "باريس"), ("5+7=?", "12"), ("لون البحر؟", "ازرق")])
        quiz_games[uid] = question
        await q.message.reply_text(f"🧠 {question[0]}")
    elif q.data == "points":
        cursor.execute("SELECT points FROM users WHERE user_id=?", (uid,))
        await q.message.reply_text(f"🏆 رصيدك: {cursor.fetchone()[0]} نقطة.")
    elif q.data == "leader":
        await q.message.reply_text(format_leaderboard(cursor), parse_mode="Markdown")
    elif q.data == "shop":
        kb = [[InlineKeyboardButton("🎟️ محاولة إضافية (20)", callback_data="buy_try")]]
        await q.message.reply_text("🛒 المتجر:", reply_markup=InlineKeyboardMarkup(kb))
    elif q.data == "buy_try":
        if buy_item(cursor, conn, uid, "extra_try", 20): await q.message.reply_text("✅ تم الشراء!")
        else: await q.message.reply_text("❌ نقاطك غير كافية.")

async def handle_invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid, data = q.from_user.id, q.data
    challenger = int(data.split("_")[2])
    await q.answer()
    if "acc" in data:
        if data.startswith("guess"):
            num = random.randint(1, 100)
            active_guess_games[uid] = active_guess_games[challenger] = {"op": challenger if uid != challenger else uid, "number": num, "turn": challenger, "tries": 5}
            await context.bot.send_message(challenger, "🔥 خصمك قبل! ابدأ بالتخمين.")
            await q.edit_message_text("✅ بدأت اللعبة! انتظر دور الخصم.")
        elif data.startswith("xo"):
            board = [" "] * 9
            game = {"p1": challenger, "p2": uid, "board": board, "turn": challenger}
            active_xo_games[uid] = active_xo_games[challenger] = game
            await context.bot.send_message(challenger, "🎮 دورك (X):", reply_markup=draw_xo_keyboard(board))
            await q.edit_message_text("✅ قبلت التحدي!", reply_markup=draw_xo_keyboard(board))
    else: await q.edit_message_text("❌ تم الرفض.")

async def handle_xo_play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid, pos = q.from_user.id, int(q.data.split("_")[2])
    if uid not in active_xo_games: return
    game = active_xo_games[uid]
    if game["turn"] != uid or game["board"][pos] != " ": return
    
    symbol = "X" if uid == game["p1"] else "O"
    game["board"][pos] = symbol
    winner = check_xo_win(game["board"])
    
    if winner:
        res = f"🎉 الفائز: {symbol}" if winner != "Draw" else "🤝 تعادل!"
        await q.edit_message_text(res, reply_markup=draw_xo_keyboard(game["board"]))
        op = game["p2"] if uid == game["p1"] else game["p1"]
        await context.bot.send_message(op, res, reply_markup=draw_xo_keyboard(game["board"]))
        del active_xo_games[game["p1"]], active_xo_games[game["p2"]]
    else:
        game["turn"] = game["p2"] if uid == game["p1"] else game["p1"]
        await q.edit_message_text("⌛ تم، انتظر الخصم...", reply_markup=draw_xo_keyboard(game["board"]))
        await context.bot.send_message(game["turn"], "🔥 دورك الآن:", reply_markup=draw_xo_keyboard(game["board"]))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, txt = update.effective_user.id, update.message.text.strip().lower()
    if uid in quiz_games:
        if txt == quiz_games[uid][1].lower():
            cursor.execute("UPDATE users SET points = points + 5 WHERE user_id = ?", (uid,))
            conn.commit()
            await update.message.reply_text("✅ صح! +5 نقاط.")
        else: await update.message.reply_text(f"❌ خطأ، الحل: {quiz_games[uid][1]}")
        del quiz_games[uid]
    elif uid in solo_games:
        try:
            g = int(txt)
            solo_games[uid]["tries"] -= 1
            if g == solo_games[uid]["num"]:
                cursor.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (uid,))
                conn.commit()
                await update.message.reply_text("🎉 صح! مبروك +10.")
                del solo_games[uid]
            elif solo_games[uid]["tries"] <= 0:
                await update.message.reply_text(f"💀 خسرت! الرقم كان {solo_games[uid]['num']}")
                del solo_games[uid]
            else:
                hint = "📉 أصغر" if g > solo_games[uid]["num"] else "📈 أكبر"
                await update.message.reply_text(f"{hint} | بقيت {solo_games[uid]['tries']} محاولات")
        except: pass

# ==============================
#    قسم لوحة التحكم (Flask)
# ==============================

app = Flask(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>لوحة التحكم | SuperGameBot</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body { background: #f4f7f6; font-family: sans-serif; padding: 40px; }
        .card { border: none; border-radius: 15px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); }
        .stat-card { background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%); color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4 text-center">🎮 SuperGameBot Dashboard</h1>
        <div class="row mb-4 text-center">
            <div class="col-md-4">
                <div class="card stat-card p-3">
                    <h4>المستخدمين</h4>
                    <h2>{{ users_count }}</h2>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card p-3 bg-dark text-white">
                    <h4>ألعاب فردية نشطة</h4>
                    <h2>{{ active_solo }}</h2>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card p-3 bg-primary text-white">
                    <h4>مباريات XO جارية</h4>
                    <h2>{{ active_xo }}</h2>
                </div>
            </div>
        </div>
        <div class="card p-4">
            <h3>🥇 قائمة أفضل 10 لاعبين</h3>
            <table class="table">
                <thead><tr><th>المستخدم ID</th><th>النقاط</th></tr></thead>
                <tbody>
                    {% for user in leaderboard %}
                    <tr><td>{{ user.user_id }}</td><td>{{ user.points }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    cur = get_db_connection().cursor()
    cur.execute("SELECT user_id, points FROM users ORDER BY points DESC LIMIT 10")
    ld = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    return render_template_string(HTML_DASHBOARD, leaderboard=ld, users_count=count, active_solo=len(solo_games), active_xo=len(active_xo_games)//2)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# ==============================
#           التشغيل
# ==============================

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("challenge", challenge_cmd))
    bot.add_handler(CommandHandler("xo", xo_cmd))
    bot.add_handler(CallbackQueryHandler(handle_xo_play, pattern="^xo_play_"))
    bot.add_handler(CallbackQueryHandler(handle_invites, pattern="^(guess|xo)_(acc|rej)_"))
    bot.add_handler(CallbackQueryHandler(menu_buttons))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("🚀 Bot and Dashboard are online!")
    bot.run_polling()
