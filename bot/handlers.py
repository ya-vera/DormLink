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
from translation import build_multilingual, detect_language, format_multilingual_for_user, translate_text

from telegram import InlineKeyboardMarkup, InlineKeyboardButton



TYPE, CATEGORY, DESCRIPTION, CONTACT, PHOTO = range(5)
AUTH_EMAIL, AUTH_CODE = range(10, 12)
LF_TYPE, LF_TITLE, LF_DESCRIPTION, LF_CONTACT, LF_PHOTO = range(20, 25)
BOOK_ZONE_NAME, BOOK_ZONE_SLOT = range(30, 32)
TICKET_THEME, TICKET_DESCRIPTION, TICKET_PHOTO = range(40, 43)

# Backward-compat constants (RU labels). Do not rely on these for UI.
BTN_START = "🏠 Старт"
BTN_MENU = "📌 Меню"
BTN_VERIFY = "🔐 Авторизация"
BTN_CHANGE_DORM = "🏢 Сменить общежитие"
BTN_LANG = "🌐 Язык / Language"
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

# Localized button labels (what user sees).
BUTTON_LABELS: dict[str, dict[str, str]] = {
    "START": {"ru": "🏠 Старт", "en": "🏠 Start", "zh": "🏠 开始"},
    "MENU": {"ru": "📌 Меню", "en": "📌 Menu", "zh": "📌 菜单"},
    "VERIFY": {"ru": "🔐 Авторизация", "en": "🔐 Verify", "zh": "🔐 验证"},
    "CHANGE_DORM": {"ru": "🏢 Сменить общежитие", "en": "🏢 Change dorm", "zh": "🏢 更换宿舍"},
    "LANG": {"ru": "🌐 Язык", "en": "🌐 Language", "zh": "🌐 语言"},
    "MARKETPLACE": {"ru": "🛍 Маркетплейс", "en": "🛍 Marketplace", "zh": "🛍 集市"},
    "SPACE": {"ru": "🏢 Пространства", "en": "🏢 Space", "zh": "🏢 空间"},
    "COMMS": {"ru": "💬 Сервис", "en": "💬 Service", "zh": "💬 服务"},
    "ADD": {"ru": "➕ Добавить объявление", "en": "➕ New listing", "zh": "➕ 发布信息"},
    "LIST": {"ru": "📋 Все объявления", "en": "📋 All listings", "zh": "📋 全部信息"},
    "MY": {"ru": "👤 Мои объявления", "en": "👤 My listings", "zh": "👤 我的信息"},
    "LOSTFOUND_ADD": {"ru": "🧷 Добавить потеряшку", "en": "🧷 New Lost&Found", "zh": "🧷 发布失物招领"},
    "LOSTFOUND_LIST": {"ru": "🧷 Список потеряшек", "en": "🧷 Lost&Found list", "zh": "🧷 失物招领列表"},
    "BOOK_ZONE": {"ru": "🗓 Забронировать зону", "en": "🗓 Book a zone", "zh": "🗓 预约区域"},
    "MY_BOOKINGS": {"ru": "📅 Мои бронирования", "en": "📅 My bookings", "zh": "📅 我的预约"},
    "LAUNDRY": {"ru": "🧺 Статус стиралок", "en": "🧺 Laundry status", "zh": "🧺 洗衣机状态"},
    "ANNOUNCEMENTS": {"ru": "📢 Официальные объявления", "en": "📢 Announcements", "zh": "📢 官方公告"},
    "TICKET_NEW": {"ru": "📝 Обращение в администрацию", "en": "📝 New ticket", "zh": "📝 提交请求"},
    "TICKET_MY": {"ru": "🔎 Мои обращения", "en": "🔎 My tickets", "zh": "🔎 我的请求"},
    "INFO": {"ru": "ℹ️ Помощь", "en": "ℹ️ Help", "zh": "ℹ️ 帮助"},
}


def _btn(key: str, lang: str) -> str:
    lang = (lang or "ru").lower()
    if lang not in {"ru", "en", "zh"}:
        lang = "ru"
    return BUTTON_LABELS.get(key, {}).get(lang) or BUTTON_LABELS.get(key, {}).get("ru") or key


def button_variants(key: str) -> list[str]:
    d = BUTTON_LABELS.get(key, {})
    return [v for v in d.values() if v]



BUTTON_REGEX: dict[str, str] = {
    key: "(?:" + "|".join(re.escape(v) for v in button_variants(key)) + ")"
    for key in BUTTON_LABELS
}

