import logging
import os
import re
import requests
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Callable, Any

# ⚙️ إعدادات البوت والـ Logging
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=LOG_LEVEL,
)
logger = logging.getLogger(__name__)

# 📝 مسار مؤقت وتخزين
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
FILE_ID_CACHE = {} 
MAX_TELEGRAM_SIZE_MB = 1950 

# 💨 مُنفّذ المهام في الخلفية
executor = ThreadPoolExecutor(max_workers=4) 

# -----------------------------------------------------
# 📚 وظائف المساعدة (Helper Functions)
# -----------------------------------------------------

# 🔄 الابتكار الرابع عشر: دالة إعادة المحاولة للرفع
async def retry_upload(func: Callable, max_retries: int = 3, delay: int = 5, *args, **kwargs) -> Any:
    """إعادة محاولة تنفيذ دالة الرفع في حالة فشل الاتصال."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            logger.warning(f"Upload attempt {attempt + 1}/{max_retries} failed. Retrying in {delay}s...")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                raise last_exception
    return None

# ⚙️ وظيفة التحديث الذاتي لـ yt-dlp (تبقى كما هي)
def self_update_ytdlp():
    """تجبر yt-dlp على تحديث نفسه عند بدء التشغيل."""
    try:
        logger.info("Attempting yt-dlp self-update...")
        YoutubeDL({'quiet': True}).download(['ytsearch:yt-dlp --update'])
        logger.info("yt-dlp self-update complete.")
    except Exception as e:
        logger.error(f"yt-dlp self-update failed: {e}")

# 📊 وظيفة لعرض شريط التقدم (تبقى كما هي)
def progress_hook_factory(update_func, total_bytes):
    # ... (الكود يبقى كما هو)
    """Factory لإنشاء خطاف تقدم ذكي لتحديث رسالة تيليجرام."""
    last_percent = -1
    last_update_time = 0
    
    async def progress_hook(d):
        nonlocal last_percent, last_update_time
        
        if d['status'] == 'downloading':
            current_time = d.get('elapsed') or 0
            if d.get('total_bytes') is None or d.get('downloaded_bytes') is None:
                return
                
            percent_f = d['downloaded_bytes'] * 100 / total_bytes if total_bytes else 0
            percent = int(percent_f)
            
            if percent != last_percent and (current_time - last_update_time > 1 or percent % 10 == 0):
                last_percent = percent
                last_update_time = current_time
                
                filled_length = int(20 * percent / 100)
                bar = '█' * filled_length + '░' * (20 - filled_length)
                
                status_text = (
                    f"**{percent}%** | `{bar}`\n"
                    f"⬇️ {d['downloaded_bytes'] / (1024*1024):.2f}MB / {total_bytes / (1024*1024):.2f}MB"
                )
                try:
                    await update_func(text=f"🔥 **جاري التنزيل...**\n{status_text}")
                except Exception as e:
                    logger.debug(f"Progress update failed: {e}") 

    return progress_hook

# -----------------------------------------------------
# ⚡️ الوظيفة الأساسية: معالج رابط الفيسبوك
# -----------------------------------------------------

async def handle_facebook_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """المُعالج الرئيسي: فحص، تنزيل، وإرسال الفيديو."""
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    
    # 📌 الابتكار الثاني عشر: جلب الرابط الحقيقي (لروابط fb.watch)
    original_url = url
    if "fb.watch" in url:
        try:
            response = requests.head(url, allow_redirects=True, timeout=5)
            url = response.url 
        except Exception:
            url = original_url 
    
    # ... (بقية التحقق من الـ Cache) ...
    if url in FILE_ID_CACHE:
        # ... (كود إرسال الـ Cache) ...
        try:
            await context.bot.send_video(
                chat_id=chat_id, 
                video=FILE_ID_CACHE[url], 
                caption="✅ تم الإرسال من الذاكرة المؤقتة! (توفير في الوقت والموارد)."
            )
            return
        except Exception:
            # 🧹 الابتكار الثالث عشر: حذف الـ Cache إذا فشل إرسال الـ file_id
            FILE_ID_CACHE.pop(url, None)
            logger.warning(f"Failed to send cached video for {url}. Cache entry deleted.")


    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ يتم معالجة الرابط... **تفعيل نظام المحاولات الثلاثية!**"
    )
    
    async def update_progress_message(text):
        # ... (كود تحديث الرسالة) ...
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message.message_id, 
            text=text,
            parse_mode='Markdown'
        )

    file_name = f"fb_video_{chat_id}_{update.update_id}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, file_name)
    download_successful = False
    video_title = 'فيديو فيسبوك'
    total_bytes = 0
    selected_format_string = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best' 
    final_error = None
    
    # -----------------------------------------------------
    # 🛡️ 0. الفحص المخصص وحجم الملف (Pre-Flight Check)
    # -----------------------------------------------------
    # ... (كود الفحص المخصص والتكيُّف الديناميكي يبقى كما هو) ...
    
    # ... (استراتيجية المحاولة الثلاثية تبقى كما هي) ...

    # -----------------------------------------------------
    # 📤 خطوة الإرسال والتنظيف
    # -----------------------------------------------------
    
    if download_successful and os.path.exists(filepath):
        upload_message = await context.bot.send_message(
            chat_id=chat_id,
            text="⏫ **جاري رفع الملف إلى تيليجرام...** (قد يستغرق وقتاً للملفات الكبيرة)"
        )
        
        try:
            caption_text = f"✅ تم التنزيل بنجاح! \n📽️ العنوان: **{video_title[:80]}**"
            
            # 🔄 الابتكار الرابع عشر: استخدام دالة إعادة محاولة الرفع
            with open(filepath, 'rb') as video_file:
                # دالة الرفع الفعلية
                async def actual_upload_func():
                    video_file.seek(0) # التأكد من قراءة الملف من البداية
                    return await context.bot.send_video(
                        chat_id=chat_id,
                        video=video_file,
                        caption=caption_text
                    )
                
                result = await retry_upload(actual_upload_func)
                
            FILE_ID_CACHE[original_url] = result.video.file_id # التخزين المؤقت بالرابط الأصلي
            await message.delete() 
            await upload_message.delete()
            
        except Exception as e:
            final_error = f"خطأ في الرفع: {e}"
            logger.error(f"Final error sending video to Telegram: {e}")
            await upload_message.delete()
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ فشل الرفع بعد محاولات عديدة. (قد يكون حجم الملف تجاوز الحد الأقصى)."
            )
            # 🧹 الابتكار الثالث عشر: عند فشل الرفع، يجب مسح الملف
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Hard cleanup: Deleted file after failed upload: {filepath}")
            return # إنهاء الدالة هنا

    else:
        # فشل نهائي لجميع المحاولات
        final_error_msg = f"❌ فشل التنزيل النهائي!\n"
        # ... (كود الإبلاغ عن الخطأ النهائي) ...
        
    # 🗑️ التنظيف النهائي
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Cleaned up file: {filepath}")

# ... (دالة main تبقى كما هي) ...
def main() -> None:
    """تشغيل البوت."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
        return
        
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(facebook\.com|fb\.watch)"), handle_facebook_link))

    logger.info("Bot is running...")
    application.job_queue.run_once(lambda context: self_update_ytdlp(), 1)
    
    application.run_polling(poll_interval=1) 

if __name__ == "__main__":
    main()
