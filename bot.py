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
from typing import Callable, Any
import time

# -----------------------------------------------------
# ⚙️ الإعدادات والموارد العالمية
# -----------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=LOG_LEVEL,
)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
FILE_ID_CACHE = {} 
MAX_TELEGRAM_SIZE_MB = 1950 

executor = ThreadPoolExecutor(max_workers=4) 

# -----------------------------------------------------
# 📚 الدوال المساعدة والمعالجة (Handlers)
# -----------------------------------------------------

# 🚀 معالج الأمر /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة ترحيبية عند استخدام الأمر /start."""
    await update.message.reply_text(
        "مرحباً بك! أنا بوت تنزيل فيديوهات فيسبوك السريع.\n"
        "**تم تفعيل نظام التحديث الذاتي لـ yt-dlp لضمان أعلى كفاءة.**\n"
        "فقط أرسل لي **رابط** فيديو فيسبوك وسأتولى الأمر بسرعة فائقة!"
    )

# ⚙️ وظيفة التحديث الذاتي لـ yt-dlp 
def self_update_ytdlp():
    """تجبر yt-dlp على تحديث نفسه عند بدء التشغيل."""
    try:
        logger.info("Attempting yt-dlp self-update...")
        # استخدام yt_dlp مباشرة للتحديث
        YoutubeDL({'quiet': True}).download(['ytsearch:yt-dlp --update'])
        logger.info("yt-dlp self-update complete.")
    except Exception as e:
        logger.error(f"yt-dlp self-update failed: {e}")

# 📊 وظيفة لعرض شريط التقدم 
def progress_hook_factory(update_func, total_bytes):
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

# 🔄 دالة إعادة المحاولة للرفع
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

