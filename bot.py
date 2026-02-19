import logging
import asyncio
import os
import tempfile
from aiohttp import web
import pdfplumber
import docx2txt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Пороги размеров файлов
BIG_FILE_THRESHOLD = 5 * 1024 * 1024      # 5 МБ
MAX_TELEGRAM_SIZE = 20 * 1024 * 1024       # 20 МБ

# Цены в Stars (для разных диапазонов)
PRICE_FREE_LIMIT = 500          # бесплатно до 500 знаков
PRICE_TIER1_LIMIT = 1000         # 1 Star до 1000 знаков
PRICE_TIER1 = 1
PRICE_TIER2_LIMIT = 10000        # 10 Stars до 10000 знаков
PRICE_TIER2 = 10
PRICE_TIER3 = 50                 # 50 Stars свыше 10000 знаков

# ========== СЛОВАРЬ ПЕРЕВОДОВ ==========
TEXTS = {
    'ru': {
        'language_selected': '✅ Язык установлен: русский',
        'start': (
            "👋 Привет! Я бот для очистки текста от скрытых символов.\n\n"
            "📝 Отправь мне текст или файл (TXT, DOCX, PDF), и я БЕСПЛАТНО покажу количество скрытых символов.\n\n"
            "💰 Тарифы:\n"
            "• Проверка текста на скрытые символы — БЕСПЛАТНО\n"
            "• Очистка текста от скрытых символов до 500 знаков — БЕСПЛАТНО\n"
            "• До 1000 знаков — 1 Star\n"
            "• 1000–10000 знаков — 10 Stars\n"
            "• Более 10000 знаков — 50 Stars\n\n"
            "📁 Файлы до 5 МБ обрабатываются быстро, от 5 до 20 МБ — в фоне.\n"
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
        'donate_button': '💖 Поддержать автора',
        'donate_prompt': 'Выберите сумму доната в Stars (или нажмите /cancel для отмены):',
        'donate_thanks': '🙏 Спасибо за поддержку! Вы подарили {amount} Stars.',
        'file_too_big': '❌ Файл слишком большой (максимум 20 МБ).',
        'file_big_background': (
            "⏳ Файл большой (>5 МБ). Начинаю обработку в фоне, это может занять некоторое время.\n"
            "Я пришлю результат сюда, как только закончу."
        ),
        'file_processing': '⏳ Обрабатываю файл...',
        'text_clean': '✅ Текст чистый! Скрытых символов не найдено.',
        'file_clean': '✅ Файл чистый! Скрытых символов не найдено.',
        'hidden_found': (
            "🔍 Найдено скрытых символов: {count}\n\n"
            "📄 Фрагмент текста:\n{preview}\n\n"
            "💰 Очистка будет стоить {price} Stars.\n"
            "📏 Длина: {length} знаков"
        ),
        'clean_button': '✨ Очистить за {price} Stars',
        'payment_success': (
            "✅ Оплата прошла успешно!\n"
            "🧹 Вот очищенный текст:\n\n{cleaned_text}"
        ),
        'payment_failed': '❌ Ошибка оплаты. Попробуйте ещё раз.',
        'unsupported_format': '❌ Поддерживаются только TXT, DOCX, PDF',
        'extract_failed': '❌ Не удалось извлечь текст из файла',
        'download_error': '❌ Не удалось скачать файл: {error}',
        'processing_error': '❌ Ошибка при обработке файла: {error}',
    },
    'en': {
        'language_selected': '✅ Language set: English',
        'start': (
            "👋 Hello! I'm a bot for cleaning text from hidden characters.\n\n"
            "📝 Send me text or a file (TXT, DOCX, PDF), and I'll show the number of hidden characters for FREE.\n\n"
            "💰 Pricing:\n"
            "• Hidden characters detection — FREE\n"
            "• Cleaning up to 500 chars — FREE\n"
            "• Up to 1000 chars — 1 Star\n"
            "• 1000–10000 chars — 10 Stars\n"
            "• More than 10000 chars — 50 Stars\n\n"
            "📁 Files up to 5 MB are processed quickly, from 5 to 20 MB — in the background.\n"
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
        'donate_button': '💖 Support the author',
        'donate_prompt': 'Choose the donation amount in Stars (or /cancel to abort):',
        'donate_thanks': '🙏 Thank you for your support! You gifted {amount} Stars.',
        'file_too_big': '❌ File is too large (max 20 MB).',
        'file_big_background': (
            "⏳ File is large (>5 MB). Starting background processing, it may take some time.\n"
            "I'll send the result here when it's done."
        ),
        'file_processing': '⏳ Processing file...',
        'text_clean': '✅ Text is clean! No hidden characters found.',
        'file_clean': '✅ File is clean! No hidden characters found.',
        'hidden_found': (
            "🔍 Hidden characters found: {count}\n\n"
            "📄 Text snippet:\n{preview}\n\n"
            "💰 Cleaning will cost {price} Stars.\n"
            "📏 Length: {length} characters"
        ),
        'clean_button': '✨ Clean for {price} Stars',
        'payment_success': (
            "✅ Payment successful!\n"
            "🧹 Here is your cleaned text:\n\n{cleaned_text}"
        ),
        'payment_failed': '❌ Payment failed. Please try again.',
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

def get_price_for_length(length: int) -> int:
    """Возвращает цену в Stars в зависимости от длины текста."""
    if length <= PRICE_FREE_LIMIT:
        return 0
    elif length <= PRICE_TIER1_LIMIT:
        return PRICE_TIER1
    elif length <= PRICE_TIER2_LIMIT:
        return PRICE_TIER2
    else:
        return PRICE_TIER3

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
            # Файл чистый — показываем кнопку доната
            donate_keyboard = [[InlineKeyboardButton(get_text(context, 'donate_button'), callback_data="donate")]]
            reply_markup = InlineKeyboardMarkup(donate_keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, 'file_clean'),
                reply_markup=reply_markup
            )
            return

        length = len(text)
        price = get_price_for_length(length)

        context.user_data['pending_text'] = text
        context.user_data['pending_price'] = price
        context.user_data['pending_length'] = length

        preview = text[:200] + "..." if length > 200 else text
        reply_text = get_text(
            context,
            'hidden_found',
            count=hidden,
            preview=preview,
            price=price,
            length=length
        )

        if price == 0:
            cleaned = clean_text(text)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, 'payment_success', cleaned_text=cleaned)
            )
            context.user_data.pop('pending_text', None)
            return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=reply_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_text(context, 'clean_button', price=price),
                    callback_data="pay_clean"
                )
            ]])
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
    return runner

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'language' not in context.user_data:
        # Первый запуск — показываем выбор языка БЕЗ кнопки доната
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
        # Язык уже выбран — просто приветствие без кнопки доната
        await update.message.reply_text(get_text(context, 'start'))