MESSAGES: dict[str, dict[str, str]] = {
    "SECTION_MARKETPLACE": {
        "ru": "Раздел: Внутренний маркетплейс\nЗдесь доступны купля/продажа и потеряшки.",
        "en": "Section: Marketplace\nBuy/Sell and Lost&Found.",
        "zh": "板块：集市\n买/卖 与 失物招领。",
    },
    # generic
    "NEED_VERIFY_FIRST": {
        "ru": "Сначала пройдите авторизацию.",
        "en": "Please verify with HSE email first.",
        "zh": "请先完成验证。",
    },
    "ACTION_CANCELLED": {"ru": "Действие отменено.", "en": "Cancelled.", "zh": "已取消。"},
    "SESSION_EXPIRED_RESTART": {
        "ru": "Сессия устарела. Введите /start и начните заново.",
        "en": "Session expired. Send /start and try again.",
        "zh": "会话已过期。请发送 /start 重新开始。",
    },
    "CHOOSE_DORM_FIRST": {
        "ru": "Сначала выберите общежитие.",
        "en": "Please choose a dorm first.",
        "zh": "请先选择宿舍。",
    },
    "CHOOSE_DORM_FIRST_CHANGE": {
        "ru": "Сначала выберите общежитие через кнопку «Сменить общежитие».",
        "en": "Choose a dorm first using “Change dorm”.",
        "zh": "请先通过“更换宿舍”选择宿舍。",
    },
    "DORM_CHOOSE_PROMPT": {
        "ru": "Выберите общежитие:",
        "en": "Choose a dorm:",
        "zh": "请选择宿舍：",
    },
    # start/menu
    "WELCOME_NEED_DORM": {
        "ru": "Добро пожаловать в DormLink! Сначала выберите общежитие:",
        "en": "Welcome to DormLink! First, choose your dorm:",
        "zh": "欢迎使用 DormLink！请先选择宿舍：",
    },
    "HELLO_WITH_DORM": {
        "ru": "Привет! Вы в DormLink.\nТекущее общежитие: {dorm}",
        "en": "Hi! You are in DormLink.\nCurrent dorm: {dorm}",
        "zh": "你好！欢迎使用 DormLink。\n当前宿舍：{dorm}",
    },
    "MAIN_MENU_TEXT": {
        "ru": "Главное меню DormLink:\n1) Внутренний маркетплейс\n2) Управление пространством\n3) Коммуникация и сервис",
        "en": "DormLink main menu:\n1) Marketplace\n2) Space\n3) Service",
        "zh": "DormLink 主菜单：\n1）集市\n2）空间\n3）服务",
    },
    # verification
    "VERIFY_REQUIRED": {
        "ru": "Для доступа к DormLink нужна верификация через корпоративную почту ВШЭ.\nНажмите кнопку ниже или используйте /verify.",
        "en": "DormLink requires verification via HSE email.\nTap the button below or use /verify.",
        "zh": "使用 DormLink 需要通过 HSE 企业邮箱验证。\n点击下方按钮或使用 /verify。",
    },
    "VERIFY_START_BTN": {
        "ru": "Начать верификацию",
        "en": "Start verification",
        "zh": "开始验证",
    },
    "ALREADY_VERIFIED": {
        "ru": "Вы уже авторизованы: {email}",
        "en": "You are already verified: {email}",
        "zh": "你已完成验证：{email}",
    },
    "ENTER_HSE_EMAIL": {
        "ru": "Введите вашу корпоративную почту ВШЭ (должна заканчиваться на @edu.hse.ru):",
        "en": "Enter your HSE email (must end with @edu.hse.ru):",
        "zh": "请输入你的 HSE 企业邮箱（必须以 @edu.hse.ru 结尾）：",
    },
    "EMAIL_INVALID": {
        "ru": "Неверный формат. Нужен адрес вида name@edu.hse.ru. Попробуйте еще раз:",
        "en": "Invalid email. Use name@edu.hse.ru and try again:",
        "zh": "邮箱格式不正确，需要 name@edu.hse.ru。请重试：",
    },
    "CODE_SENT": {
        "ru": "Окей, отправили код. Введи его как придет. (Не забудьте заглянуть в спам.)",
        "en": "Code sent. Enter it when it arrives (check spam).",
        "zh": "好的，验证码已发送。收到后请输入（也请检查垃圾邮件）。",
    },
    "ALREADY_CONFIRMED": {
        "ru": "Вы уже подтверждены через почту.",
        "en": "You are already confirmed.",
        "zh": "你已确认完成。",
    },
    "SEND_CODE_FIRST": {
        "ru": "Сначала отправьте код через /verify.",
        "en": "Send a code first using /verify.",
        "zh": "请先使用 /verify 获取验证码。",
    },
    "CODE_EXPIRED": {
        "ru": "Срок действия кода истек. Запустите /verify заново.",
        "en": "Code expired. Start /verify again.",
        "zh": "验证码已过期。请重新执行 /verify。",
    },
    "CODE_WRONG": {
        "ru": "Неверный код. Проверьте письмо и попробуйте еще раз:",
        "en": "Wrong code. Check the email and try again:",
        "zh": "验证码错误。请检查邮件并重试：",
    },
    "CODE_OK": {
        "ru": "Код верный, ура! Начинаем!",
        "en": "Code correct. Welcome!",
        "zh": "验证码正确！开始吧！",
    },
    "CURRENT_DORM": {"ru": "Текущее общежитие: {dorm}", "en": "Current dorm: {dorm}", "zh": "当前宿舍：{dorm}"},
    "NOW_CHOOSE_DORM": {
        "ru": "Теперь выберите общежитие:",
        "en": "Now choose a dorm:",
        "zh": "现在请选择宿舍：",
    },
    # dorm change
    "CHOOSE_NEW_DORM": {
        "ru": "Выберите новое общежитие:",
        "en": "Choose a new dorm:",
        "zh": "请选择新的宿舍：",
    },
    "DORM_CHOSEN": {"ru": "Вы выбрали: {dorm}", "en": "Selected: {dorm}", "zh": "已选择：{dorm}"},
    "READY_TO_START": {
        "ru": "Можно начинать работу 👇",
        "en": "Ready. You can use the bot 👇",
        "zh": "现在可以开始使用 👇",
    },
    # language flow
    "LANG_SAVED": {
        "ru": "Язык сохранён.",
        "en": "Language saved.",
        "zh": "语言已保存。",
    },
    "RESTART_DONE": {
        "ru": "Регистрация сброшена. Начнем заново 👇",
        "en": "Registration reset. Let's start again 👇",
        "zh": "注册已重置。我们重新开始吧 👇",
    },
    "NOW_VERIFY": {
        "ru": "Теперь пройдите верификацию через почту ВШЭ.",
        "en": "Now verify with your HSE email.",
        "zh": "现在请通过 HSE 邮箱完成验证。",
    },
    # listings
    "LISTING_TYPE_PROMPT": {"ru": "Тип объявления:", "en": "Listing type:", "zh": "信息类型："},
    "LISTING_CATEGORY_PROMPT": {"ru": "Выберите категорию:", "en": "Choose a category:", "zh": "选择类别："},
    "LISTING_ENTER_DESC": {
        "ru": "Введите описание объявления:\nчто продаете/ищете, состояние, цена, где передать.",
        "en": "Enter description:\nwhat you sell/need, condition, price, where to meet.",
        "zh": "请输入描述：\n卖/买什么、成色、价格、如何交接。",
    },
    "DESC_EMPTY": {
        "ru": "Описание не может быть пустым. Введите еще раз:",
        "en": "Description can't be empty. Try again:",
        "zh": "描述不能为空。请重试：",
    },
    "ENTER_CONTACT": {
        "ru": "Укажите контакт для связи (например, @username):",
        "en": "Enter contact (e.g., @username):",
        "zh": "请输入联系方式（例如 @username）：",
    },
    "CONTACT_EMPTY": {
        "ru": "Контакт не может быть пустым. Введите еще раз:",
        "en": "Contact can't be empty. Try again:",
        "zh": "联系方式不能为空。请重试：",
    },
    "SEND_PHOTO_OR_SKIP": {
        "ru": "Отправьте фото объявления или нажмите кнопку «Пропустить фото».",
        "en": "Send a photo or tap “Skip photo”.",
        "zh": "发送照片或点击“跳过照片”。",
    },
    "SKIP_PHOTO_BTN": {"ru": "Пропустить фото", "en": "Skip photo", "zh": "跳过照片"},
    "PHOTO_SKIPPED": {"ru": "Фото пропущено.", "en": "Photo skipped.", "zh": "已跳过照片。"},
    "LISTING_CREATED": {"ru": "Объявление создано.", "en": "Listing created.", "zh": "信息已发布。"},
    "NEED_PHOTO_OR_SKIP_CMD": {
        "ru": "Нужно фото или команда «пропустить».",
        "en": "Send a photo or type “skip”.",
        "zh": "请发送照片或输入“skip”。",
    },
    "NOT_IMAGE": {
        "ru": "Это не изображение. Нужен PNG/JPG/WEBP.",
        "en": "Not an image. Use PNG/JPG/WEBP.",
        "zh": "这不是图片。请发送 PNG/JPG/WEBP。",
    },
    "NEED_PHOTO_OR_BUTTON": {
        "ru": "Нужна фотография или кнопка «Пропустить фото».",
        "en": "Need a photo or “Skip photo” button.",
        "zh": "需要照片或点击“跳过照片”。",
    },
    # lost&found
    "LF_PUBLISH_PROMPT": {"ru": "Что вы хотите опубликовать?", "en": "What do you want to publish?", "zh": "你想发布什么？"},
    "LF_TITLE_PROMPT": {
        "ru": "Коротко укажите, что за вещь (например: 'Черный кошелек').",
        "en": "Short title (e.g., 'Black wallet').",
        "zh": "简短标题（例如“黑色钱包”）。",
    },
    "LF_TITLE_EMPTY": {"ru": "Название не может быть пустым. Введите еще раз:", "en": "Title can't be empty. Try again:", "zh": "标题不能为空。请重试："},
    "LF_DESC_PROMPT": {"ru": "Опишите подробнее: где/когда потеряли или нашли.", "en": "Describe details: where/when lost or found.", "zh": "请描述细节：在哪里/何时丢失或找到。"},
    "LF_DESC_EMPTY": {"ru": "Описание не может быть пустым. Введите еще раз:", "en": "Description can't be empty. Try again:", "zh": "描述不能为空。请重试："},
    "LF_CONTACT_PROMPT": {"ru": "Укажите контакт для связи:", "en": "Enter contact:", "zh": "请输入联系方式："},
    "LF_PUBLISHED": {"ru": "Потеряшка опубликована ✅", "en": "Posted ✅", "zh": "已发布 ✅"},
    "NO_ACTIVE_LF": {"ru": "Пока нет активных потеряшек.", "en": "No active items yet.", "zh": "暂无有效信息。"},
    "LF_NOT_FOUND_OR_NOT_YOURS": {"ru": "Потеряшка не найдена или не принадлежит вам.", "en": "Item not found or not yours.", "zh": "未找到该条目或不属于你。"},
    "LF_CLOSED": {
        "ru": "Потеряшка #{id} закрыта как переданная владельцу ✅",
        "en": "Item #{id} marked as returned ✅",
        "zh": "条目 #{id} 已标记为已归还 ✅",
    },
    "LF_DELETED": {
        "ru": "Потеряшка #{id} удалена.",
        "en": "Item #{id} deleted.",
        "zh": "条目 #{id} 已删除。",
    },
    # list/my
    "NO_MY_LISTINGS": {"ru": "У вас нет активных объявлений.", "en": "You have no active listings.", "zh": "你没有有效信息。"},
    "WHICH_LISTINGS_SHOW": {"ru": "Какие объявления показать?", "en": "Which listings to show?", "zh": "要显示哪些信息？"},
    "LISTINGS_BUY_BTN": {"ru": "🛒 Покупка", "en": "🛒 Buying", "zh": "🛒 求购"},
    "LISTINGS_SELL_BTN": {"ru": "💸 Продажа", "en": "💸 Selling", "zh": "💸 出售"},
    "SECTION_EMPTY": {
        "ru": "В разделе «{section}» пока нет активных объявлений.",
        "en": "No active listings in “{section}”.",
        "zh": "“{section}”暂无有效信息。",
    },
    "SECTION_HEADER": {
        "ru": "Раздел «{section}» в {dorm}:",
        "en": "“{section}” in {dorm}:",
        "zh": "{dorm} 的“{section}”：",
    },
    # booking
    "CHOOSE_ZONE": {"ru": "Выберите общую зону для брони:", "en": "Choose a zone to book:", "zh": "请选择要预约的区域："},
    "BOOKING_WINDOW_ONLY": {"ru": "Бронь доступна только максимум на неделю вперед.", "en": "Booking is available up to 7 days ahead.", "zh": "只能预约未来 7 天内。"},
    "UNKNOWN_ZONE": {"ru": "Неизвестная зона.", "en": "Unknown zone.", "zh": "未知区域。"},
    "BOOKING_WINDOW_ONLY_SHORT": {"ru": "Бронь может быть только максимум на неделю вперед.", "en": "Booking is only up to 7 days ahead.", "zh": "仅支持预约未来 7 天内。"},
    "SLOT_BUSY": {"ru": "Этот слот уже занят. Выберите другой слот.", "en": "This slot is busy. Choose another.", "zh": "该时段已被占用，请选择其他时段。"},
    "BOOKING_NOT_FOUND": {"ru": "Бронь не найдена или уже недоступна.", "en": "Booking not found or unavailable.", "zh": "未找到预约或不可用。"},
    "BOOKING_CANNOT_CANCEL": {"ru": "Эту бронь уже нельзя отменить.", "en": "This booking can't be cancelled.", "zh": "该预约无法取消。"},
    "ZONE_TODAY": {"ru": "Сегодня", "en": "Today", "zh": "今天"},
    "ZONE_TOMORROW": {"ru": "Завтра", "en": "Tomorrow", "zh": "明天"},
    "ZONE_PICK_DATE": {
        "ru": "Зона: {zone}\nВыберите дату бронирования:",
        "en": "Zone: {zone}\nChoose a booking date:",
        "zh": "区域：{zone}\n请选择预约日期：",
    },
    "ZONE_PICK_SLOT": {
        "ru": "Зона: {zone}\nДата: {date}\nВыберите свободный слот:",
        "en": "Zone: {zone}\nDate: {date}\nChoose an available slot:",
        "zh": "区域：{zone}\n日期：{date}\n请选择可用时段：",
    },
    "NO_FREE_SLOTS": {"ru": "Нет свободных слотов на этот день", "en": "No free slots for this day", "zh": "当天无空闲时段"},
    "BACK_TO_DAYS": {"ru": "⬅️ Назад к выбору даты", "en": "⬅️ Back to dates", "zh": "⬅️ 返回日期选择"},
    "BACK_TO_ZONES": {"ru": "⬅️ Назад к выбору зоны", "en": "⬅️ Back to zones", "zh": "⬅️ 返回区域选择"},
    "BOOKING_CREATED": {
        "ru": "Заявка на бронирование создана ✅\n{zone}: {slot}",
        "en": "Booking request created ✅\n{zone}: {slot}",
        "zh": "预约已创建 ✅\n{zone}：{slot}",
    },
    "MY_BOOKINGS_NONE": {
        "ru": "У вас пока нет заявок на бронирование в {dorm}.",
        "en": "You have no booking requests in {dorm}.",
        "zh": "你在 {dorm} 暂无预约。",
    },
    "MY_BOOKINGS_HEADER": {
        "ru": "Ваши бронирования в {dorm}:",
        "en": "Your bookings in {dorm}:",
        "zh": "你在 {dorm} 的预约：",
    },
    "CANCEL_BOOKING_BTN": {"ru": "❌ Отменить бронь", "en": "❌ Cancel booking", "zh": "❌ 取消预约"},
    "BOOKING_CANCELLED": {
        "ru": "Бронь #{id} отменена. Слот снова доступен для других ✅",
        "en": "Booking #{id} cancelled. Slot is available again ✅",
        "zh": "预约 #{id} 已取消，该时段已重新开放 ✅",
    },
    # laundry
    "LAUNDRY_HEADER": {
        "ru": "Статус стиралок в {dorm}:",
        "en": "Laundry status in {dorm}:",
        "zh": "{dorm} 洗衣机状态：",
    },
    # official announcements
    "OFFICIAL_ANN_HEADER": {
        "ru": "Официальные объявления ({dorm}):",
        "en": "Announcements ({dorm}):",
        "zh": "官方公告（{dorm}）：",
    },
    "OFFICIAL_ANN_PUBLISHED": {
        "ru": "Официальное объявление для {dorm} опубликовано ✅",
        "en": "Announcement for {dorm} published ✅",
        "zh": "{dorm} 的公告已发布 ✅",
    },
    # announcements/admin
    "ADMIN_ONLY": {"ru": "Команда доступна только администраторам.", "en": "Admins only.", "zh": "仅管理员可用。"},
    "NO_OFFICIAL_ANN": {"ru": "Пока нет официальных объявлений.", "en": "No announcements yet.", "zh": "暂无官方公告。"},
    "ANNOUNCE_FORMAT": {
        "ru": "Формат: /announce Заголовок | Текст объявления",
        "en": "Format: /announce Title | Text",
        "zh": "格式：/announce 标题 | 内容",
    },
    "ANNOUNCE_NEED_PIPE": {
        "ru": "Используйте разделитель '|': /announce Заголовок | Текст",
        "en": "Use '|' separator: /announce Title | Text",
        "zh": "请使用分隔符“|”：/announce 标题 | 内容",
    },
    # tickets
    "TICKET_THEME_PROMPT": {"ru": "Укажите тему обращения (например: Шум/Интернет/Сантехника):", "en": "Enter ticket theme (e.g., Noise/Internet/Plumbing):", "zh": "请输入主题（例如：噪音/网络/维修）："},
    "TICKET_THEME_EMPTY": {"ru": "Тема не может быть пустой. Введите еще раз:", "en": "Theme can't be empty. Try again:", "zh": "主题不能为空。请重试："},
    "TICKET_DESC_PROMPT": {"ru": "Опишите обращение подробнее:", "en": "Describe your issue:", "zh": "请详细描述问题："},
    "TICKET_DESC_EMPTY": {"ru": "Описание не может быть пустым. Введите еще раз:", "en": "Description can't be empty. Try again:", "zh": "描述不能为空。请重试："},
    "TICKET_PHOTO_OPTIONAL": {"ru": "Прикрепите фото (опционально):", "en": "Attach a photo (optional):", "zh": "上传照片（可选）："},
    "SEND_PHOTO_OR_SKIP_SIMPLE": {"ru": "Отправьте фото или пропустите.", "en": "Send a photo or skip.", "zh": "发送照片或跳过。"},
    "NO_TICKETS_FOR_DORM": {
        "ru": "У вас пока нет обращений для {dorm}.",
        "en": "You have no tickets for {dorm}.",
        "zh": "你在 {dorm} 暂无请求。",
    },
    "MY_TICKETS_HEADER": {
        "ru": "Ваши обращения ({dorm}):",
        "en": "Your tickets ({dorm}):",
        "zh": "你的请求（{dorm}）：",
    },
    "TICKET_STATUS_FORMAT": {
        "ru": "Формат: /ticket_status <id> <новое|в работе|закрыто>",
        "en": "Format: /ticket_status <id> <new|in progress|closed>",
        "zh": "格式：/ticket_status <id> <新建|处理中|已关闭>",
    },
    "TICKET_NOT_FOUND": {"ru": "Обращение не найдено.", "en": "Ticket not found.", "zh": "未找到该请求。"},
    "TICKET_STATUS_UPDATED": {
        "ru": "Статус обращения #{id} обновлен: {status}",
        "en": "Ticket #{id} status updated: {status}",
        "zh": "请求 #{id} 状态已更新：{status}",
    },
    # delete/buy
    "NOT_FOUND_DELETE": {"ru": "Не найдено, уже удалено или не ваше объявление.", "en": "Not found, deleted, or not yours.", "zh": "未找到/已删除/不属于你。"},
    "DELETE_NEED_ID": {"ru": "Укажите ID: /delete 12", "en": "Provide ID: /delete 12", "zh": "请输入 ID：/delete 12"},
    "ID_MUST_BE_NUMBER_DELETE": {"ru": "ID должен быть числом. Пример: /delete 12", "en": "ID must be a number. Example: /delete 12", "zh": "ID 必须是数字。例如：/delete 12"},
    "NOT_FOUND_BUY": {"ru": "Не найдено, уже продано или не ваше объявление.", "en": "Not found, sold, or not yours.", "zh": "未找到/已售出/不属于你。"},
    "BUY_NEED_ID": {"ru": "Укажите ID: /buy 12", "en": "Provide ID: /buy 12", "zh": "请输入 ID：/buy 12"},
    "ID_MUST_BE_NUMBER_BUY": {"ru": "ID должен быть числом. Пример: /buy 12", "en": "ID must be a number. Example: /buy 12", "zh": "ID 必须是数字。例如：/buy 12"},
    "LISTING_DELETED": {
        "ru": "Объявление #{id} удалено.",
        "en": "Listing #{id} deleted.",
        "zh": "信息 #{id} 已删除。",
    },
    "LISTING_MARKED": {
        "ru": "Объявление #{id} отмечено как {status}.",
        "en": "Listing #{id} marked as {status}.",
        "zh": "信息 #{id} 已标记为 {status}。",
    },
    # language
    "LANG_CHOOSE": {
        "ru": "Выберите язык / Choose language / 选择语言:",
        "en": "Choose language / Выберите язык / 选择语言:",
        "zh": "选择语言 / Choose language / Выберите язык:",
    },
    "LANG_UNKNOWN": {
        "ru": "Неизвестный язык.",
        "en": "Unknown language.",
        "zh": "未知语言。",
    },
    "LANG_UPDATED": {
        "ru": "Готово ✅ Язык обновлён.",
        "en": "Language updated.",
        "zh": "完成 ✅ 语言已更新。",
    },
    "MY_ACTIVE_LISTINGS": {
        "ru": "Ваши активные объявления в {dorm}:",
        "en": "Your active listings in {dorm}:",
        "zh": "您在 {dorm} 的有效信息：",
    },
    "SECTION_MARKETPLACE": {
        "ru": "Раздел: Внутренний маркетплейс\nЗдесь доступны купля/продажа и потеряшки.",
        "en": "Section: Marketplace\nBuy/Sell and Lost&Found.",
        "zh": "板块：集市\n买/卖 与 失物招领。",
    },
    "SECTION_SPACE": {
        "ru": "Раздел: Управление пространством\nЗдесь можно бронировать зоны и смотреть стиралки.",
        "en": "Section: Space\nBook zones and check laundry.",
        "zh": "板块：空间管理\n预约区域并查看洗衣机。",
    },
    "SECTION_COMMS": {
        "ru": "Раздел: Коммуникация и сервис\nОфициальные объявления и обращения.",
        "en": "Section: Communication & Service\nAnnouncements and tickets.",
        "zh": "板块：沟通与服务\n公告和请求。",
    },
    "ZONE_COWORKING": {"ru":"Коворкинг","en":"Coworking","zh":"自习室"},
    "ZONE_KITCHEN": {"ru":"Кухня","en":"Kitchen","zh":"厨房"},
    "ZONE_TUTOR": {"ru":"Репетиторская","en":"Study room","zh":"学习室"},
    "NO_PHOTO": {
        "ru": "(без фото)",
        "en": "(no photo)",
        "zh": "(无图片)",
    },
    "LAUNDRY_ROW": {
        "ru": "{name}: {status}",
        "en": "{name}: {status}",
        "zh": "{name}: {status}",
    },
    "ANN_ROW":{
    "ru":"[{date}] {title}\n{text}",
    "en":"[{date}] {title}\n{text}",
    "zh":"[{date}] {title}\n{text}",
    },
    "LF_LOST":{"ru":"🔍 Потеряно","en":"Lost","zh":"丢失"},
    "LF_FOUND":{"ru":"📦 Найдено","en":"Found","zh":"找到"},
    "BOUGHT": {
        "ru": "✅ Куплено",
        "en": "✅ Bought",
        "zh": "已购买",
    },

    "SOLD": {
        "ru": "✅ Продано",
        "en": "✅ Sold",
        "zh": "已售",
    },

    "DELETE": {
        "ru": "🗑 Удалить",
        "en": "🗑 Delete",
        "zh": "删除",
    },

    "NO_PHOTO": {
        "ru": "(без фото)",
        "en": "(no photo)",
        "zh": "(无图片)",
    },
    "LF_HEADER": {
        "ru": "Потеряшки в {dorm}:",
        "en": "Lost & Found in {dorm}:",
        "zh": "{dorm} 的失物招领：",
    },
    "LF_ITEM_TEXT": {
        "ru": "#{id} {type} | {title}\n{desc}\nКонтакт: {contact}\nДобавлено: {created}",
        "en": "#{id} {type} | {title}\n{desc}\nContact: {contact}\nAdded: {created}",
        "zh": "#{id} {type} | {title}\n{desc}\n联系方式: {contact}\n发布: {created}",
    },

    "LF_DONE": {
        "ru": "✅ Передано владельцу",
        "en": "✅ Returned",
        "zh": "已归还",
    },
    "BUY": {
        "ru": "Куплю",
        "en": "Buy",
        "zh": "求购",
    },

    "SELL": {
        "ru": "Продам",
        "en": "Sell",
        "zh": "出售",
    },
    "TODAY": {
        "ru": "Сегодня",
        "en": "Today",
        "zh": "今天",
    },

    "TOMORROW": {
        "ru": "Завтра",
        "en": "Tomorrow",
        "zh": "明天",
    },
    "BOOKING_ROW": {
        "ru": "#{id} {zone}\nВремя: {time}\nСтатус: {status}",
        "en": "#{id} {zone}\nTime: {time}\nStatus: {status}",
        "zh": "#{id} {zone}\n时间: {time}\n状态: {status}",
    },
    "TICKET_CREATED": {
        "ru": "Обращение #{id} зарегистрировано ✅",
        "en": "Ticket #{id} created ✅",
        "zh": "请求 #{id} 已创建 ✅",
    },
    "TICKET_ROW": {
        "ru": "#{id} | {theme}\n{desc}\nСтатус: {status}",
        "en": "#{id} | {theme}\n{desc}\nStatus: {status}",
        "zh": "#{id} | {theme}\n{desc}\n状态: {status}",
    },
    "BOUGHT_DONE": {
        "ru": "купленным",
        "en": "bought",
        "zh": "已购买",
    },

    "SOLD_DONE": {
        "ru": "проданным",
        "en": "sold",
        "zh": "已售",
    },
    "NOT_SELECTED": {
        "ru": "не выбрано",
        "en": "not selected",
        "zh": "未选择",
    },

    "NEED_CHOOSE_DORM": {
        "ru": "Сначала выберите номер общежития, чтобы продолжить работу.",
        "en": "Please choose your dorm first before proceeding.",
        "zh": "请先选择您的宿舍，然后继续操作。",
    },

    "EMAIL_SUBJECT": {
        "ru": "DormLink: код подтверждения",
        "en": "DormLink: verification code",
        "zh": "DormLink：验证码",
    },
    "EMAIL_BODY": {
        "ru": "Ваш код подтверждения для DormLink: {code}\nКод действует 10 минут.",
        "en": "Your verification code for DormLink is: {code}\nThe code is valid for 10 minutes.",
        "zh": "您的 DormLink 验证码是: {code}\n该验证码有效期为10分钟。",
    },
    "SENDGRID_NOT_CONFIGURED": {
        "ru": "SendGrid не настроен. Укажите SENDGRID_API_KEY и SENDGRID_FROM в .env.",
        "en": "SendGrid is not configured. Set SENDGRID_API_KEY and SENDGRID_FROM in .env.",
        "zh": "SendGrid 未配置，请在 .env 中设置 SENDGRID_API_KEY 和 SENDGRID_FROM。",
    },
    "EMAIL_SENT_SUCCESS": {
        "ru": "Код отправлен через SendGrid.",
        "en": "Code sent via SendGrid.",
        "zh": "验证码已通过 SendGrid 发送。",
    },

    "INFO_TEXT": {
        "ru":
            "Текущее общежитие: {dorm}\n\n"
            "DormLink — сервис для жизни в общежитии.\n\n"
            "Разделы меню:\n"
            "1) 🛍 Внутренний маркетплейс: купля/продажа + потеряшки\n"
            "2) 🏢 Управление пространством: бронь зон + статус стиралок\n"
            "3) 💬 Коммуникация и сервис: объявления организации + обращения\n\n"
            "Ключевые команды:\n"
            "/start /restart /verify /change /info\n"
            "/add /list /my /delete <id> /buy <id>\n"
            "/lostfound_add /lostfound_list\n"
            "/book_zone /my_bookings /laundry\n"
            "/announcements /ticket_new /my_tickets",

        "en":
            "Current dorm: {dorm}\n\n"
            "DormLink — dorm life service.\n\n"
            "Menu:\n"
            "1) Marketplace: buy/sell + lost&found\n"
            "2) Space: zones booking + laundry\n"
            "3) Communication: announcements + tickets\n\n"
            "Commands:\n"
            "/start /restart /verify /change /info\n"
            "/add /list /my /delete <id> /buy <id>\n"
            "/lostfound_add /lostfound_list\n"
            "/book_zone /my_bookings /laundry\n"
            "/announcements /ticket_new /my_tickets",

        "zh":
            "当前宿舍: {dorm}\n\n"
            "DormLink — 宿舍服务系统\n\n"
            "菜单:\n"
            "1) 集市\n"
            "2) 空间管理\n"
            "3) 通信\n\n"
            "命令:\n"
            "/start /restart /verify /change /info",
    }
}


