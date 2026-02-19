import logging
import asyncio
import os
import tempfile
from aiohttp import web
import pdfplumber
import docx2txt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Пороги размеров файлов
BIG_FILE_THRESHOLD = 5 * 1024 * 1024      # 5 МБ
MAX_TELEGRAM_SIZE = 20 * 1024 * 1024       # 20 МБ

# ========== СЛОВАРЬ ПЕРЕВОДОВ ==========
TEXTS = {
    'ru': {
        'language_selected': '✅ Язык установлен: русский',
        'start': (
            "👋 Привет! Я бот для очистки текста от скрытых символов.\n\n"
            "📝 Отправь мне текст или файл (TXT, DOCX, PDF), и я покажу количество скрытых символов.\n\n"
            "💰 Очистка текста — 10 Stars (до 1000 знаков)\n\n"
            "📁 Файлы до 5 МБ обрабатываются быстро, от 5 до 20 МБ — в фоне (нужно подождать).\n"
            "❌ Файлы больше 20 МБ не принимаются из‑за ограничений Telegram.\n\n"
            "Используй /help для списка команд."
        ),
        'help': (
            "📚 Список команд:\n"
            "/start - Начать работу\n"
            "/language - Выбрать язык\n"
            "/help - Показать это сообщение\n\n"
            "📌 Просто отправь текст или файл, и я проверю скрытые символы."
        ),
        'choose_language': '🌐 Пожалуйста, выберите язык:',
        'file_too_big': '❌ Файл слишком большой (максимум 20 МБ). Пожалуйста, отправьте файл меньшего размера.',
        'file_big_background': (
            "⏳ Файл большой (>5 МБ). Начинаю обработку в фоне, это может занять некоторое время.\n"
            "Я пришлю результат сюда, как только закончу."
        ),
        'file_processing': '⏳ Обрабатываю файл...',
        'text_clean': '✅ Текст чистый! Скрытых символов не найдено.',
        'file_clean': '✅ Файл чистый! Скрытых символов не найдено.',
        'hidden_found': '🔍 Найдено скрытых символов: {count}\n\n📄 Фрагмент текста:\n{preview}\n\n💰 Очистить за 10 Stars\n📏 Длина: {length} знаков',
        'clean_button': '✨ Очистить за 10 Stars',
        'clean_placeholder': '🧹 Очистка будет доступна после подключения Stars.',
        'unsupported_format': '❌ Поддерживаются только TXT, DOCX, PDF',
        'extract_failed': '❌ Не удалось извлечь текст из файла',
        'download_error': '❌ Не удалось скачать файл: {error}',
        'processing_error': '❌ Ошибка при обработке файла: {error}',
    },
    'en': {
        'language_selected': '✅ Language set: English',
        'start': (
            "👋 Hello! I'm a bot for cleaning text from hidden characters.\n\n"
            "📝 Send me text or a file (TXT, DOCX, PDF), and I'll show you the number of hidden characters.\n\n"
            "💰 Text cleaning — 10 Stars (up to 1000 characters)\n\n"
            "📁 Files up to 5 MB are processed quickly, from 5 to 20 MB — in the background (please wait).\n"
            "❌ Files larger than 20 MB are not accepted due to Telegram limitations.\n\n"
            "Use /help for command list."
        ),
        'help': (
            "📚 Command list:\n"
            "/start - Start the bot\n"
            "/language - Choose language\n"
            "/help - Show this message\n\n"
            "📌 Just send text or a file, and I'll check for hidden characters."
        ),
        'choose_language': '🌐 Please choose language:',
        'file_too_big': '❌ File is too large (maximum 20 MB). Please send a smaller file.',
        'file_big_background': (
            "⏳ File is large (>5 MB). Starting background processing, it may take some time.\n"
            "I'll send the result here when it's done."
        ),
        'file_processing': '⏳ Processing file...',
        'text_clean': '✅ Text is clean! No hidden characters found.',
        'file_clean': '✅ File is clean! No hidden characters found.',
        'hidden_found': '🔍 Hidden characters found: {count}\n\n📄 Text snippet:\n{preview}\n\n💰 Clean for 10 Stars\n📏 Length: {length} characters',
        'clean_button': '✨ Clean for 10 Stars',
        'clean_placeholder': '🧹 Cleaning will be available after Stars integration.',
        'unsupported_format': '❌ Only TXT, DOCX, PDF are supported',
        'extract_failed': '❌ Failed to extract text from file',
        'download_error': '❌ Failed to download file: {error}',
        'processing_error': '❌ Error processing file: {error}',
    }
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_text(context: ContextTypes.DEFAULT_TYPE, key: str, **kwargs) -> str:
    lang = context.user_data.get('language', 'ru')
    text = TEXTS[lang].get(key, f"Missing translation: {key}")
    if kwargs:
        text = text.format(**kwargs)
    return text

# ========== ФУНКЦИИ ОЧИСТКИ ==========
def clean_text(text):
    replacements = {
        '\u00A0': ' ', '\u202F': ' ', '\u200B': '', '\u200C': '', '\u200D': '',
        '\u200E': '', '\u200F': '', '\u00AD': '', '\u2011': '-', '\u2013': '-',
        '\u2014': '-', '\u2018': "'", '\u2019': "'", '\u201C': '"', '\u201D': '"',
        '\u2026': '...', '\uFEFF': ''
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def count_hidden_chars(text):
    hidden_chars = [
        '\u00A0', '\u202F', '\u200B', '\u200C', '\u200D', '\u200E', '\u200F',
        '\u00AD', '\u2011', '\u2013', '\u2014', '\u2018', '\u2019', '\u201C',
        '\u201D', '\u2026', '\uFEFF'
    ]
    count = 0
    for char in hidden_chars:
        count += text.count(char)
    return count

# ========== ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ ФАЙЛОВ ==========
def extract_text_from_txt(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def extract_text_from_docx(file_path):
    return docx2txt.process(file_path)

def extract_text_from_pdf(file_path):
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

# ========== ФОНОВАЯ ОБРАБОТКА ФАЙЛА ==========
async def process_file_background(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str, file_name: str):
    try:
        text = ""
        if file_name.endswith('.txt'):
            text = extract_text_from_txt(file_path)
        elif file_name.endswith('.docx'):
            text = extract_text_from_docx(file_path)
        elif file_name.endswith('.pdf'):
            text = extract_text_from_pdf(file_path)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, 'unsupported_format')
            )
            return

        if not text.strip():
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, 'extract_failed')
            )
            return

        hidden = count_hidden_chars(text)
        if hidden == 0:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, 'file_clean')
            )
            return

        preview = text[:200] + "..." if len(text) > 200 else text
        reply_text = get_text(
            context,
            'hidden_found',
            count=hidden,
            preview=preview,
            length=len(text)
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=reply_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(context, 'clean_button'), callback_data="clean")]])
        )
    except Exception as e:
        logger.exception("Ошибка при фоновой обработке файла")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=get_text(context, 'processing_error', error=str(e)[:100])
        )
    finally:
        if os.path.exists(file_path):
            os.unlink(file_path)

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
async def handle_http(request):
    return web.Response(text="Bot is running")

