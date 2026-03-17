import os
import random
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes, ConversationHandler

from models import (
    LaundryStatus,
    Listing,
    LostFoundItem,
    OfficialAnnouncement,
    SupportTicket,
    UserProfile,
    ZoneBooking,
)

TYPE, CATEGORY, DESCRIPTION, CONTACT, PHOTO = range(5)
AUTH_EMAIL, AUTH_CODE = range(10, 12)
LF_TYPE, LF_TITLE, LF_DESCRIPTION, LF_CONTACT, LF_PHOTO = range(20, 25)
BOOK_ZONE_NAME, BOOK_ZONE_SLOT = range(30, 32)
TICKET_THEME, TICKET_DESCRIPTION, TICKET_PHOTO = range(40, 43)

BTN_START = "🏠 Старт"
BTN_MENU = "📌 Меню"
BTN_VERIFY = "🔐 Авторизация"
BTN_CHANGE_DORM = "🏢 Сменить общежитие"
BTN_MARKETPLACE = "🛍 Внутренний маркетплейс"
BTN_SPACE = "🏢 Управление пространством"
BTN_COMMS = "💬 Коммуникация и сервис"
BTN_ADD = "➕ Добавить объявление"
BTN_LIST = "📋 Все объявления"
BTN_MY = "👤 Мои объявления"
BTN_LOSTFOUND_ADD = "🧷 Добавить потеряшку"
BTN_LOSTFOUND_LIST = "🧷 Список потеряшек"
BTN_BOOK_ZONE = "🗓 Забронировать зону"
BTN_MY_BOOKINGS = "📅 Мои бронирования"
BTN_LAUNDRY = "🧺 Статус стиралок"
BTN_ANNOUNCEMENTS = "📢 Официальные объявления"
BTN_TICKET_NEW = "📝 Обращение в администрацию"
BTN_TICKET_MY = "🔎 Мои обращения"
BTN_INFO = "ℹ️ Помощь"

ZONE_MAP = {
    "coworking": "Коворкинг",
    "kitchen": "Кухня",
    "tutor": "Репетиторская",
}

HSE_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@edu\.hse\.ru$", re.IGNORECASE)

DORMS = [
    "Общежитие №1",
    "Общежитие №2",
    "Общежитие №3",
    "Общежитие №4",
    "Общежитие №5",
    "Общежитие №6",
    "Общежитие №7",
    "Общежитие №8 «Трилистник»",
    "Общежитие №9",
    "Общежитие №10",
    "Дом аспиранта",
    "Студенческий городок Дубки",
]

ALLOWED_CATEGORIES = [
    "Книги",
    "Мебель",
    "Техника",
    "Одежда",
    "Аксессуары",
    "Спорт",
    "Еда",
    "Косметика",
    "Игры",
    "Другое",
]


def _menu_keyboard(is_verified: bool) -> ReplyKeyboardMarkup:
    if not is_verified:
        rows = [[BTN_VERIFY], [BTN_INFO]]
    else:
        rows = [
            [BTN_MARKETPLACE, BTN_SPACE],
            [BTN_COMMS, BTN_CHANGE_DORM],
            [BTN_INFO],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _dorm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(d, callback_data=f"dorm_{d}")] for d in DORMS])


def _marketplace_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [BTN_ADD, BTN_LIST],
        [BTN_MY, BTN_LOSTFOUND_ADD],
        [BTN_LOSTFOUND_LIST, BTN_MENU],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _space_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [BTN_BOOK_ZONE, BTN_MY_BOOKINGS],
        [BTN_LAUNDRY, BTN_MENU],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _comms_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [BTN_ANNOUNCEMENTS, BTN_TICKET_NEW],
        [BTN_TICKET_MY, BTN_MENU],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _clear_listing_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ["type", "category", "description", "contact"]:
        context.user_data.pop(key, None)


def _clear_lostfound_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ["lf_type", "lf_title", "lf_description", "lf_contact"]:
        context.user_data.pop(key, None)


def _profile_for_update(update: Update) -> UserProfile:
    user = update.effective_user
    full_name = " ".join([p for p in [user.first_name, user.last_name] if p]) or user.username or str(user.id)
    profile, _ = UserProfile.get_or_create(
        telegram_id=user.id,
        defaults={"full_name": full_name},
    )
    if profile.full_name != full_name:
        profile.full_name = full_name
        profile.save()
    return profile


async def _reply(update: Update, text: str, **kwargs) -> None:
    if update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)
    else:
        await update.message.reply_text(text, **kwargs)


def _is_verified(profile: UserProfile) -> bool:
    return bool(
        profile.is_verified
        and profile.email
        and profile.email.lower().endswith("@edu.hse.ru")
        and profile.verification_code == "CONFIRMED"
    )


async def _ensure_verified(update: Update) -> UserProfile | None:
    profile = _profile_for_update(update)
    if _is_verified(profile):
        return profile

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Начать верификацию", callback_data="verify_start")]]
    )
    await _reply(
        update,
        "Для доступа к DormLink нужна верификация через корпоративную почту ВШЭ.\n"
        "Нажмите кнопку ниже или используйте /verify.",
        reply_markup=keyboard,
    )
    return None