# ⚡️ الوظيفة الأساسية: معالج رابط الفيسبوك
async def handle_facebook_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """المُعالج الرئيسي: فحص، تنزيل، وإرسال الفيديو."""
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    
    # 📌 جلب الرابط الحقيقي (لروابط fb.watch)
    original_url = url
    if "fb.watch" in url:
        try:
            response = requests.head(url, allow_redirects=True, timeout=5)
            url = response.url 
        except Exception:
            url = original_url 
    
    # 💾 التحقق من الـ Cache
    if url in FILE_ID_CACHE:
        try:
            await context.bot.send_video(
                chat_id=chat_id, 
                video=FILE_ID_CACHE[url], 
                caption="✅ تم الإرسال من الذاكرة المؤقتة! (توفير في الوقت والموارد)."
            )
            return
        except Exception:
            FILE_ID_CACHE.pop(url, None)
            logger.warning(f"Failed to send cached video for {url}. Cache entry deleted.")


    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ يتم معالجة الرابط... **تفعيل نظام المحاولات الثلاثية!**"
    )
    
    async def update_progress_message(text):
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
    try:
        await update_progress_message(text="🔍 جاري فحص حالة الفيديو واستخلاص البيانات الوصفية (Pre-Flight Check)...")
        with YoutubeDL({'quiet': True, 'noprogress': True}) as ydl_meta:
            info = await asyncio.get_event_loop().run_in_executor(
                executor, lambda: ydl_meta.extract_info(url, download=False)
            )
            
            video_title = info.get('title', 'فيديو فيسبوك')
            total_bytes = info.get('filesize_approx') or info.get('filesize')
            
            # 📏 التكيُّف الديناميكي للجودة
            if total_bytes and total_bytes > (MAX_TELEGRAM_SIZE_MB * 1024 * 1024):
                formats = info.get('formats', [])
                for fmt in formats:
                    if fmt.get('height') in [720, 480] and fmt.get('ext') == 'mp4' and fmt.get('acodec') != 'none':
                        selected_format_string = f"bestvideo[height<={fmt['height']}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                        total_bytes = fmt.get('filesize') or total_bytes * 0.5 
                        await update_progress_message(text="⚠️ تم اكتشاف حجم ملف كبير! جاري التكيُّف واختيار جودة أقل لضمان الرفع.")
                        break
            
            if not total_bytes:
                total_bytes = 10 * 1024 * 1024 
                
            await update_progress_message(
                text=f"✅ تم فحص الفيديو بنجاح. الحجم المقدر: **{total_bytes / (1024*1024):.2f}MB**.\n"
                     f"**العنوان:** {video_title[:80]}"
            )

    except ExtractorError as e:
        error_detail = str(e).split('\n')[-1].strip()
        logger.error(f"Pre-Flight Check Failed: {error_detail}")
        await update_progress_message(
            text=f"❌ **فشل الفحص الأولي:**\n`{error_detail}`\nقد يكون الرابط خاصاً أو محذوفاً."
        )
        return
    except Exception as e:
        logger.error(f"Unexpected error during Pre-Flight Check: {e}")
        total_bytes = 50 * 1024 * 1024
        await update_progress_message(text="⚠️ فشل الفحص، جاري البدء بالتنزيل بشكل مباشر...")


    # 🛠️ إعدادات yt-dlp الأساسية
    base_ydl_opts = {
        'format': selected_format_string, 
        'outtmpl': filepath,
        'noplaylist': True,
        'retries': 3,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook_factory(update_progress_message, total_bytes)],
    }

    # -----------------------------------------------------
    # 🚀 استراتيجية المحاولة الثلاثية
    # -----------------------------------------------------

    def attempt_download(url, opts):
        """وظيفة يتم تشغيلها في ThreadPoolExecutor."""
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

    attempts = [
        ("🚀 المحاولة 1/3: تنزيل سريع وعادي...", base_ydl_opts),
        ("🔥 المحاولة 2/3: تنزيل عدواني (محاكاة كاملة)...", {
            **base_ydl_opts,
            'http_headers': { 
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        }),
        ("💣 المحاولة 3/3: تفعيل استراتيجية انتحال المُرجع (Spoofing)...", {
            **base_ydl_opts,
            'referer': 'https://www.facebook.com/', 
            'http_headers': { 
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        })
    ]
    
    for status_message, opts in attempts:
        if download_successful: break
        
        await update_progress_message(text=status_message)
        try:
            await asyncio.get_event_loop().run_in_executor(
                executor, attempt_download, url, opts
            )
            download_successful = True
        except DownloadError as e:
            final_error = str(e).split('\n')[-1].strip()
            logger.warning(f"Attempt failed: {final_error}")
        except Exception as e:
            final_error = f"خطأ عام: {str(e)}"
            logger.error(f"Attempt failed with general error: {final_error}")


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
            
            with open(filepath, 'rb') as video_file:
                # دالة الرفع الفعلية
                async def actual_upload_func():
                    video_file.seek(0)
                    return await context.bot.send_video(
                        chat_id=chat_id,
                        video=video_file,
                        caption=caption_text
                    )
                
                result = await retry_upload(actual_upload_func)
                
            FILE_ID_CACHE[original_url] = result.video.file_id
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
            # 🧹 التنظيف الصارم عند فشل الرفع
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Hard cleanup: Deleted file after failed upload: {filepath}")
            return 

    else:
        # فشل نهائي لجميع المحاولات
        final_error_msg = f"❌ فشل التنزيل النهائي!\n"
        if final_error:
            final_error_msg += f"**سبب الخطأ:** `{final_error}`"
        else:
             final_error_msg += "الرابط غير صالح أو محمي."
             
        await update_progress_message(
            text=final_error_msg
        )

    # 🗑️ التنظيف النهائي
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Cleaned up file: {filepath}")


# -----------------------------------------------------
# 🏃 الدالة الرئيسية (main)
# -----------------------------------------------------

def main() -> None:
    """تشغيل البوت."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
        return
        
    # البناء الصحيح: Application بدلاً من Updater
    application = Application.builder().token(TOKEN).build()

    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(facebook\.com|fb\.watch)"), handle_facebook_link))

    logger.info("Bot is running...")
    # تنفيذ التحديث الذاتي لـ yt-dlp عند بدء التشغيل
    application.job_queue.run_once(lambda context: self_update_ytdlp(), 1)
    
    # تشغيل البوت
    application.run_polling(poll_interval=1) 

if __name__ == "__main__":
    # هذا السطر يضمن أن main() هي الدالة الوحيدة التي تعمل
    main()