def _ensure_listing_translation_fields(listing: Listing) -> None:
    """
    If translations are missing (or equal to original), backfill them on-the-fly.
    This makes UX robust when external translators fail during creation.
    """
    changed = False
    base = (listing.description or "").strip()
    if not base:
        return

    desc_ru = (listing.description_ru or "").strip()
    desc_en = (listing.description_en or "").strip()
    desc_zh = (getattr(listing, "description_zh", None) or "").strip()

    detected = (listing.description_lang or "").strip().lower() or None
    if not detected:
        detected = detect_language(base)
        listing.description_lang = detected
        changed = True

    # Backfill per language. If translation equals input, treat as missing.
    if detected == "en":
        if not desc_en:
            listing.description_en = base
            desc_en = base
            changed = True
        if not desc_ru or desc_ru == desc_en:
            listing.description_ru = translate_text(desc_en, "ru")
            changed = True
        if not desc_zh or desc_zh == desc_en:
            listing.description_zh = translate_text(desc_en, "zh")
            changed = True
    elif detected == "ru":
        if not desc_ru:
            listing.description_ru = base
            desc_ru = base
            changed = True
        if not desc_en or desc_en == desc_ru:
            listing.description_en = translate_text(desc_ru, "en")
            changed = True
        if not desc_zh or desc_zh == desc_ru:
            listing.description_zh = translate_text(desc_ru, "zh")
            changed = True
    elif detected == "zh":
        if not desc_zh:
            listing.description_zh = base
            desc_zh = base
            changed = True
        if not desc_en or desc_en == desc_zh:
            listing.description_en = translate_text(desc_zh, "en")
            changed = True
        if not desc_ru or desc_ru == desc_zh:
            listing.description_ru = translate_text(desc_zh, "ru")
            changed = True
    else:
        # Unknown: attempt to fill all
        if not desc_en:
            listing.description_en = translate_text(base, "en")
            changed = True
        if not desc_ru:
            listing.description_ru = translate_text(base, "ru")
            changed = True
        if not desc_zh:
            listing.description_zh = translate_text(base, "zh")
            changed = True

    if changed:
        try:
            listing.save()
        except Exception:
            pass