async def run_web_server():
    app_web = web.Application()
    app_web.router.add_get('/', handle_http)
    app_web.router.add_get('/ping', handle_http)
    port = int(os.environ.get('PORT', 10000))
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")
    # keep the server running forever
    await asyncio.Event().wait()

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'language' not in context.user_data:
        intro_text = (
            "👋 Hello! I'm a bot for cleaning text from hidden characters.\n"
            "👋 Привет! Я бот для очистки текста от скрытых символов.\n\n"
            "Please choose your language / Пожалуйста, выберите язык:"
        )
        keyboard = [
            [InlineKeyboardButton("Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("English", callback_data="lang_en")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(intro_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(get_text(context, 'start'))

async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("English", callback_data="lang_en")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        get_text(context, 'choose_language'),
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_text(context, 'help'))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'language' not in context.user_data:
        await language_selection(update, context)
        return

    text = update.message.text
    hidden = count_hidden_chars(text)
    if hidden == 0:
        await update.message.reply_text(get_text(context, 'text_clean'))
        return
    preview = text[:200] + "..." if len(text) > 200 else text
    reply_text = get_text(
        context,
        'hidden_found',
        count=hidden,
        preview=preview,
        length=len(text)
    )
    await update.message.reply_text(
        reply_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(context, 'clean_button'), callback_data="clean")]])
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'language' not in context.user_data:
        await language_selection(update, context)
        return

    file = update.message.document
    file_size = file.file_size
    file_name = file.file_name.lower()

    if file_size > MAX_TELEGRAM_SIZE:
        await update.message.reply_text(get_text(context, 'file_too_big'))
        return

    if file_size > BIG_FILE_THRESHOLD:
        await update.message.reply_text(get_text(context, 'file_big_background'))
    else:
        await update.message.reply_text(get_text(context, 'file_processing'))

    try:
        tg_file = await file.get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix="_" + file.file_name) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        asyncio.create_task(process_file_background(update, context, tmp_path, file_name))
    except Exception as e:
        logger.exception("Ошибка при скачивании файла")
        await update.message.reply_text(get_text(context, 'download_error', error=str(e)[:100]))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('lang_'):
        lang = query.data.split('_')[1]
        context.user_data['language'] = lang
        await query.edit_message_text(get_text(context, 'language_selected'))
        await query.message.reply_text(get_text(context, 'start'))
    elif query.data == "clean":
        await query.edit_message_text(get_text(context, 'clean_placeholder'))

# ========== ЗАПУСК БОТА И ВЕБ-СЕРВЕРА ==========
async def main():
    token = os.environ.get('TELEGRAM_TOKEN', "8464092666:AAFMjdZKgy9D3yzcTo8aM2S33GornzPYZ4g")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_selection))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Запускаем polling в фоне
    asyncio.create_task(app.run_polling())

    # Запускаем веб-сервер (будет работать вечно)
    await run_web_server()

if __name__ == "__main__":
    asyncio.run(main())