async def _ensure_dorm_selected(update: Update, profile: UserProfile) -> bool:
    if profile.selected_dorm:
        return True
    await _reply(
        update,
        "Сначала выберите номер общежития, чтобы продолжить работу.",
        reply_markup=_dorm_keyboard(),
    )
    return False


def _smtp_send_verification(email: str, code: str) -> tuple[bool, str]:
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    sendgrid_user = os.getenv("SENDGRID_SMTP_USER", "apikey")
    sendgrid_from = os.getenv("SENDGRID_FROM")
    sendgrid_host = os.getenv("SENDGRID_SMTP_HOST", "smtp.sendgrid.net")
    sendgrid_port = int(os.getenv("SENDGRID_SMTP_PORT", "587"))
    sendgrid_tls = os.getenv("SENDGRID_SMTP_USE_TLS", "true").lower() != "false"
    sendgrid_ssl = os.getenv("SENDGRID_SMTP_USE_SSL", "false").lower() == "true"

    if not all([sendgrid_key, sendgrid_from]):
        return (
            False,
            "SendGrid не настроен. Укажите SENDGRID_API_KEY и SENDGRID_FROM в .env.",
        )

    msg = EmailMessage()
    msg["Subject"] = "DormLink: код подтверждения"
    msg["From"] = sendgrid_from
    msg["To"] = email
    msg.set_content(
        "Ваш код подтверждения для DormLink:\n\n"
        f"{code}\n\n"
        "Код действует 10 минут."
    )

    try:
        ssl_context = ssl.create_default_context()
        if sendgrid_ssl or sendgrid_port == 465:
            with smtplib.SMTP_SSL(sendgrid_host, sendgrid_port, timeout=20, context=ssl_context) as server:
                server.login(sendgrid_user, sendgrid_key)
                server.send_message(msg)
        else:
            with smtplib.SMTP(sendgrid_host, sendgrid_port, timeout=20) as server:
                server.ehlo()
                if sendgrid_tls:
                    server.starttls(context=ssl_context)
                    server.ehlo()
                server.login(sendgrid_user, sendgrid_key)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        server_msg = ""
        try:
            if exc.smtp_error:
                server_msg = exc.smtp_error.decode("utf-8", errors="ignore")
        except Exception:
            server_msg = str(exc)
        return (False, f"SendGrid auth_failed {server_msg}".strip())
    except Exception as exc:
        return (False, f"SendGrid error: {exc}")

    return (True, "Код отправлен через SendGrid.")



def _listing_text(listing: Listing) -> str:
    created = listing.created_at.strftime("%d.%m %H:%M")
    return (
        f"#{listing.id}  {listing.type.upper()} | {listing.category}\n"
        f"{listing.description}\n"
        f"Контакт: {listing.contact}\n"
        f"Добавлено: {created}"
    )


def _reply_message(update: Update):
    return update.message if update.message else update.callback_query.message


async def _send_listing(update: Update, listing: Listing, with_actions: bool = False) -> None:
    message = _reply_message(update)
    text = _listing_text(listing)
    markup = None
    if with_actions:
        is_buy_request = listing.type.strip().lower() == "куплю"
        close_label = "✅ Куплено" if is_buy_request else "✅ Продано"
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(close_label, callback_data=f"mark_{listing.id}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"del_{listing.id}"),
                ]
            ]
        )

    if listing.photo_file_id:
        if listing.photo_type == "photo":
            await message.reply_photo(photo=listing.photo_file_id, caption=text, reply_markup=markup)
        else:
            await message.reply_document(document=listing.photo_file_id, caption=text, reply_markup=markup)
    else:
        await message.reply_text(text + "\n(без фото)", reply_markup=markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile):
        return await verify_start(update, context)

    if not profile.selected_dorm:
        await update.message.reply_text(
            "Добро пожаловать в DormLink! Сначала выберите общежитие:",
            reply_markup=_menu_keyboard(True),
        )
        await update.message.reply_text("Выберите общежитие:", reply_markup=_dorm_keyboard())
        return ConversationHandler.END

    await update.message.reply_text(
        f"Привет! Вы в DormLink.\nТекущее общежитие: {profile.selected_dorm}",
        reply_markup=_menu_keyboard(True),
    )
    return ConversationHandler.END


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile):
        await update.message.reply_text("Сначала пройдите авторизацию.", reply_markup=_menu_keyboard(False))
        return
    await update.message.reply_text(
        "Главное меню DormLink:\n"
        "1) Внутренний маркетплейс\n"
        "2) Управление пространством\n"
        "3) Коммуникация и сервис",
        reply_markup=_menu_keyboard(True),
    )


async def open_marketplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        "Раздел: Внутренний маркетплейс\n"
        "Здесь доступны купля/продажа и потеряшки.",
        reply_markup=_marketplace_keyboard(),
    )