def t(profile: UserProfile | None, key: str, **fmt) -> str:
    lang = _user_lang(profile)
    text = (MESSAGES.get(key, {}).get(lang) or MESSAGES.get(key, {}).get("ru") or key)
    try:
        return text.format(**fmt)
    except Exception:
        return text

ZONE_MAP = {
    "coworking": "Коворкинг",
    "kitchen": "Кухня",
    "tutor": "Репетиторская",
}

HSE_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@edu\.hse\.ru$", re.IGNORECASE)

DORMS = {
    "ru": [
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
    ],
    "en": [
        "Dorm №1",
        "Dorm №2",
        "Dorm №3",
        "Dorm №4",
        "Dorm №5",
        "Dorm №6",
        "Dorm №7",
        "Dorm №8 “Trilistnik”",
        "Dorm №9",
        "Dorm №10",
        "Graduate House",
        "Dubki Student Campus",
    ],
    "zh": [
        "宿舍1号",
        "宿舍2号",
        "宿舍3号",
        "宿舍4号",
        "宿舍5号",
        "宿舍6号",
        "宿舍7号",
        "宿舍8号 “三叶草”",
        "宿舍9号",
        "宿舍10号",
        "研究生宿舍",
        "杜布基学生园区",
    ],
}

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

TYPE_EN = {
    "Продам": "Sell",
    "Куплю": "Buy",
}

TYPE_ZH = {
    "Продам": "出售",
    "Куплю": "求购",
}

LF_TYPE_EN = {
    "Потеряно": "Lost",
    "Найдено": "Found",
}

LF_TYPE_ZH = {
    "Потеряно": "丢失",
    "Найдено": "找到",
}

CATEGORY_EN = {
    "Книги": "Books",
    "Мебель": "Furniture",
    "Техника": "Electronics",
    "Одежда": "Clothes",
    "Аксессуары": "Accessories",
    "Спорт": "Sport",
    "Еда": "Food",
    "Косметика": "Cosmetics",
    "Игры": "Games",
    "Другое": "Other",
}

CATEGORY_ZH = {
    "Книги": "书籍",
    "Мебель": "家具",
    "Техника": "电子产品",
    "Одежда": "衣服",
    "Аксессуары": "配件",
    "Спорт": "运动",
    "Еда": "食物",
    "Косметика": "化妆品",
    "Игры": "游戏",
    "Другое": "其他",
}


def _pair_label(primary: str, secondary: str) -> str:
    primary = (primary or "").strip()
    secondary = (secondary or "").strip()
    if not secondary or secondary == primary:
        return primary
    return f"{primary} ({secondary})"


def _listing_type_label(ru_type: str, lang: str) -> str:
    if lang == "en":
        return _pair_label(TYPE_EN.get(ru_type, ru_type), ru_type)
    if lang == "zh":
        return _pair_label(TYPE_ZH.get(ru_type, ru_type), ru_type)
    return _pair_label(ru_type, TYPE_EN.get(ru_type, ru_type))


def _category_label(ru_cat: str, lang: str) -> str:
    if lang == "en":
        return _pair_label(CATEGORY_EN.get(ru_cat, ru_cat), ru_cat)
    if lang == "zh":
        return _pair_label(CATEGORY_ZH.get(ru_cat, ru_cat), ru_cat)
    return _pair_label(ru_cat, CATEGORY_EN.get(ru_cat, ru_cat))

