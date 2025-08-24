import nest_asyncio
nest_asyncio.apply()
import subprocess
import time
import asyncio
from telegram import Update
from telegram.helpers import mention_html
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ⚡ Cấu hình
ADMIN_ID = 123456789  # 🔥 thay bằng Telegram user_id admin
COOLDOWN_TIME = 120
MAX_TIME = 200

# Cooldown riêng từng user
user_cooldowns = {}

# Hàng đợi
queue = asyncio.Queue()
is_running = False

# Tiến trình đang chạy {user_id: process}
running_processes = {}

# Trạng thái bot (active hay bị dừng bởi admin)
bot_active = True


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Xin chào, tôi là bot panel SMS. Gõ /help để xem hướng dẫn sử dụng.")


# /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hướng dẫn sử dụng:\n"
        "/sms <số điện thoại> <time> <delay> <luồng>\n\n"
        "Ví dụ:\n"
        "/sms 0123456789 60 1 5\n\n"
        f"⚠️ Lưu ý:\n- Thời gian tối đa {MAX_TIME} giây.\n"
        f"- Mỗi người có cooldown {COOLDOWN_TIME} giây.\n"
        "- Bot chỉ hoạt động trong group.\n"
        "- Nếu có người đang chạy, yêu cầu sẽ vào hàng đợi.\n"
        "- Admin có thể dừng bot bằng /stopbot và bật lại bằng /startbot."
    )


# worker xử lý hàng đợi
async def worker():
    global is_running
    while True:
        chat_id, user_id, cmd, msg_context, msg_id, user_name = await queue.get()
        is_running = True
        try:
            await msg_context.bot.send_message(
                chat_id=chat_id,
                text=f"🚀 Đang thực thi yêu cầu của {mention_html(user_id, user_name)}:\n<code>{' '.join(cmd)}</code>",
                parse_mode="HTML",
                reply_to_message_id=msg_id
            )

            # chạy tool
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            running_processes[user_id] = process

            process.wait()  # chờ tool xong

            if process.returncode == 0:
                await msg_context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Hoàn thành yêu cầu của {mention_html(user_id, user_name)}.",
                    parse_mode="HTML"
                )
            else:
                await msg_context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Tool kết thúc với mã lỗi {process.returncode}.",
                    parse_mode="HTML"
                )

            running_processes.pop(user_id, None)

            # báo hàng đợi (chỉ khi bot chưa bị stopbot)
            if bot_active:
                if not queue.empty():
                    await msg_context.bot.send_message(chat_id=chat_id, text="📌 Người tiếp theo trong hàng đợi sẽ được chạy ngay.")
                else:
                    await msg_context.bot.send_message(chat_id=chat_id, text="📌 Hiện không còn yêu cầu trong hàng đợi.")
            else:
                await msg_context.bot.send_message(chat_id=chat_id, text="🛑 Bot đã dừng. Không nhận thêm lệnh mới.")

        except Exception as e:
            await msg_context.bot.send_message(chat_id=chat_id, text=f"❌ Lỗi: {e}")
        finally:
            is_running = False
            queue.task_done()


# /sms
async def sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    if not bot_active:
        await update.message.reply_text("🛑 Bot đã bị admin dừng, không thể sử dụng /sms nữa.")
        return

    if update.message.chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("⚠️ Lệnh này chỉ dùng trong group.")
        return

    user = update.message.from_user
    user_id = user.id
    user_name = user.first_name
    now = time.time()

    # cooldown riêng
    if user_id in user_cooldowns and now - user_cooldowns[user_id] < COOLDOWN_TIME:
        remaining = int(COOLDOWN_TIME - (now - user_cooldowns[user_id]))
        await update.message.reply_text(
            f"⏳ {mention_html(user_id, user_name)}, bạn cần chờ {remaining} giây nữa.",
            parse_mode="HTML"
        )
        return

    if len(context.args) != 4:
        await update.message.reply_text("⚠️ Sai cú pháp! Dùng: /sms <sdt> <time> <delay> <luồng>")
        return

    sdt, time_arg, delay, luong = context.args

    try:
        time_arg = int(time_arg)
    except ValueError:
        await update.message.reply_text("⚠️ Tham số time phải là số nguyên.")
        return

    if time_arg > MAX_TIME:
        await update.message.reply_text(f"⚠️ Thời gian tối đa là {MAX_TIME} giây.")
        return

    # Ghi nhận cooldown
    user_cooldowns[user_id] = now

    # Tạo command
    cmd = ["python", "sms.py", sdt, str(time_arg), delay, luong]

    # Đưa vào hàng đợi
    await queue.put((update.message.chat_id, user_id, cmd, context, update.message.message_id, user_name))
    if is_running:
        await update.message.reply_text("📌 Yêu cầu đã vào hàng đợi. Vui lòng chờ người trước chạy xong.")
    else:
        await update.message.reply_text("🚀 Yêu cầu đang được thực thi ngay.")


# /stopbot (chỉ admin)
async def stopbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bạn không có quyền dùng lệnh này.")
        return

    bot_active = False
    # clear toàn bộ queue
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except:
            break

    await update.message.reply_text("🛑 Bot đã bị dừng. Hàng đợi đã bị xoá. Tiến trình hiện tại sẽ chạy xong rồi dừng.")


# /startbot (chỉ admin)
async def startbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bạn không có quyền dùng lệnh này.")
        return

    bot_active = True
    await update.message.reply_text("✅ Bot đã được bật lại, mọi người có thể tiếp tục sử dụng.")


# Main
async def main():
    TOKEN = "8436373433:AAFGzm5Bshd3QZlhl-he5cWb3isdMd0MKxA"  # 🔥 thay token thật
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sms", sms))
    app.add_handler(CommandHandler("stopbot", stopbot))
    app.add_handler(CommandHandler("startbot", startbot))

    # chạy worker song song
    asyncio.create_task(worker())

    print("Bot đang chạy...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())