async def open_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        "Раздел: Управление пространством\n"
        "Здесь можно бронировать общие зоны и смотреть статус стиралок.",
        reply_markup=_space_keyboard(),
    )


async def open_comms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        "Раздел: Коммуникация и сервис\n"
        "Официальные объявления и обращения в администрацию.",
        reply_markup=_comms_keyboard(),
    )


async def verify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if _is_verified(profile):
        await _reply(
            update,
            f"Вы уже авторизованы: {profile.email}",
            reply_markup=_menu_keyboard(True),
        )
        return ConversationHandler.END

    await _reply(
        update,
        "Введите вашу корпоративную почту ВШЭ (должна заканчиваться на @edu.hse.ru):",
        reply_markup=_menu_keyboard(False),
    )
    return AUTH_EMAIL


async def verify_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await verify_start(update, context)


async def verify_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()
    if not HSE_EMAIL_PATTERN.match(email):
        await update.message.reply_text("Неверный формат. Нужен адрес вида name@edu.hse.ru. Попробуйте еще раз:")
        return AUTH_EMAIL

    profile = _profile_for_update(update)
    profile.is_verified = False
    profile.verification_code = None
    profile.code_expires_at = None
    profile.save()

    code = f"{random.randint(100000, 999999)}"
    ok, message = _smtp_send_verification(email, code)
    if not ok:
        await update.message.reply_text(message)
        return AUTH_EMAIL

    profile.email = email
    profile.verification_code = code
    profile.code_expires_at = datetime.utcnow() + timedelta(minutes=10)
    profile.save()

    await update.message.reply_text(
        "Окей, отправили код. Введи его как придет. (Не забудьте заглянуть в спам.)"
    )
    return AUTH_CODE


async def verify_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    profile = _profile_for_update(update)
    if _is_verified(profile):
        await update.message.reply_text("Вы уже подтверждены через почту.", reply_markup=_menu_keyboard(True))
        return ConversationHandler.END

    if not profile.verification_code or not profile.code_expires_at:
        await update.message.reply_text("Сначала отправьте код через /verify.")
        return ConversationHandler.END

    if datetime.utcnow() > profile.code_expires_at:
        await update.message.reply_text("Срок действия кода истек. Запустите /verify заново.")
        profile.verification_code = None
        profile.code_expires_at = None
        profile.save()
        return ConversationHandler.END

    if code != profile.verification_code:
        await update.message.reply_text("Неверный код. Проверьте письмо и попробуйте еще раз:")
        return AUTH_CODE

    profile.is_verified = True
    profile.verification_code = "CONFIRMED"
    profile.code_expires_at = None
    profile.save()

    await update.message.reply_text(
        "Код верный, ура! Начинаем!",
        reply_markup=_menu_keyboard(True),
    )
    if profile.selected_dorm:
        await update.message.reply_text(f"Текущее общежитие: {profile.selected_dorm}")
    else:
        await update.message.reply_text("Теперь выберите общежитие:", reply_markup=_dorm_keyboard())
    return ConversationHandler.END


async def change_dorm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    await update.message.reply_text("Выберите новое общежитие:", reply_markup=_dorm_keyboard())


async def dorm_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return

    dorm = query.data.replace("dorm_", "")
    profile.selected_dorm = dorm
    profile.save()
    await query.edit_message_text(f"Вы выбрали: {dorm}")
    await query.message.reply_text("Можно начинать работу 👇", reply_markup=_menu_keyboard(True))


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие через кнопку «Сменить общежитие».")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("Продам", callback_data="type_Продам")],
        [InlineKeyboardButton("Куплю", callback_data="type_Куплю")],
    ]
    await update.message.reply_text("Тип объявления:", reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data.replace("type_", "")

    keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in ALLOWED_CATEGORIES]
    await query.edit_message_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data.replace("cat_", "")
    await query.edit_message_text(
        "Введите описание объявления:\n"
        "что продаете/ищете, состояние, цена, где передать."
    )
    return DESCRIPTION


async def add_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text("Описание не может быть пустым. Введите еще раз:")
        return DESCRIPTION

    context.user_data["description"] = desc
    await update.message.reply_text("Укажите контакт для связи (например, @username):")
    return CONTACT