def _menu_keyboard(is_verified: bool, lang: str) -> ReplyKeyboardMarkup:
    if not is_verified:
        rows = [[_btn("VERIFY", lang)], [_btn("INFO", lang)]]
    else:
        rows = [
            [_btn("MARKETPLACE", lang), _btn("SPACE", lang)],
            [_btn("COMMS", lang), _btn("CHANGE_DORM", lang)],
            [_btn("LANG", lang), _btn("INFO", lang)],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _dorm_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    dorm_list = DORMS.get(lang, DORMS["ru"])
    buttons = [[InlineKeyboardButton(d, callback_data=f"dorm_{i}")] for i, d in enumerate(dorm_list, start=1)]
    return InlineKeyboardMarkup(buttons)

def _marketplace_keyboard(lang: str) -> ReplyKeyboardMarkup:
    rows = [
        [_btn("ADD", lang), _btn("LIST", lang)],
        [_btn("MY", lang), _btn("LOSTFOUND_ADD", lang)],
        [_btn("LOSTFOUND_LIST", lang), _btn("MENU", lang)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _space_keyboard(lang: str) -> ReplyKeyboardMarkup:
    rows = [
        [_btn("BOOK_ZONE", lang), _btn("MY_BOOKINGS", lang)],
        [_btn("LAUNDRY", lang), _btn("MENU", lang)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _comms_keyboard(lang: str) -> ReplyKeyboardMarkup:
    rows = [
        [_btn("ANNOUNCEMENTS", lang), _btn("TICKET_NEW", lang)],
        [_btn("TICKET_MY", lang), _btn("MENU", lang)],
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
    if os.getenv("DISABLE_VERIFICATION", "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return True
    return bool(
        profile.is_verified
        and profile.email
        and profile.email.lower().endswith("@edu.hse.ru")
        and profile.verification_code == "CONFIRMED"
    )


async def _ensure_verified(update: Update) -> UserProfile | None:
    profile = _profile_for_update(update)

    # язык берём либо из профиля, либо "ru"
    lang = getattr(profile, "preferred_language", None) or "ru"

    if _is_verified(profile):
        return profile

    # Если язык ещё не выбран, не показываем verify (пока)
    if not getattr(profile, "preferred_language", None):
        return None  # просто возвращаем None

    # иначе показываем кнопку и текст на нужном языке
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(profile, "VERIFY_START_BTN"), callback_data="verify_start")]]
    )
    await _reply(
        update,
        t(profile, "VERIFY_REQUIRED"),  # текст будет через t() с выбранным языком
        reply_markup=keyboard,
    )
    return None


def _user_lang(profile: UserProfile | None) -> str:
    lang = (getattr(profile, "preferred_language", None) or "ru").strip().lower()
    return lang if lang in {"ru", "en", "zh"} else "ru"


async def _ensure_dorm_selected(update: Update, profile: UserProfile) -> bool:
    if profile.selected_dorm:
        return True

    # Определяем язык пользователя
    lang = profile.preferred_language or "ru"

    # Отправляем сообщение с текстом через t() и клавиатуру на нужном языке
    await _reply(
        update,
        t(profile, "NEED_CHOOSE_DORM"),  # добавь в MESSAGES ключ "NEED_CHOOSE_DORM"
        reply_markup=_dorm_keyboard(lang),
    )
    return False


def _smtp_send_verification(email: str, code: str, profile: UserProfile) -> tuple[bool, str]:
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    sendgrid_user = os.getenv("SENDGRID_SMTP_USER", "apikey")
    sendgrid_from = os.getenv("SENDGRID_FROM")
    sendgrid_host = os.getenv("SENDGRID_SMTP_HOST", "smtp.sendgrid.net")
    sendgrid_port = int(os.getenv("SENDGRID_SMTP_PORT", "587"))
    sendgrid_tls = os.getenv("SENDGRID_SMTP_USE_TLS", "true").lower() != "false"
    sendgrid_ssl = os.getenv("SENDGRID_SMTP_USE_SSL", "false").lower() == "true"

    lang = _user_lang(profile)

    if not all([sendgrid_key, sendgrid_from]):
        return (
            False,
            t(profile, "SENDGRID_NOT_CONFIGURED")  # новый ключ в MESSAGES
        )

    # Тема письма
    subject = t(profile, "EMAIL_SUBJECT")  # новый ключ, например "DormLink: код подтверждения"

    # Текст письма с кодом
    body = t(profile, "EMAIL_BODY", code=code)  # пример: "Ваш код подтверждения для DormLink: {code}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sendgrid_from
    msg["To"] = email
    msg.set_content(body)

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

    return (True, t(profile, "EMAIL_SENT_SUCCESS"))  # "Код отправлен через SendGrid."



def _listing_text_for_lang(listing: Listing, viewer_lang: str) -> str:
    _ensure_listing_translation_fields(listing)
    created = listing.created_at.strftime("%d.%m %H:%M")
    type_primary = listing.type
    type_secondary = TYPE_EN.get(listing.type, listing.type)
    cat_primary = listing.category
    cat_secondary = CATEGORY_EN.get(listing.category, listing.category)

    if viewer_lang == "en":
        type_primary, type_secondary = type_secondary, listing.type
        cat_primary, cat_secondary = cat_secondary, listing.category

    type_txt = type_primary if type_secondary == type_primary else f"{type_primary} ({type_secondary})"
    cat_txt = cat_primary if cat_secondary == cat_primary else f"{cat_primary} ({cat_secondary})"
    desc_txt = format_multilingual_for_user(
        listing.description_ru,
        listing.description_en,
        getattr(listing, "description_zh", None),
        viewer_lang,
    )

    if viewer_lang == "en":
        contact_label, created_label = "Contact", "Added"
    elif viewer_lang == "zh":
        contact_label, created_label = "联系方式", "发布"
    else:
        contact_label, created_label = "Контакт", "Добавлено"
    return (
        f"#{listing.id}  {type_txt.upper()} | {cat_txt}\n"
        f"{desc_txt}\n"
        f"{contact_label}: {listing.contact}\n"
        f"{created_label}: {created}"
    )


def _reply_message(update: Update):
    return update.message if update.message else update.callback_query.message


async def _send_listing(update: Update, listing: Listing, with_actions: bool = False) -> None:
    message = _reply_message(update)
    viewer_profile = _profile_for_update(update)
    viewer_lang = _user_lang(viewer_profile)

    text = _listing_text_for_lang(listing, viewer_lang)

    markup = None

    if with_actions:
        is_buy_request = listing.type.strip().lower() == "куплю"

        close_label = (
            t(viewer_profile, "BOUGHT")
            if is_buy_request
            else t(viewer_profile, "SOLD")
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        close_label,
                        callback_data=f"mark_{listing.id}",
                    ),
                    InlineKeyboardButton(
                        t(viewer_profile, "DELETE"),
                        callback_data=f"del_{listing.id}",
                    ),
                ]
            ]
        )

    if listing.photo_file_id:

        if listing.photo_type == "photo":

            await message.reply_photo(
                photo=listing.photo_file_id,
                caption=text,
                reply_markup=markup,
            )

        else:

            await message.reply_document(
                document=listing.photo_file_id,
                caption=text,
                reply_markup=markup,
            )

    else:

        await message.reply_text(
            text + "\n" + t(viewer_profile, "NO_PHOTO"),
            reply_markup=markup,
        )


# клавиатура выбора языка
def language_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇨🇳 中文", callback_data="lang_zh"),
            ]
        ]
    )

# /start — только выбор языка
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # показываем выбор языка сразу
    await update.message.reply_text(
        "Choose language / Выберите язык / 选择语言",
        reply_markup=language_keyboard(),
    )


async def restart_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    profile.email = None
    profile.is_verified = False
    profile.selected_dorm = None
    profile.verification_code = None
    profile.code_expires_at = None
    profile.save()
    context.user_data.clear()

    await update.message.reply_text(t(profile, "RESTART_DONE"))
    await update.message.reply_text(
        "Choose language / Выберите язык / 选择语言",
        reply_markup=language_keyboard(),
    )

# Callback после выбора языка
async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "").strip().lower()
    if lang not in {"ru", "en", "zh"}:
        await query.message.reply_text("Unknown language")
        return

    # сохраняем язык в профиле
    profile = _profile_for_update(update)
    profile.preferred_language = lang
    profile.save()
    context.user_data["lang"] = lang

    # показываем подтверждение выбора языка
    await query.message.reply_text(t(profile, "LANG_SAVED"))

    # если пользователь ещё не верифицирован — предлагаем пройти verify
    if not _is_verified(profile):
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(profile, "VERIFY_START_BTN"), callback_data="verify_start")]]
        )
        await query.message.reply_text(
            t(profile, "NOW_VERIFY"),
            reply_markup=keyboard,
        )
    else:
        # если уже верифицирован, просто показываем обновлённое меню на новом языке
        lang_now = _user_lang(profile)
        await query.message.reply_text(
            t(profile, "MAIN_MENU_TEXT"),
            reply_markup=_menu_keyboard(True, lang_now),
        )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    lang = _user_lang(profile)
    if not _is_verified(profile):
        await update.message.reply_text(t(profile, "NEED_VERIFY_FIRST"), reply_markup=_menu_keyboard(False, lang))
        return
    await update.message.reply_text(
        t(profile, "MAIN_MENU_TEXT"),
        reply_markup=_menu_keyboard(True, lang),
    )


async def open_marketplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        t(profile, "SECTION_MARKETPLACE"),
        reply_markup=_marketplace_keyboard(_user_lang(profile)),
    )


async def open_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        t(profile, "SECTION_SPACE"),
        reply_markup=_space_keyboard(_user_lang(profile)),
    )


async def open_comms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not await _ensure_dorm_selected(update, profile):
        return
    await update.message.reply_text(
        t(profile, "SECTION_COMMS"),
        reply_markup=_comms_keyboard(_user_lang(profile)),
    )


async def verify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    lang = _user_lang(profile)
    if _is_verified(profile):
        await _reply(
            update,
            t(profile, "ALREADY_VERIFIED", email=profile.email),
            reply_markup=_menu_keyboard(True, lang),
        )
        return ConversationHandler.END

    await _reply(
        update,
        t(profile, "ENTER_HSE_EMAIL"),
        reply_markup=_menu_keyboard(False, lang),
    )
    return AUTH_EMAIL


async def verify_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    profile = _profile_for_update(update)
    lang = _user_lang(profile)

    # отправляем код на email
    success, msg = _smtp_send_verification(profile.email, profile.verification_code, profile)
    await query.message.reply_text(msg)

    # ждём ввод кода (ConversationHandler)
    await query.message.reply_text(t(profile, "SEND_CODE_FIRST"))


async def verify_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()
    if not HSE_EMAIL_PATTERN.match(email):
        await update.message.reply_text(t(_profile_for_update(update), "EMAIL_INVALID"))
        return AUTH_EMAIL

    profile = _profile_for_update(update)
    profile.is_verified = False
    profile.verification_code = None
    profile.code_expires_at = None
    profile.save()

    code = f"{random.randint(100000, 999999)}"
    ok, message = _smtp_send_verification(email, code, profile)
    if not ok:
        await update.message.reply_text(message)
        return AUTH_EMAIL

    profile.email = email
    profile.verification_code = code
    profile.code_expires_at = datetime.utcnow() + timedelta(minutes=10)
    profile.save()

    await update.message.reply_text(
        t(_profile_for_update(update), "CODE_SENT")
    )
    return AUTH_CODE


