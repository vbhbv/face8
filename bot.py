import logging
import os
import re
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ⚙️ إعدادات البوت والـ Logging
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") # يجب تعيين هذا المتغير في Railway
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=LOG_LEVEL,
)
logger = logging.getLogger(__name__)

# 📝 مسار مؤقت لتخزين الفيديوهات قبل رفعها
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 🚀 معالج الأمر /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة ترحيبية عند استخدام الأمر /start."""
    await update.message.reply_text(
        "مرحباً بك! أنا بوت تنزيل فيديوهات فيسبوك السريع.\n"
        "فقط أرسل لي **رابط** فيديو فيسبوك وسأتولى الأمر بسرعة فائقة!"
    )

# ⚡️ الوظيفة الأساسية: تنزيل وإرسال الفيديو
async def handle_facebook_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الرابط المُرسل وتنزيل فيديو فيسبوك."""
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    
    # 🕵️‍♀️ التحقق من أن الرابط هو رابط فيسبوك (تحقق مبدئي لزيادة الكفاءة)
    if not re.match(r"(https?://)?(www\.)?(facebook\.com|fb\.watch)/.*", url):
        await context.bot.send_message(
            chat_id=chat_id,
            text="عفواً، الرجاء إرسال **رابط فيديو صحيح من فيسبوك**."
        )
        return

    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ يتم معالجة الرابط وبدء التنزيل... الرجاء الانتظار."
    )
    
    # 💡 توليد اسم ملف فريد (بابتكار لضمان عدم تضارب الملفات)
    file_name = f"fb_video_{chat_id}_{update.update_id}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, file_name)

    try:
        # 🛠️ إعدادات yt-dlp للسرعة والجودة (ابتكار: استخدام تنسيق mp4 مُحدد)
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': filepath,
            'noplaylist': True,
            'verbose': False,
            'no_warnings': True,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            # 🚀 سرعة وموثوقية: استخدام yt-dlp لتحليل وتنزيل الفيديو
            ydl.download([url])
            
        # 📤 إرسال الملف إلى تيليجرام كفيديو (مبتكر: استخدام send_video)
        # هذا يضمن أن يظهر الفيديو كفيديو قابل للتشغيل وليس ملفاً عادياً
        with open(filepath, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=f"✅ تم التنزيل بنجاح! \n**البوت السريع والموثوق!**"
            )
        
        await message.delete() # حذف رسالة "جاري المعالجة"

    except DownloadError as e:
        logger.error(f"Download Error for URL {url}: {e}")
        error_msg = "❌ لم نتمكن من تنزيل الفيديو. تأكد من أن الرابط عام ومتاح."
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=error_msg
        )
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        error_msg = "🚨 حدث خطأ غير متوقع. جرب رابطاً آخر."
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=error_msg
        )
    finally:
        # 🗑️ تنظيف الملف بعد الرفع (مهم جدًا لبيئة الاستضافة مثل Railway)
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up file: {filepath}")

# 🏃 دالة التشغيل الرئيسية
def main() -> None:
    """تشغيل البوت."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
        return
        
    # 💡 تطبيق جديد (مبتكر: استخدام ApplicationBuilder)
    application = Application.builder().token(TOKEN).build()

    # 🤝 إضافة معالجات الأوامر والرسائل
    application.add_handler(CommandHandler("start", start_command))
    # 🧠 المرشحات (Filters) لمعالجة الرسائل النصية التي تبدو كروابط فيسبوك فقط
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(facebook\.com|fb\.watch)"), handle_facebook_link))

    # 👂 بدء تشغيل البوت
    logger.info("Bot is running...")
    application.run_polling(poll_interval=1) # استخدام poll_interval لزيادة سرعة الاستجابة

if __name__ == "__main__":
    main()