async def add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text.strip()
    if not contact:
        await update.message.reply_text("Контакт не может быть пустым. Введите еще раз:")
        return CONTACT

    context.user_data["contact"] = contact
    keyboard = [[InlineKeyboardButton("Пропустить фото", callback_data="skip_photo")]]
    await update.message.reply_text(
        "Отправьте фото объявления или нажмите кнопку «Пропустить фото».",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PHOTO


def _create_listing_from_draft(profile: UserProfile, user_id: int, context, photo_file_id=None, photo_type=None) -> Listing:
    return Listing.create(
        author_id=user_id,
        dorm=profile.selected_dorm,
        type=context.user_data["type"],
        category=context.user_data["category"],
        description=context.user_data["description"],
        contact=context.user_data["contact"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile) or not profile.selected_dorm:
        await _reply(update, "Сессия устарела. Введите /start и начните заново.")
        _clear_listing_draft(context)
        return ConversationHandler.END

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "skip_photo":
            _create_listing_from_draft(profile, update.effective_user.id, context)
            await query.edit_message_text("Фото пропущено.")
            await query.message.reply_text("Объявление создано.", reply_markup=_marketplace_keyboard())
            _clear_listing_draft(context)
            return ConversationHandler.END

    if update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt in ["skip", "/skip", "пропустить", "без фото"]:
            _create_listing_from_draft(profile, update.effective_user.id, context)
            await update.message.reply_text("Фото пропущено.")
            await update.message.reply_text("Объявление создано.", reply_markup=_marketplace_keyboard())
            _clear_listing_draft(context)
            return ConversationHandler.END
        await update.message.reply_text("Нужно фото или команда «пропустить».")
        return PHOTO

    photo_file_id = None
    photo_type = None
    if update.message and update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        photo_type = "photo"
    elif update.message and update.message.document:
        doc = update.message.document
        if doc.mime_type in ["image/png", "image/jpeg", "image/webp"]:
            photo_file_id = doc.file_id
            photo_type = "document"
        else:
            await update.message.reply_text("Это не изображение. Нужен PNG/JPG/WEBP.")
            return PHOTO
    else:
        await update.message.reply_text("Нужна фотография или кнопка «Пропустить фото».")
        return PHOTO

    _create_listing_from_draft(profile, update.effective_user.id, context, photo_file_id, photo_type)
    await update.message.reply_text("Объявление создано.", reply_markup=_marketplace_keyboard())
    _clear_listing_draft(context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_listing_draft(context)
    _clear_lostfound_draft(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


async def lostfound_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Потеряно", callback_data="lf_type_Потеряно")],
            [InlineKeyboardButton("📦 Найдено", callback_data="lf_type_Найдено")],
        ]
    )
    await update.message.reply_text("Что вы хотите опубликовать?", reply_markup=keyboard)
    return LF_TYPE


async def lostfound_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["lf_type"] = query.data.replace("lf_type_", "")
    await query.edit_message_text("Коротко укажите, что за вещь (например: 'Черный кошелек').")
    return LF_TITLE


async def lostfound_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Название не может быть пустым. Введите еще раз:")
        return LF_TITLE
    context.user_data["lf_title"] = title
    await update.message.reply_text("Опишите подробнее: где/когда потеряли или нашли.")
    return LF_DESCRIPTION


async def lostfound_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не может быть пустым. Введите еще раз:")
        return LF_DESCRIPTION
    context.user_data["lf_description"] = description
    await update.message.reply_text("Укажите контакт для связи:")
    return LF_CONTACT


async def lostfound_contact_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text.strip()
    if not contact:
        await update.message.reply_text("Контакт не может быть пустым. Введите еще раз:")
        return LF_CONTACT
    context.user_data["lf_contact"] = contact
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить фото", callback_data="lf_skip_photo")]])
    await update.message.reply_text(
        "Отправьте фото вещи или нажмите «Пропустить фото».",
        reply_markup=keyboard,
    )
    return LF_PHOTO


async def lostfound_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile) or not profile.selected_dorm:
        await _reply(update, "Сессия устарела. Введите /start и начните заново.")
        _clear_lostfound_draft(context)
        return ConversationHandler.END

    photo_file_id = None
    photo_type = None

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "lf_skip_photo":
            await query.edit_message_text("Фото пропущено.")
            update_message = query.message
        else:
            update_message = query.message
    else:
        update_message = update.message

    if update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt not in ["skip", "/skip", "пропустить", "без фото"]:
            await update.message.reply_text("Нужно фото или команда «пропустить».")
            return LF_PHOTO
    elif update.message and update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        photo_type = "photo"
    elif update.message and update.message.document:
        doc = update.message.document
        if doc.mime_type in ["image/png", "image/jpeg", "image/webp"]:
            photo_file_id = doc.file_id
            photo_type = "document"
        else:
            await update.message.reply_text("Это не изображение. Нужен PNG/JPG/WEBP.")
            return LF_PHOTO

    LostFoundItem.create(
        author_id=update.effective_user.id,
        dorm=profile.selected_dorm,
        item_type=context.user_data["lf_type"],
        title=context.user_data["lf_title"],
        description=context.user_data["lf_description"],
        contact=context.user_data["lf_contact"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )

    await update_message.reply_text("Потеряшка опубликована ✅", reply_markup=_marketplace_keyboard())
    _clear_lostfound_draft(context)
    return ConversationHandler.END


async def lostfound_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return

    items = (
        LostFoundItem.select()
        .where(
            LostFoundItem.dorm == profile.selected_dorm,
            LostFoundItem.status == "активно",
        )
        .order_by(LostFoundItem.created_at.desc())
    )
    if not items.exists():
        await update.message.reply_text("Пока нет активных потеряшек.")
        return

    await update.message.reply_text(f"Потеряшки в {profile.selected_dorm}:")
    for item in items[:15]:
        await _send_lostfound_item(update, item, show_actions=(item.author_id == update.effective_user.id))


async def _send_lostfound_item(update: Update, item: LostFoundItem, show_actions: bool = False) -> None:
    message = _reply_message(update)
    created = item.created_at.strftime("%d.%m %H:%M")
    text = (
        f"#{item.id} {item.item_type} | {item.title}\n"
        f"{item.description}\n"
        f"Контакт: {item.contact}\n"
        f"Добавлено: {created}"
    )
    markup = None
    if show_actions:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Передано владельцу", callback_data=f"lf_done_{item.id}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"lf_del_{item.id}"),
                ]
            ]
        )
    if item.photo_file_id:
        if item.photo_type == "photo":
            await message.reply_photo(item.photo_file_id, caption=text, reply_markup=markup)
        else:
            await message.reply_document(item.photo_file_id, caption=text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


async def lostfound_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.replace("lf_done_", ""))
    try:
        item = LostFoundItem.get(
            LostFoundItem.id == item_id,
            LostFoundItem.author_id == update.effective_user.id,
            LostFoundItem.status == "активно",
        )
    except LostFoundItem.DoesNotExist:
        await query.message.reply_text("Потеряшка не найдена или не принадлежит вам.")
        return
    item.status = "передано"
    item.save()
    await query.message.reply_text(f"Потеряшка #{item_id} закрыта как переданная владельцу ✅")