async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Команда /language — только выбор языка, без доната
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
    # Справка без кнопки доната
    await update.message.reply_text(get_text(context, 'help'))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'language' not in context.user_data:
        await language_selection(update, context)
        return

    text = update.message.text
    hidden = count_hidden_chars(text)
    if hidden == 0:
        # Текст чистый — показываем кнопку доната
        donate_keyboard = [[InlineKeyboardButton(get_text(context, 'donate_button'), callback_data="donate")]]
        reply_markup = InlineKeyboardMarkup(donate_keyboard)
        await update.message.reply_text(
            get_text(context, 'text_clean'),
            reply_markup=reply_markup
        )
        return

    length = len(text)
    price = get_price_for_length(length)

    context.user_data['pending_text'] = text
    context.user_data['pending_price'] = price
    context.user_data['pending_length'] = length

    preview = text[:200] + "..." if length > 200 else text
    reply_text = get_text(
        context,
        'hidden_found',
        count=hidden,
        preview=preview,
        price=price,
        length=length
    )

    if price == 0:
        cleaned = clean_text(text)
        await update.message.reply_text(get_text(context, 'payment_success', cleaned_text=cleaned))
        context.user_data.pop('pending_text', None)
        return

    await update.message.reply_text(
        reply_text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                get_text(context, 'clean_button', price=price),
                callback_data="pay_clean"
            )
        ]])
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

# ========== ОБРАБОТЧИКИ КНОПОК И ПЛАТЕЖЕЙ ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('lang_'):
        lang = query.data.split('_')[1]
        context.user_data['language'] = lang
        await query.edit_message_text(get_text(context, 'language_selected'))
        # После выбора языка показываем приветствие без кнопки доната
        await query.message.reply_text(get_text(context, 'start'))
        return

    if query.data == "donate":
        # Показываем inline-кнопки с вариантами доната
        donate_keyboard = [
            [InlineKeyboardButton("1 ⭐️", callback_data="donate_1")],
            [InlineKeyboardButton("5 ⭐️", callback_data="donate_5")],
            [InlineKeyboardButton("10 ⭐️", callback_data="donate_10")],
            [InlineKeyboardButton("25 ⭐️", callback_data="donate_25")],
            [InlineKeyboardButton("50 ⭐️", callback_data="donate_50")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back")]
        ]
        await query.edit_message_text(
            get_text(context, 'donate_prompt'),
            reply_markup=InlineKeyboardMarkup(donate_keyboard)
        )
        return

    if query.data.startswith('donate_'):
        amount = int(query.data.split('_')[1])
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="Поддержка автора",
            description="Благодарю за ваш вклад!",
            payload="donation",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Донат", amount=amount)]
        )
        return

    if query.data == "pay_clean":
        text = context.user_data.get('pending_text', '')
        price = context.user_data.get('pending_price', 0)
        if not text:
            await query.edit_message_text("❌ Ошибка: текст не найден. Отправьте текст заново.")
            return

        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="Очистка текста",
            description="Удаление скрытых символов",
            payload="clean_text",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Очистка", amount=price)]
        )
        return

    if query.data == "back":
        # Возврат в главное меню (без кнопки доната)
        await query.edit_message_text(get_text(context, 'start'))
        return

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение готовности к оплате"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Что делать после успешной оплаты"""
    payload = update.message.successful_payment.invoice_payload
    if payload == "donation":
        amount = update.message.successful_payment.total_amount
        await update.message.reply_text(
            get_text(context, 'donate_thanks', amount=amount)
        )
    elif payload == "clean_text":
        text = context.user_data.get('pending_text', '')
        if not text:
            await update.message.reply_text("❌ Ошибка: текст не найден. Отправьте текст заново.")
            return
        cleaned = clean_text(text)
        await update.message.reply_text(
            get_text(context, 'payment_success', cleaned_text=cleaned)
        )
        context.user_data.pop('pending_text', None)

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
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # Запускаем веб-сервер
    web_runner = await run_web_server()

    # Запускаем бота
    logger.info("Starting bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        # Держим программу запущенной
        await asyncio.Event().wait()
    finally:
        # Корректное завершение
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