async def verify_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    profile = _profile_for_update(update)
    lang = _user_lang(profile)  # язык пользователя

    if _is_verified(profile):
        await update.message.reply_text(
            t(profile, "ALREADY_CONFIRMED"),
            reply_markup=_menu_keyboard(True, lang)
        )
        return ConversationHandler.END

    if not profile.verification_code or not profile.code_expires_at:
        await update.message.reply_text(t(profile, "SEND_CODE_FIRST"))
        return ConversationHandler.END

    if datetime.utcnow() > profile.code_expires_at:
        await update.message.reply_text(t(profile, "CODE_EXPIRED"))
        profile.verification_code = None
        profile.code_expires_at = None
        profile.save()
        return ConversationHandler.END

    if code != profile.verification_code:
        await update.message.reply_text(t(profile, "CODE_WRONG"))
        return AUTH_CODE

    # Подтверждаем профиль
    # после успешной верификации
    profile.is_verified = True
    profile.verification_code = "CONFIRMED"
    profile.save()

    # меню
    await update.message.reply_text(t(profile, "CODE_OK"), reply_markup=_menu_keyboard(True, lang))

    # выбор общежития
    await update.message.reply_text(
        t(profile, "NOW_CHOOSE_DORM"),
        reply_markup=_dorm_keyboard(lang)
    )
    return ConversationHandler.END

async def change_dorm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)  # берём профиль пользователя
    user_language = profile.preferred_language or "ru"  # язык из профиля
    keyboard = _dorm_keyboard(user_language)  # кнопки на нужном языке
    await update.message.reply_text(
        t(profile, "CHOOSE_DORM"),  # текст тоже через t() с профилем
        reply_markup=keyboard
    )

async def dorm_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return

    dorm = query.data.replace("dorm_", "")
    profile.selected_dorm = dorm
    profile.save()
    lang = _user_lang(profile)
    await query.edit_message_text(t(profile, "DORM_CHOSEN", dorm=dorm))
    await query.message.reply_text(t(profile, "READY_TO_START"), reply_markup=_menu_keyboard(True, lang))


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST_CHANGE"))
        return ConversationHandler.END

    lang = _user_lang(profile)
    keyboard = [
        [InlineKeyboardButton(_listing_type_label("Продам", lang), callback_data="type_Продам")],
        [InlineKeyboardButton(_listing_type_label("Куплю", lang), callback_data="type_Куплю")],
    ]
    await update.message.reply_text(t(profile, "LISTING_TYPE_PROMPT"), reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data.replace("type_", "")

    profile = _profile_for_update(update)
    lang = _user_lang(profile)
    keyboard = [[InlineKeyboardButton(_category_label(c, lang), callback_data=f"cat_{c}")] for c in ALLOWED_CATEGORIES]
    await query.edit_message_text(t(profile, "LISTING_CATEGORY_PROMPT"), reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data.replace("cat_", "")
    profile = _profile_for_update(update)
    await query.edit_message_text(t(profile, "LISTING_ENTER_DESC"))
    return DESCRIPTION


async def add_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text(t(_profile_for_update(update), "DESC_EMPTY"))
        return DESCRIPTION

    context.user_data["description"] = desc
    await update.message.reply_text(t(_profile_for_update(update), "ENTER_CONTACT"))
    return CONTACT


async def add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text.strip()
    if not contact:
        await update.message.reply_text(t(_profile_for_update(update), "CONTACT_EMPTY"))
        return CONTACT

    context.user_data["contact"] = contact
    profile = _profile_for_update(update)
    keyboard = [[InlineKeyboardButton(t(profile, "SKIP_PHOTO_BTN"), callback_data="skip_photo")]]
    await update.message.reply_text(
        t(profile, "SEND_PHOTO_OR_SKIP"),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PHOTO


def _create_listing_from_draft(profile: UserProfile, user_id: int, context, photo_file_id=None, photo_type=None) -> Listing:
    m = build_multilingual(context.user_data["description"])
    return Listing.create(
        author_id=user_id,
        dorm=profile.selected_dorm,
        type=context.user_data["type"],
        category=context.user_data["category"],
        description=context.user_data["description"],
        description_lang=m.detected_lang,
        description_ru=m.ru,
        description_en=m.en,
        description_zh=m.zh,
        contact=context.user_data["contact"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile) or not profile.selected_dorm:
        await _reply(update, t(profile, "SESSION_EXPIRED_RESTART"))
        _clear_listing_draft(context)
        return ConversationHandler.END

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "skip_photo":
            _create_listing_from_draft(profile, update.effective_user.id, context)
            await query.edit_message_text(t(profile, "PHOTO_SKIPPED"))
            await query.message.reply_text(t(profile, "LISTING_CREATED"), reply_markup=_marketplace_keyboard(_user_lang(profile)))
            _clear_listing_draft(context)
            return ConversationHandler.END

    if update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt in ["skip", "/skip", "пропустить", "без фото"]:
            _create_listing_from_draft(profile, update.effective_user.id, context)
            await update.message.reply_text(t(profile, "PHOTO_SKIPPED"))
            await update.message.reply_text(t(profile, "LISTING_CREATED"), reply_markup=_marketplace_keyboard(_user_lang(profile)))
            _clear_listing_draft(context)
            return ConversationHandler.END
        await update.message.reply_text(t(profile, "NEED_PHOTO_OR_SKIP_CMD"))
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
            await update.message.reply_text(t(profile, "NOT_IMAGE"))
            return PHOTO
    else:
        await update.message.reply_text(t(profile, "NEED_PHOTO_OR_BUTTON"))
        return PHOTO

    _create_listing_from_draft(profile, update.effective_user.id, context, photo_file_id, photo_type)
    await update.message.reply_text(t(profile, "LISTING_CREATED"), reply_markup=_marketplace_keyboard(_user_lang(profile)))
    _clear_listing_draft(context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_listing_draft(context)
    _clear_lostfound_draft(context)
    await update.message.reply_text(t(_profile_for_update(update), "ACTION_CANCELLED"))
    return ConversationHandler.END


async def lostfound_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(profile, "LF_LOST"),
                    callback_data="lf_type_Потеряно",
                ),
                InlineKeyboardButton(
                    t(profile, "LF_FOUND"),
                    callback_data="lf_type_Найдено",
                ),
            ]
        ]
    )
    await update.message.reply_text(t(profile, "LF_PUBLISH_PROMPT"), reply_markup=keyboard)
    return LF_TYPE


async def lostfound_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["lf_type"] = query.data.replace("lf_type_", "")
    profile = _profile_for_update(update)
    await query.edit_message_text(t(profile, "LF_TITLE_PROMPT"))
    return LF_TITLE


async def lostfound_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text(t(_profile_for_update(update), "LF_TITLE_EMPTY"))
        return LF_TITLE
    context.user_data["lf_title"] = title
    await update.message.reply_text(t(_profile_for_update(update), "LF_DESC_PROMPT"))
    return LF_DESCRIPTION


async def lostfound_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text(t(_profile_for_update(update), "LF_DESC_EMPTY"))
        return LF_DESCRIPTION
    context.user_data["lf_description"] = description
    await update.message.reply_text(t(_profile_for_update(update), "LF_CONTACT_PROMPT"))
    return LF_CONTACT


async def lostfound_contact_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text.strip()
    if not contact:
        await update.message.reply_text(t(_profile_for_update(update), "CONTACT_EMPTY"))
        return LF_CONTACT
    context.user_data["lf_contact"] = contact
    profile = _profile_for_update(update)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(t(profile, "SKIP_PHOTO_BTN"), callback_data="lf_skip_photo")]])
    await update.message.reply_text(
        t(profile, "SEND_PHOTO_OR_SKIP"),
        reply_markup=keyboard,
    )
    return LF_PHOTO


async def lostfound_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not _is_verified(profile) or not profile.selected_dorm:
        await _reply(update, t(profile, "SESSION_EXPIRED_RESTART"))
        _clear_lostfound_draft(context)
        return ConversationHandler.END

    photo_file_id = None
    photo_type = None

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "lf_skip_photo":
            await query.edit_message_text(t(profile, "PHOTO_SKIPPED"))
            update_message = query.message
        else:
            update_message = query.message
    else:
        update_message = update.message

    if update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt not in ["skip", "/skip", "пропустить", "без фото"]:
            await update.message.reply_text(t(profile, "NEED_PHOTO_OR_SKIP_CMD"))
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
            await update.message.reply_text(t(profile, "NOT_IMAGE"))
            return LF_PHOTO

    title_m = build_multilingual(context.user_data["lf_title"])
    desc_m = build_multilingual(context.user_data["lf_description"])
    LostFoundItem.create(
        author_id=update.effective_user.id,
        dorm=profile.selected_dorm,
        item_type=context.user_data["lf_type"],
        title=context.user_data["lf_title"],
        description=context.user_data["lf_description"],
        text_lang=desc_m.detected_lang if desc_m.detected_lang != "unknown" else title_m.detected_lang,
        title_ru=title_m.ru,
        title_en=title_m.en,
        title_zh=title_m.zh,
        description_ru=desc_m.ru,
        description_en=desc_m.en,
        description_zh=desc_m.zh,
        contact=context.user_data["lf_contact"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )

    await update_message.reply_text(t(profile, "LF_PUBLISHED"), reply_markup=_marketplace_keyboard(_user_lang(profile)))
    _clear_lostfound_draft(context)
    return ConversationHandler.END


async def lostfound_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
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
        await update.message.reply_text(t(profile, "NO_ACTIVE_LF"))
        return

    await update.message.reply_text(
        t(profile, "LF_HEADER", dorm=profile.selected_dorm)
    )
    for item in items[:15]:
        await _send_lostfound_item(update, item, show_actions=(item.author_id == update.effective_user.id))


async def _send_lostfound_item(update: Update, item: LostFoundItem, show_actions: bool = False) -> None:
    message = _reply_message(update)
    viewer_profile = _profile_for_update(update)
    viewer_lang = _user_lang(viewer_profile)

    created = item.created_at.strftime("%d.%m %H:%M")

    title_txt = format_multilingual_for_user(
        item.title_ru,
        item.title_en,
        getattr(item, "title_zh", None),
        viewer_lang,
    )

    desc_txt = format_multilingual_for_user(
        item.description_ru,
        item.description_en,
        getattr(item, "description_zh", None),
        viewer_lang,
    )

    type_primary = item.item_type
    type_secondary = LF_TYPE_EN.get(item.item_type, item.item_type)

    if viewer_lang == "en":
        type_primary, type_secondary = type_secondary, item.item_type

    item_type_txt = (
        type_primary
        if type_secondary == type_primary
        else f"{type_primary} ({type_secondary})"
    )

    text = t(
        viewer_profile,
        "LF_ITEM_TEXT",
        id=item.id,
        type=item_type_txt,
        title=title_txt,
        desc=desc_txt,
        contact=item.contact,
        created=created,
    )

    markup = None

    if show_actions:

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t(viewer_profile, "LF_DONE"),
                        callback_data=f"lf_done_{item.id}",
                    ),
                    InlineKeyboardButton(
                        t(viewer_profile, "DELETE"),
                        callback_data=f"lf_del_{item.id}",
                    ),
                ]
            ]
        )

    if item.photo_file_id:

        if item.photo_type == "photo":

            await message.reply_photo(
                item.photo_file_id,
                caption=text,
                reply_markup=markup,
            )

        else:

            await message.reply_document(
                item.photo_file_id,
                caption=text,
                reply_markup=markup,
            )

    else:

        await message.reply_text(
            text,
            reply_markup=markup,
        )


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
        await query.message.reply_text(t(_profile_for_update(update), "LF_NOT_FOUND_OR_NOT_YOURS"))
        return
    item.status = "передано"
    item.save()
    await query.message.reply_text(t(_profile_for_update(update), "LF_CLOSED", id=item_id))


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
        await query.message.reply_text(t(_profile_for_update(update), "LF_NOT_FOUND_OR_NOT_YOURS"))
        return
    item.delete_instance()
    await query.message.reply_text(t(_profile_for_update(update), "LF_DELETED", id=item_id))