async def lostfound_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.replace("lf_del_", ""))
    try:
        item = LostFoundItem.get(
            LostFoundItem.id == item_id,
            LostFoundItem.author_id == update.effective_user.id,
        )
    except LostFoundItem.DoesNotExist:
        await query.message.reply_text("Потеряшка не найдена или не принадлежит вам.")
        return
    item.delete_instance()
    await query.message.reply_text(f"Потеряшка #{item_id} удалена.")


async def my_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return

    listings = (
        Listing.select()
        .where(
            Listing.author_id == update.effective_user.id,
            Listing.dorm == profile.selected_dorm,
            Listing.status == "активно",
        )
        .order_by(Listing.created_at.desc())
    )
    if not listings.exists():
        await update.message.reply_text("У вас нет активных объявлений.")
        return

    await update.message.reply_text(f"Ваши активные объявления в {profile.selected_dorm}:")
    for listing in listings[:10]:
        await _send_listing(update, listing, with_actions=True)


async def list_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🛒 Покупка", callback_data="list_buy"),
                InlineKeyboardButton("💸 Продажа", callback_data="list_sell"),
            ]
        ]
    )
    await update.message.reply_text("Какие объявления показать?", reply_markup=keyboard)


async def list_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_type = "Куплю" if query.data == "list_buy" else "Продам"
    await _send_listings_by_type(update, listing_type)


async def _send_listings_by_type(update: Update, listing_type: str) -> None:
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await _reply(update, "Сначала выберите общежитие.")
        return

    listings = (
        Listing.select()
        .where(
            Listing.dorm == profile.selected_dorm,
            Listing.status == "активно",
            Listing.type == listing_type,
        )
        .order_by(Listing.created_at.desc())
    )
    if not listings.exists():
        await _reply(update, f"В разделе «{listing_type}» пока нет активных объявлений.")
        return

    await _reply(update, f"Раздел «{listing_type}» в {profile.selected_dorm}:")
    for listing in listings[:15]:
        await _send_listing(update, listing, with_actions=False)


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids = set()
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids


async def zone_booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return ConversationHandler.END
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Коворкинг", callback_data="zone_pick_coworking")],
            [InlineKeyboardButton("Кухня", callback_data="zone_pick_kitchen")],
            [InlineKeyboardButton("Репетиторская", callback_data="zone_pick_tutor")],
        ]
    )
    await update.message.reply_text("Выберите общую зону для брони:", reply_markup=keyboard)
    return BOOK_ZONE_NAME


def _zone_hours(zone_key: str) -> tuple[int, int]:
    if zone_key == "kitchen":
        return (0, 24)
    return (8, 23)


def _zone_slot_params(zone_key: str) -> tuple[int, int]:
    if zone_key == "kitchen":
        return (2, 2)  # duration_hours, step_hours
    return (1, 1)


def _booking_window_bounds() -> tuple[datetime, datetime]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    max_day = today + timedelta(days=7)
    return today, max_day


def _is_day_within_booking_window(day: datetime) -> bool:
    min_day, max_day = _booking_window_bounds()
    return min_day <= day <= max_day


def _slot_datetime(day: datetime, hour: int, duration_hours: int = 1) -> tuple[datetime, datetime]:
    start_at = day.replace(hour=hour, minute=0, second=0, microsecond=0)
    end_at = start_at + timedelta(hours=duration_hours)
    return start_at, end_at


