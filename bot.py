import logging
import os
import re
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

# ⚙️ إعدادات البوت والـ Logging
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
PROXY_URL = os.environ.get("PROXY_URL") # 🌐 المتغير البيئي الجديد المضاد للحظر
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=LOG_LEVEL,
)
logger = logging.getLogger(__name__)

# 📝 مسار مؤقت لتخزين الفيديوهات قبل رفعها
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 🚀 معالج الأمر /start (يبقى كما هو)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة ترحيبية عند استخدام الأمر /start."""
    await update.message.reply_text(
        "مرحباً بك! أنا بوت تنزيل فيديوهات فيسبوك السريع.\n"
        "فقط أرسل لي **رابط** فيديو فيسبوك وسأتولى الأمر بسرعة فائقة!"
    )

# 📊 وظيفة لعرض شريط التقدم (الابتكار الرابع)
def progress_hook_factory(update_func, total_bytes):
    last_percent = -1
    last_update_time = 0
    
    async def progress_hook(d):
        nonlocal last_percent, last_update_time
        
        if d['status'] == 'downloading':
            # 💡 تحديث فقط إذا تغيرت النسبة أو مر وقت كافٍ (لتقليل استهلاك API تيليجرام)
            current_time = d.get('elapsed') or 0 # تقريبي
            if d.get('total_bytes') is None or d.get('downloaded_bytes') is None:
                return # لا يوجد معلومات كافية
                
            percent_f = d['downloaded_bytes'] * 100 / total_bytes if total_bytes else 0
            percent = int(percent_f)
            
            if percent != last_percent and (current_time - last_update_time > 1 or percent % 10 == 0):
                last_percent = percent
                last_update_time = current_time
                
                # بناء شريط التقدم
                filled_length = int(20 * percent / 100)
                bar = '█' * filled_length + '░' * (20 - filled_length)
                
                status_text = (
                    f"**{percent}%** | `{bar}`\n"
                    f"⬇️ {d['downloaded_bytes'] / (1024*1024):.2f}MB / {total_bytes / (1024*1024):.2f}MB"
                )
                try:
                    await update_func(text=f"🔥 **جاري التنزيل...**\n{status_text}")
                except Exception as e:
                    # قد يفشل التحديث بسبب حذف الرسالة، نتجاهل الخطأ
                    logger.debug(f"Progress update failed: {e}") 

    return progress_hook


# ⚡️ الوظيفة الأساسية: تنزيل وإرسال الفيديو (الوحش الثوري)
async def handle_facebook_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الرابط عبر استراتيجية المحاولة الثلاثية والتعدين العميق."""
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    
    # 🕵️‍♀️ التحقق الأولي
    if not re.match(r"(https?://)?(www\.)?(facebook\.com|fb\.watch)/.*", url):
        await context.bot.send_message(
            chat_id=chat_id,
            text="عفواً، الرجاء إرسال **رابط فيديو صحيح من فيسبوك**."
        )
        return

    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ يتم معالجة الرابط... **تفعيل نظام المحاولات الثلاثية!**"
    )
    
    # وظيفة مساعدة لتحديث رسالة التقدم
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
    
    # -----------------------------------------------------
    # 🛡️ 0. الابتكار الخامس: فحص حالة الفيديو (Pre-Flight Check)
    # -----------------------------------------------------
    video_title = 'فيديو فيسبوك'
    total_bytes = 0
    try:
        await update_progress_message(text="🔍 جاري فحص حالة الفيديو واستخلاص البيانات الوصفية (Pre-Flight Check)...")
        with YoutubeDL({'quiet': True, 'noprogress': True}) as ydl_meta:
            info = ydl_meta.extract_info(url, download=False)
            video_title = info.get('title', 'فيديو فيسبوك')
            
            # محاولة تقدير الحجم الإجمالي للفيديو (مهم لمؤشر التقدم)
            if 'filesize_approx' in info:
                 total_bytes = info['filesize_approx']
            elif 'requested_downloads' in info and info['requested_downloads']:
                total_bytes = info['requested_downloads'][0]['filesize']
            
            if not total_bytes:
                # حجم افتراضي لتجنب القسمة على صفر في شريط التقدم
                total_bytes = 10 * 1024 * 1024 # 10MB
                
            await update_progress_message(
                text=f"✅ تم فحص الفيديو بنجاح. الحجم المقدر: **{total_bytes / (1024*1024):.2f}MB**.\n"
                     f"**العنوان:** {video_title[:80]}"
            )

    except ExtractorError as e:
        logger.error(f"Pre-Flight Check Failed for {url}: {e}")
        await update_progress_message(
            text=f"❌ **فشل الفحص الأولي (Pre-Flight Check):** قد يكون الرابط خاصاً أو محذوفاً. {str(e)}"
        )
        return # التوقف هنا لعدم إضاعة الموارد
    except Exception as e:
        logger.error(f"Unexpected error during Pre-Flight Check: {e}")
        total_bytes = 50 * 1024 * 1024 # حجم افتراضي أكبر كحل بديل
        await update_progress_message(text="⚠️ فشل الفحص، جاري البدء بالتنزيل بشكل مباشر...")


    # 🛠️ إعدادات yt-dlp الأساسية
    base_ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': filepath,
        'noplaylist': True,
        'retries': 3,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook_factory(update_progress_message, total_bytes)], # 🚀 إضافة مؤشر التقدم
    }

    # -----------------------------------------------------
    # 🚀 استراتيجية المحاولة الثلاثية 🚀
    # -----------------------------------------------------

    # 1. المحاولة الأولى: السريعة والعادية
    if not download_successful:
        try:
            await update_progress_message(text="🚀 المحاولة 1/3: تنزيل سريع وعادي...")
            with YoutubeDL(base_ydl_opts) as ydl:
                ydl.download([url])
            download_successful = True
        except DownloadError:
            logger.warning("Attempt 1 failed. Moving to Aggressive.")

    # 2. المحاولة الثانية: الثورية والعدوانية (محاكاة متصفح كاملة)
    if not download_successful:
        aggressive_ydl_opts = base_ydl_opts.copy()
        aggressive_ydl_opts.update({
            'http_headers': { 
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        })
        try:
            await update_progress_message(text="🔥 المحاولة 2/3: تنزيل عدواني (محاكاة كاملة)...")
            with YoutubeDL(aggressive_ydl_opts) as ydl:
                ydl.download([url])
            download_successful = True
        except DownloadError:
            logger.warning("Attempt 2 failed. Moving to Proxy if available.")

    # 3. 🌐 المحاولة الثالثة: البروكسي المضاد للحظر (الابتكار الناري)
    if not download_successful and PROXY_URL:
        proxy_ydl_opts = base_ydl_opts.copy()
        proxy_ydl_opts.update({
            'proxy': PROXY_URL, 
            'force_generic_extractor': True, 
            'progress_hooks': [progress_hook_factory(update_progress_message, total_bytes)], # إعادة ربط مؤشر التقدم
        })
        try:
            await update_progress_message(text="🌐 المحاولة 3/3: تفعيل البروكسي (كسر الحظر)...")
            with YoutubeDL(proxy_ydl_opts) as ydl:
                ydl.download([url])
            download_successful = True
        except Exception as e:
            logger.error(f"Proxy attempt failed. Final Error for {url}: {str(e)}")


    # -----------------------------------------------------
    # 📤 خطوة الإرسال والتنظيف
    # -----------------------------------------------------
    
    if download_successful and os.path.exists(filepath):
        try:
            caption_text = f"✅ تم التنزيل بنجاح! \n📽️ العنوان: **{video_title[:80]}**"
            await update_progress_message(text=f"📤 جاري رفع الملف إلى تيليجرام...")
            
            # 🔥 الابتكار السادس: فتح الملف بشكل مباشر لتقليل الذاكرة (Memory Efficiency)
            with open(filepath, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=caption_text
                )
            await message.delete() # حذف رسالة "جاري المعالجة"
            
        except Exception as e:
            logger.error(f"Error sending video to Telegram (Check file size): {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ تم تنزيل الفيديو لكن حدث خطأ أثناء رفعه إلى تيليجرام. (قد يكون حجم الملف أكبر من 2000 ميجابايت!)"
            )
    else:
        # فشل نهائي لجميع المحاولات
        final_error_msg = "❌ فشل التنزيل النهائي! تأكد من أن الرابط عام ومتاح وغير محظور جغرافياً. (جرب إضافة PROXY_URL)"
        await update_progress_message(
            text=final_error_msg
        )

    # 🗑️ التنظيف النهائي (ضروري)
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Cleaned up file: {filepath}")

# 🏃 دالة التشغيل الرئيسية (تبقى كما هي)
def main() -> None:
    """تشغيل البوت."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
        return
        
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(facebook\.com|fb\.watch)"), handle_facebook_link))

    logger.info("Bot is running...")
    application.run_polling(poll_interval=1) 

if __name__ == "__main__":
    main()