async def my_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
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
        await update.message.reply_text(t(profile, "NO_MY_LISTINGS"))
        return

    await update.message.reply_text(
        t(profile, "MY_ACTIVE_LISTINGS", dorm=profile.selected_dorm)
    )
    for listing in listings[:10]:
        await _send_listing(update, listing, with_actions=True)


async def list_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(profile, "LISTINGS_BUY_BTN"), callback_data="list_buy"),
                InlineKeyboardButton(t(profile, "LISTINGS_SELL_BTN"), callback_data="list_sell"),
            ]
        ]
    )
    await update.message.reply_text(t(profile, "WHICH_LISTINGS_SHOW"), reply_markup=keyboard)


async def list_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_type = "Куплю" if query.data == "list_buy" else "Продам"

    profile = _profile_for_update(update)

    listing_type_label = (
        t(profile, "BUY")
        if listing_type == "Куплю"
        else t(profile, "SELL")
    )

    await _send_listings_by_type(update, listing_type, listing_type_label)


async def _send_listings_by_type(update, listing_type, listing_type_label)-> None:
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await _reply(update, t(profile, "CHOOSE_DORM_FIRST"))
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
        await _reply(update, t(profile, "SECTION_EMPTY", section=listing_type_label))
        return

    await _reply(
        update,
        t(
            profile,
            "SECTION_HEADER",
            section=listing_type_label,
            dorm=profile.selected_dorm,
        ),
    )
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
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return ConversationHandler.END
    await update.message.reply_text(t(profile, "CHOOSE_ZONE"), reply_markup=_zone_picker_keyboard(profile))
    return BOOK_ZONE_NAME


def _zone_picker_keyboard(profile: UserProfile) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(profile, "ZONE_COWORKING"), callback_data="zone_pick_coworking")],
            [InlineKeyboardButton(t(profile, "ZONE_KITCHEN"), callback_data="zone_pick_kitchen")],
            [InlineKeyboardButton(t(profile, "ZONE_TUTOR"), callback_data="zone_pick_tutor")],
        ]
    )


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


async def _show_zone_days(query_message, zone_key: str, profile) -> None:
    zone_name = ZONE_MAP[zone_key]
    today, max_day = _booking_window_bounds()

    keyboard_rows = []  # список списков кнопок
    day_buttons = []    # текущая строка кнопок
    for i in range((max_day - today).days + 1):
        current_day = today + timedelta(days=i)
        if i == 0:
            label = t(profile, "TODAY")
        elif i == 1:
            label = t(profile, "TOMORROW")
        else:
            label = current_day.strftime("%d.%m")

        # создаём кнопку и добавляем в текущую строку
        day_buttons.append(
            InlineKeyboardButton(
                label,
                callback_data=f"zone_day_{zone_key}_{current_day.strftime('%Y%m%d')}"
            )
        )

        # каждые 3 кнопки формируем новую строку
        if len(day_buttons) == 3:
            keyboard_rows.append(day_buttons)
            day_buttons = []

    # оставшиеся кнопки (если их меньше 3)
    if day_buttons:
        keyboard_rows.append(day_buttons)
    keyboard_rows.append([InlineKeyboardButton(t(profile, "BACK_TO_ZONES"), callback_data="zone_back_to_zones")])

    # ОБЯЗАТЕЛЬНО передаём список списков кнопок
    reply_markup = InlineKeyboardMarkup(keyboard_rows)

    await query_message.reply_text(
        t(profile, "ZONE_PICK_DATE", zone=zone_name),
        reply_markup=reply_markup,
    )


async def _show_zone_slots(query_message, profile: UserProfile, zone_key: str, day: datetime) -> None:
    zone_name = ZONE_MAP[zone_key]
    date_key = day.strftime("%Y%m%d")
    if not _is_day_within_booking_window(day):
        await query_message.reply_text(t(profile, "BOOKING_WINDOW_ONLY"))
        return

    keyboard_rows = []
    free_slots = _slots_for_day(profile.selected_dorm, zone_key, day)
    for label, start_at, _ in free_slots[:20]:
        callback = f"zone_slot_{zone_key}_{date_key}_{start_at.hour:02d}"
        keyboard_rows.append([InlineKeyboardButton(label, callback_data=callback)])

    if not free_slots:
        keyboard_rows.append([InlineKeyboardButton(t(profile, "NO_FREE_SLOTS"), callback_data="zone_noslot")])
    keyboard_rows.append([InlineKeyboardButton(t(profile, "BACK_TO_DAYS"), callback_data=f"zone_back_{zone_key}")])

    text = t(profile, "ZONE_PICK_SLOT", zone=zone_name, date=day.strftime("%d.%m.%Y"))
    await query_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))



def language_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇨🇳 中文", callback_data="lang_zh"),
            ]
        ]
    )

async def zone_booking_zone_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await query.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return ConversationHandler.END

    zone_key = query.data.replace("zone_pick_", "")
    if zone_key not in ZONE_MAP:
        await query.message.reply_text(t(profile, "UNKNOWN_ZONE"))
        return ConversationHandler.END

    await _show_zone_days(query.message, zone_key, profile)
    return BOOK_ZONE_SLOT


async def zone_booking_slot_or_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await query.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return ConversationHandler.END

    if query.data == "zone_noslot":
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_back_"):
        if query.data == "zone_back_to_zones":
            await query.message.reply_text(t(profile, "CHOOSE_ZONE"), reply_markup=_zone_picker_keyboard(profile))
            return BOOK_ZONE_NAME
        _, _, zone_key = query.data.split("_", 2)
        if zone_key not in ZONE_MAP:
            await query.message.reply_text(t(profile, "UNKNOWN_ZONE"))
            return BOOK_ZONE_SLOT
        await _show_zone_days(query.message, zone_key, profile)
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_day_"):
        # zone_day_<zone_key>_<yyyymmdd>
        _, _, zone_key, day_key = query.data.split("_", 3)
        day = datetime.strptime(day_key, "%Y%m%d")
        if not _is_day_within_booking_window(day):
            await query.message.reply_text(t(profile, "BOOKING_WINDOW_ONLY_SHORT"))
            return BOOK_ZONE_SLOT
        await _show_zone_slots(query.message, profile, zone_key, day)
        return BOOK_ZONE_SLOT

    if query.data.startswith("zone_slot_"):
        # zone_slot_<zone_key>_<yyyymmdd>_<hour>
        _, _, zone_key, day_key, hour_txt = query.data.split("_", 4)
        day = datetime.strptime(day_key, "%Y%m%d")
        if not _is_day_within_booking_window(day):
            await query.message.reply_text(t(profile, "BOOKING_WINDOW_ONLY_SHORT"))
            return BOOK_ZONE_SLOT
        duration_hours, _ = _zone_slot_params(zone_key)
        start_at, end_at = _slot_datetime(day, int(hour_txt), duration_hours)
        zone_name = ZONE_MAP.get(zone_key)
        if not zone_name:
            await query.message.reply_text(t(profile, "UNKNOWN_ZONE"))
            return BOOK_ZONE_SLOT

        if _is_slot_busy(profile.selected_dorm, zone_name, start_at, end_at):
            await query.message.reply_text(t(profile, "SLOT_BUSY"))
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
            t(profile, "BOOKING_CREATED", zone=zone_name, slot=slot_text),
            reply_markup=_space_keyboard(_user_lang(profile)),
        )
        return ConversationHandler.END

    return BOOK_ZONE_SLOT