def _is_slot_busy(dorm: str, zone_name: str, start_at: datetime, end_at: datetime) -> bool:
    query = (
        ZoneBooking.select()
        .where(
            ZoneBooking.dorm == dorm,
            ZoneBooking.zone_name == zone_name,
            ZoneBooking.status.in_(["ожидает подтверждения", "подтверждено"]),
            ZoneBooking.start_at.is_null(False),
            ZoneBooking.end_at.is_null(False),
            ZoneBooking.start_at < end_at,
            ZoneBooking.end_at > start_at,
        )
    )
    return query.exists()


def _slots_for_day(dorm: str, zone_key: str, day: datetime) -> list[tuple[str, datetime, datetime]]:
    zone_name = ZONE_MAP[zone_key]
    hour_from, hour_to = _zone_hours(zone_key)
    duration_hours, step_hours = _zone_slot_params(zone_key)
    now = datetime.now()
    slots = []
    last_start = hour_to - duration_hours
    for hour in range(hour_from, last_start + 1, step_hours):
        start_at, end_at = _slot_datetime(day, hour, duration_hours)
        if start_at <= now:
            continue
        if not _is_slot_busy(dorm, zone_name, start_at, end_at):
            label = f"{start_at.strftime('%d.%m')} {start_at.strftime('%H:%M')}-{end_at.strftime('%H:%M')}"
            slots.append((label, start_at, end_at))
    return slots


async def _show_zone_days(query_message, zone_key: str) -> None:
    zone_name = ZONE_MAP[zone_key]
    today, max_day = _booking_window_bounds()

    keyboard_rows = []
    day_buttons = []
    for i in range(0, (max_day - today).days + 1):
        current_day = today + timedelta(days=i)
        label = "Сегодня" if i == 0 else ("Завтра" if i == 1 else current_day.strftime("%d.%m"))
        day_buttons.append(
            InlineKeyboardButton(label, callback_data=f"zone_day_{zone_key}_{current_day.strftime('%Y%m%d')}")
        )
        if len(day_buttons) == 3:
            keyboard_rows.append(day_buttons)
            day_buttons = []
    if day_buttons:
        keyboard_rows.append(day_buttons)

    await query_message.reply_text(
        f"Зона: {zone_name}\nВыберите дату бронирования:",
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def _show_zone_slots(query_message, profile: UserProfile, zone_key: str, day: datetime) -> None:
    zone_name = ZONE_MAP[zone_key]
    date_key = day.strftime("%Y%m%d")
    if not _is_day_within_booking_window(day):
        await query_message.reply_text("Бронь доступна только максимум на неделю вперед.")
        return

    keyboard_rows = []
    free_slots = _slots_for_day(profile.selected_dorm, zone_key, day)
    for label, start_at, _ in free_slots[:20]:
        callback = f"zone_slot_{zone_key}_{date_key}_{start_at.hour:02d}"
        keyboard_rows.append([InlineKeyboardButton(label, callback_data=callback)])

    if not free_slots:
        keyboard_rows.append([InlineKeyboardButton("Нет свободных слотов на этот день", callback_data="zone_noslot")])
    keyboard_rows.append([InlineKeyboardButton("⬅️ Назад к выбору даты", callback_data=f"zone_back_{zone_key}")])

    text = (
        f"Зона: {zone_name}\n"
        f"Дата: {day.strftime('%d.%m.%Y')}\n"
        "Выберите свободный слот:"
    )
    await query_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))


async def zone_booking_zone_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await query.message.reply_text("Сначала выберите общежитие.")
        return ConversationHandler.END

    zone_key = query.data.replace("zone_pick_", "")
    if zone_key not in ZONE_MAP:
        await query.message.reply_text("Неизвестная зона.")
        return ConversationHandler.END

    await _show_zone_days(query.message, zone_key)
    return BOOK_ZONE_SLOT


async def zone_booking_slot_or_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await query.message.reply_text("Сначала выберите общежитие.")
        return ConversationHandler.END

    if query.data == "zone_noslot":
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_back_"):
        _, _, zone_key = query.data.split("_", 2)
        if zone_key not in ZONE_MAP:
            await query.message.reply_text("Неизвестная зона.")
            return BOOK_ZONE_SLOT
        await _show_zone_days(query.message, zone_key)
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_day_"):
        # zone_day_<zone_key>_<yyyymmdd>
        _, _, zone_key, day_key = query.data.split("_", 3)
        day = datetime.strptime(day_key, "%Y%m%d")
        if not _is_day_within_booking_window(day):
            await query.message.reply_text("Бронь может быть только максимум на неделю вперед.")
            return BOOK_ZONE_SLOT
        await _show_zone_slots(query.message, profile, zone_key, day)
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_slot_"):
        # zone_slot_<zone_key>_<yyyymmdd>_<hour>
        _, _, zone_key, day_key, hour_txt = query.data.split("_", 4)
        day = datetime.strptime(day_key, "%Y%m%d")
        if not _is_day_within_booking_window(day):
            await query.message.reply_text("Бронь может быть только максимум на неделю вперед.")
            return BOOK_ZONE_SLOT
        duration_hours, _ = _zone_slot_params(zone_key)
        start_at, end_at = _slot_datetime(day, int(hour_txt), duration_hours)
        zone_name = ZONE_MAP.get(zone_key)
        if not zone_name:
            await query.message.reply_text("Неизвестная зона.")
            return BOOK_ZONE_SLOT

        if _is_slot_busy(profile.selected_dorm, zone_name, start_at, end_at):
            await query.message.reply_text("Этот слот уже занят. Выберите другой слот.")
            await _show_zone_slots(query.message, profile, zone_key, day)
            return BOOK_ZONE_SLOT

        slot_text = f"{start_at.strftime('%d.%m %H:%M')}-{end_at.strftime('%H:%M')}"
        ZoneBooking.create(
            user_id=update.effective_user.id,
            dorm=profile.selected_dorm,
            zone_name=zone_name,
            slot_text=slot_text,
            start_at=start_at,
            end_at=end_at,
        )
        await query.message.reply_text(
            f"Заявка на бронирование создана ✅\n{zone_name}: {slot_text}",
            reply_markup=_space_keyboard(),
        )
        return ConversationHandler.END

    return BOOK_ZONE_SLOT


async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return
    bookings = (
        ZoneBooking.select()
        .where(
            ZoneBooking.user_id == update.effective_user.id,
            ZoneBooking.dorm == profile.selected_dorm,
        )
        .order_by(ZoneBooking.created_at.desc())
    )
    if not bookings.exists():
        await update.message.reply_text(f"У вас пока нет заявок на бронирование в {profile.selected_dorm}.")
        return
    await update.message.reply_text(f"Ваши бронирования в {profile.selected_dorm}:")
    for b in bookings[:10]:
        markup = None
        if b.status in {"ожидает подтверждения", "подтверждено"}:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Отменить бронь", callback_data=f"book_cancel_{b.id}")]]
            )
        await update.message.reply_text(
            f"#{b.id} {b.zone_name}\n"
            f"Время: {b.slot_text}\n"
            f"Статус: {b.status}",
            reply_markup=markup,
        )


async def booking_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    booking_id = int(query.data.replace("book_cancel_", ""))
    try:
        booking = ZoneBooking.get(
            ZoneBooking.id == booking_id,
            ZoneBooking.user_id == update.effective_user.id,
        )
    except ZoneBooking.DoesNotExist:
        await query.message.reply_text("Бронь не найдена или уже недоступна.")
        return

    if booking.status not in {"ожидает подтверждения", "подтверждено"}:
        await query.message.reply_text("Эту бронь уже нельзя отменить.")
        return

    booking.status = "отменено"
    booking.save()
    await query.message.reply_text(f"Бронь #{booking_id} отменена. Слот снова доступен для других ✅")


async def laundry_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text("Сначала выберите общежитие.")
        return

    rows = LaundryStatus.select().where(LaundryStatus.dorm == profile.selected_dorm)
    if not rows.exists():
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #1", status="свободна")
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #2", status="занята")
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #3", status="свободна")
        rows = LaundryStatus.select().where(LaundryStatus.dorm == profile.selected_dorm)

    await update.message.reply_text(f"Статус стиралок в {profile.selected_dorm}:")
    for row in rows:
        await update.message.reply_text(f"{row.machine_name}: {row.status}")


async def announcements_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    rows = (
        OfficialAnnouncement.select()
        .where((OfficialAnnouncement.dorm == "all") | (OfficialAnnouncement.dorm == (profile.selected_dorm or "")))
        .order_by(OfficialAnnouncement.created_at.desc())
    )
    if not rows.exists():
        await update.message.reply_text("Пока нет официальных объявлений.")
        return
    await update.message.reply_text("Официальные объявления:")
    for row in rows[:15]:
        created = row.created_at.strftime("%d.%m %H:%M")
        await update.message.reply_text(f"[{created}] {row.title}\n{row.text}")


async def announcement_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    if not context.args:
        await update.message.reply_text("Формат: /announce Заголовок | Текст объявления")
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text("Используйте разделитель '|': /announce Заголовок | Текст")
        return
    title, text = [part.strip() for part in raw.split("|", 1)]
    OfficialAnnouncement.create(
        dorm="all",
        title=title,
        text=text,
        created_by=update.effective_user.id,
    )
    await update.message.reply_text("Официальное объявление опубликовано ✅")


async def ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    await update.message.reply_text("Укажите тему обращения (например: Шум/Интернет/Сантехника):")
    return TICKET_THEME


async def ticket_theme_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    theme = update.message.text.strip()
    if not theme:
        await update.message.reply_text("Тема не может быть пустой. Введите еще раз:")
        return TICKET_THEME
    context.user_data["ticket_theme"] = theme
    await update.message.reply_text("Опишите обращение подробнее:")
    return TICKET_DESCRIPTION


async def ticket_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не может быть пустым. Введите еще раз:")
        return TICKET_DESCRIPTION
    context.user_data["ticket_description"] = description
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить фото", callback_data="ticket_skip_photo")]])
    await update.message.reply_text("Прикрепите фото (опционально):", reply_markup=keyboard)
    return TICKET_PHOTO