async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
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
        await update.message.reply_text(t(profile, "MY_BOOKINGS_NONE", dorm=profile.selected_dorm))
        return
    await update.message.reply_text(t(profile, "MY_BOOKINGS_HEADER", dorm=profile.selected_dorm))
    for b in bookings[:10]:
        markup = None
        if b.status in {"ожидает подтверждения", "подтверждено"}:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(profile, "CANCEL_BOOKING_BTN"), callback_data=f"book_cancel_{b.id}")]]
            )
        await update.message.reply_text(
            t(
                profile,
                "BOOKING_ROW",
                id=b.id,
                zone=b.zone_name,
                time=b.slot_text,
                status=b.status,
            ),
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
        await query.message.reply_text(t(_profile_for_update(update), "BOOKING_NOT_FOUND"))
        return

    if booking.status not in {"ожидает подтверждения", "подтверждено"}:
        await query.message.reply_text(t(_profile_for_update(update), "BOOKING_CANNOT_CANCEL"))
        return

    booking.status = "отменено"
    booking.save()
    await query.message.reply_text(t(_profile_for_update(update), "BOOKING_CANCELLED", id=booking_id))


async def laundry_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return

    rows = LaundryStatus.select().where(LaundryStatus.dorm == profile.selected_dorm)
    if not rows.exists():
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #1", status="свободна")
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #2", status="занята")
        LaundryStatus.create(dorm=profile.selected_dorm, machine_name="Стиралка #3", status="свободна")
        rows = LaundryStatus.select().where(LaundryStatus.dorm == profile.selected_dorm)

    await update.message.reply_text(t(profile, "LAUNDRY_HEADER", dorm=profile.selected_dorm))
    for row in rows:
        await update.message.reply_text(
            t(profile,"LAUNDRY_ROW",
            name=row.machine_name,
            status=row.status)
        )


async def announcements_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return
    rows = (
        OfficialAnnouncement.select()
        .where(OfficialAnnouncement.dorm == profile.selected_dorm)
        .order_by(OfficialAnnouncement.created_at.desc())
    )
    if not rows.exists():
        await update.message.reply_text(t(profile, "NO_OFFICIAL_ANN"))
        return
    await update.message.reply_text(t(profile, "OFFICIAL_ANN_HEADER", dorm=profile.selected_dorm))
    for row in rows[:15]:
        created = row.created_at.strftime("%d.%m %H:%M")
        await update.message.reply_text(
            t(profile,"ANN_ROW",
            date=created,
            title=row.title,
            text=row.text)
        )


async def announcement_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text(t(_profile_for_update(update), "ADMIN_ONLY"))
        return
    profile = _profile_for_update(update)
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return
    if not context.args:
        await update.message.reply_text(t(profile, "ANNOUNCE_FORMAT"))
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text(t(profile, "ANNOUNCE_NEED_PIPE"))
        return
    title, text = [part.strip() for part in raw.split("|", 1)]
    OfficialAnnouncement.create(
        dorm=profile.selected_dorm,
        title=title,
        text=text,
        created_by=update.effective_user.id,
    )
    await update.message.reply_text(t(profile, "OFFICIAL_ANN_PUBLISHED", dorm=profile.selected_dorm))


async def ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return ConversationHandler.END
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return ConversationHandler.END
    await update.message.reply_text(t(profile, "TICKET_THEME_PROMPT"))
    return TICKET_THEME


async def ticket_theme_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    theme = update.message.text.strip()
    if not theme:
        await update.message.reply_text(t(_profile_for_update(update), "TICKET_THEME_EMPTY"))
        return TICKET_THEME
    context.user_data["ticket_theme"] = theme
    await update.message.reply_text(t(_profile_for_update(update), "TICKET_DESC_PROMPT"))
    return TICKET_DESCRIPTION


async def ticket_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text(t(_profile_for_update(update), "TICKET_DESC_EMPTY"))
        return TICKET_DESCRIPTION
    context.user_data["ticket_description"] = description
    profile = _profile_for_update(update)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(t(profile, "SKIP_PHOTO_BTN"), callback_data="ticket_skip_photo")]])
    await update.message.reply_text(t(profile, "TICKET_PHOTO_OPTIONAL"), reply_markup=keyboard)
    return TICKET_PHOTO


async def ticket_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = _profile_for_update(update)
    if not profile.selected_dorm:
        message = _reply_message(update)
        await message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        context.user_data.pop("ticket_theme", None)
        context.user_data.pop("ticket_description", None)
        return ConversationHandler.END
    photo_file_id = None
    photo_type = None
    message = _reply_message(update)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "ticket_skip_photo":
            await query.edit_message_text(t(profile, "PHOTO_SKIPPED"))

    elif update.message and update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        photo_type = "photo"
    elif update.message and update.message.document:
        doc = update.message.document
        if doc.mime_type in ["image/png", "image/jpeg", "image/webp"]:
            photo_file_id = doc.file_id
            photo_type = "document"
        else:
            await update.message.reply_text(t(profile, "NOT_IMAGE"))
            return TICKET_PHOTO
    elif update.message and update.message.text:
        txt = update.message.text.strip().lower()
        if txt not in ["skip", "/skip", "пропустить", "без фото"]:
            await update.message.reply_text(t(profile, "SEND_PHOTO_OR_SKIP_SIMPLE"))
            return TICKET_PHOTO

    ticket = SupportTicket.create(
        user_id=update.effective_user.id,
        dorm=profile.selected_dorm,
        theme=context.user_data["ticket_theme"],
        description=context.user_data["ticket_description"],
        photo_file_id=photo_file_id,
        photo_type=photo_type,
    )
    context.user_data.pop("ticket_theme", None)
    context.user_data.pop("ticket_description", None)
    await message.reply_text(
        t(profile, "TICKET_CREATED", id=ticket.id),
        reply_markup=_comms_keyboard(_user_lang(profile)),
    )
    return ConversationHandler.END


async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not profile.selected_dorm:
        await update.message.reply_text(t(profile, "CHOOSE_DORM_FIRST"))
        return
    rows = (
        SupportTicket.select()
        .where(
            (SupportTicket.user_id == update.effective_user.id)
            & (SupportTicket.dorm == profile.selected_dorm)
        )
        .order_by(SupportTicket.created_at.desc())
    )
    if not rows.exists():
        await update.message.reply_text(t(profile, "NO_TICKETS_FOR_DORM", dorm=profile.selected_dorm))
        return
    await update.message.reply_text(t(profile, "MY_TICKETS_HEADER", dorm=profile.selected_dorm))
    for row in rows[:15]:
        await update.message.reply_text(
            t(
                profile,
                "TICKET_ROW",
                id=row.id,
                theme=row.theme,
                desc=row.description,
                status=row.status,
            )
        )


async def ticket_status_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text(t(_profile_for_update(update), "ADMIN_ONLY"))
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(t(_profile_for_update(update), "TICKET_STATUS_FORMAT"))
        return
    ticket_id = int(context.args[0])
    new_status = " ".join(context.args[1:]).strip()
    try:
        ticket = SupportTicket.get(SupportTicket.id == ticket_id)
    except SupportTicket.DoesNotExist:
        await update.message.reply_text(t(_profile_for_update(update), "TICKET_NOT_FOUND"))
        return
    ticket.status = new_status
    ticket.save()
    await update.message.reply_text(t(_profile_for_update(update), "TICKET_STATUS_UPDATED", id=ticket_id, status=new_status))

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
        await _reply(update, t(profile, "NOT_FOUND_DELETE"))
        return

    listing.delete_instance()
    await _reply(update, t(profile, "LISTING_DELETED", id=listing_id))


async def delete_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not context.args:
        await update.message.reply_text(t(profile, "DELETE_NEED_ID"))
        await my_ads(update, context)
        return
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t(profile, "ID_MUST_BE_NUMBER_DELETE"))
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
        await _reply(update, t(profile, "NOT_FOUND_BUY"))
        return

    is_buy_request = listing.type.strip().lower() == "куплю"

    listing.status = "куплено" if is_buy_request else "продано"
    listing.save()

    done_text = (
        t(profile, "BOUGHT_DONE")
        if is_buy_request
        else t(profile, "SOLD_DONE")
    )

    await _reply(
        update,
        t(profile, "LISTING_MARKED", id=listing_id, status=done_text),
    )

async def buy_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    if not context.args:
        await update.message.reply_text(t(profile, "BUY_NEED_ID"))
        return
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t(profile, "ID_MUST_BE_NUMBER_BUY"))
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

    dorm_text = (
        profile.selected_dorm
        if profile.selected_dorm
        else t(profile, "NOT_SELECTED")
    )

    text = t(
        profile,
        "INFO_TEXT",
        dorm=dorm_text,
    )

    await update.message.reply_text(text)


def _needs_translation(*values: str | None) -> bool:
    for v in values:
        if not v or not str(v).strip():
            return True
    return False


async def retranslate_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command: доперевести существующие записи Listing/LostFoundItem.
    Использование: /retranslate [--limit 200]
    """
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text(t(_profile_for_update(update), "ADMIN_ONLY"))
        return

    limit = 200
    if context.args and context.args[0].startswith("--limit"):
        try:
            limit = int(context.args[0].split("=", 1)[1])
        except Exception:
            limit = 200
    elif context.args and context.args[0].isdigit():
        limit = int(context.args[0])

    fixed_listings = 0
    fixed_lf = 0

    listings = Listing.select().order_by(Listing.created_at.desc()).limit(limit)
    for l in listings:
        if _needs_translation(l.description_ru, l.description_en, getattr(l, "description_zh", None)):
            m = build_multilingual(l.description or "")
            l.description_lang = l.description_lang or m.detected_lang
            l.description_ru = m.ru
            l.description_en = m.en
            l.description_zh = m.zh
            l.save()
            fixed_listings += 1

    items = LostFoundItem.select().order_by(LostFoundItem.created_at.desc()).limit(limit)
    for it in items:
        changed = False
        if _needs_translation(it.title_ru, it.title_en, getattr(it, "title_zh", None)):
            tm = build_multilingual(it.title or "")
            it.title_ru = tm.ru
            it.title_en = tm.en
            it.title_zh = tm.zh
            changed = True
        if _needs_translation(it.description_ru, it.description_en, getattr(it, "description_zh", None)):
            dm = build_multilingual(it.description or "")
            it.text_lang = it.text_lang or dm.detected_lang
            it.description_ru = dm.ru
            it.description_en = dm.en
            it.description_zh = dm.zh
            changed = True
        if changed:
            it.save()
            fixed_lf += 1

    await update.message.reply_text(
        f"Готово ✅\n"
        f"Listings обновлено: {fixed_listings}\n"
        f"Lost&Found обновлено: {fixed_lf}"
    )


async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await _ensure_verified(update)
    if not profile:
        return
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("English", callback_data="lang_en")],
            [InlineKeyboardButton("中文", callback_data="lang_zh")],
        ]
    )
    await _reply(update, t(profile, "LANG_CHOOSE"), reply_markup=keyboard)


async def language_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    profile = _profile_for_update(update)
    if not _is_verified(profile):
        await query.message.reply_text(t(profile, "NEED_VERIFY_FIRST"))
        return
    lang = query.data.replace("lang_", "").strip().lower()
    if lang not in {"ru", "en", "zh"}:
        await query.message.reply_text(t(profile, "LANG_UNKNOWN"))
        return
    profile.preferred_language = lang
    profile.save()
    await query.edit_message_text(t(profile, "LANG_UPDATED"))

    # Force immediate ReplyKeyboard refresh: Telegram updates reply keyboards
    # only when a new message is sent with reply_markup.
    new_lang = _user_lang(profile)
    await query.message.reply_text(
        t(profile, "MAIN_MENU_TEXT") if _is_verified(profile) else t(profile, "NEED_VERIFY_FIRST"),
        reply_markup=_menu_keyboard(_is_verified(profile), new_lang),
    )