async def ticket_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    photo_file_id = None
    photo_type = None
    message = _reply_message(update)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "ticket_skip_photo":
            await query.edit_message_text("Фото пропущено.")

    elif update.message and update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        photo_type = "photo"
    elif update.message and update.message.document:
        doc = update.message.document
        if doc.mime_type in ["image/png", "image/jpeg", "image/webp"]:
            photo_file_id = doc.file_id
            photo_type = "document"
        else:
            await update.message.reply_text("Это не изображение. Нужен PNG/JPG/WEBP.")
            return TICKET_PHOTO
    elif update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt not in ["skip", "/skip", "пропустить", "без фото"]:
            await update.message.reply_text("Отправьте фото или пропустите.")
            return TICKET_PHOTO

    ticket = SupportTicket.create(
        user_id=update.effective_user.id,
        dorm=profile.selected_dorm or "не указано",
        theme=context.user_data["ticket_theme"],
        description=context.user_data["ticket_description"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )
    context.user_data.pop("ticket_theme", None)
    context.user_data.pop("ticket_description", None)
    await message.reply_text(f"Обращение #{ticket.id} зарегистрировано ✅", reply_markup=_comms_keyboard())
    return ConversationHandler.END


async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    rows = (
        SupportTicket.select()
        .where(SupportTicket.user_id == update.effective_user.id)
        .order_by(SupportTicket.created_at.desc())
    )
    if not rows.exists():
        await update.message.reply_text("У вас пока нет обращений.")
        return
    await update.message.reply_text("Ваши обращения:")
    for row in rows[:15]:
        await update.message.reply_text(
            f"#{row.id} | {row.theme}\n"
            f"{row.description}\n"
            f"Статус: {row.status}"
        )


async def ticket_status_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /ticket_status <id> <новое|в работе|закрыто>")
        return
    ticket_id = int(context.args[0])
    new_status = " ".join(context.args[1:]).strip()
    try:
        ticket = SupportTicket.get(SupportTicket.id == ticket_id)
    except SupportTicket.DoesNotExist:
        await update.message.reply_text("Обращение не найдено.")
        return
    ticket.status = new_status
    ticket.save()
    await update.message.reply_text(f"Статус обращения #{ticket_id} обновлен: {new_status}")

async def _delete_listing_by_id(update: Update, listing_id: int) -> None:
    profile = _profile_for_update(update)
    try:
        listing = Listing.get(
            Listing.id == listing_id,
            Listing.author_id == update.effective_user.id,
            Listing.dorm == profile.selected_dorm,
            Listing.status == "активно",
        )
    except Listing.DoesNotExist:
        await _reply(update, "Не найдено, уже удалено или не ваше объявление.")
        return

    listing.delete_instance()
    await _reply(update, f"Объявление #{listing_id} удалено.")


async def delete_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not context.args:
        await update.message.reply_text("Укажите ID: /delete 12")
        await my_ads(update, context)
        return
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /delete 12")
        return
    await _delete_listing_by_id(update, listing_id)


async def _mark_listing_sold_by_id(update: Update, listing_id: int) -> None:
    profile = _profile_for_update(update)
    try:
        listing = Listing.get(
            Listing.id == listing_id,
            Listing.author_id == update.effective_user.id,
            Listing.dorm == profile.selected_dorm,
            Listing.status == "активно",
        )
    except Listing.DoesNotExist:
        await _reply(update, "Не найдено, уже продано или не ваше объявление.")
        return

    is_buy_request = listing.type.strip().lower() == "куплю"
    listing.status = "куплено" if is_buy_request else "продано"
    listing.save()
    done_text = "купленным" if is_buy_request else "проданным"
    await _reply(update, f"Объявление #{listing_id} отмечено как {done_text}.")


async def buy_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not context.args:
        await update.message.reply_text("Укажите ID: /buy 12")
        return
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /buy 12")
        return
    await _mark_listing_sold_by_id(update, listing_id)


async def mark_listing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.replace("mark_", ""))
    await _mark_listing_sold_by_id(update, listing_id)


async def delete_listing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.replace("del_", ""))
    await _delete_listing_by_id(update, listing_id)


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    dorm_text = profile.selected_dorm if profile.selected_dorm else "не выбрано"
    text = (
        f"Текущее общежитие: {dorm_text}\n\n"
        "DormLink — сервис для жизни в общежитии.\n\n"
        "Разделы меню:\n"
        "1) 🛍 Внутренний маркетплейс: купля/продажа + потеряшки\n"
        "2) 🏢 Управление пространством: бронь зон + статус стиралок\n"
        "3) 💬 Коммуникация и сервис: объявления организации + обращения\n\n"
        "Ключевые команды:\n"
        "/start /verify /change /info\n"
        "/add /list /my /delete <id> /buy <id>\n"
        "/lostfound_add /lostfound_list\n"
        "/book_zone /my_bookings /laundry\n"
        "/announcements /ticket_new /my_tickets"
    )
    await update.message.reply_text(text)