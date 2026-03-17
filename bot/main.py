# bot/main.py

import os
from pathlib import Path
from dotenv import load_dotenv
from peewee import PostgresqlDatabase

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from handlers import (
    AUTH_CODE,
    AUTH_EMAIL,
    BUTTON_REGEX,
    CATEGORY,
    CONTACT,
    DESCRIPTION,
    BOOK_ZONE_NAME,
    BOOK_ZONE_SLOT,
    LF_CONTACT,
    LF_DESCRIPTION,
    LF_PHOTO,
    LF_TITLE,
    LF_TYPE,
    PHOTO,
    TICKET_DESCRIPTION,
    TICKET_PHOTO,
    TICKET_THEME,
    TYPE,
    add_contact,
    add_description,
    add_photo,
    add_start,
    announcement_create,
    buy_listing,
    booking_cancel_callback,
    cancel,
    category_selected,
    change_dorm,
    delete_listing,
    delete_listing_callback,
    dorm_chosen,
    info_command,
    laundry_status,
    lostfound_add_start,
    lostfound_contact_input,
    lostfound_delete_callback,
    lostfound_description_input,
    lostfound_done_callback,
    lostfound_list,
    lostfound_photo_input,
    lostfound_title_input,
    lostfound_type_selected,
    list_listings,
    list_type_callback,
    mark_listing_callback,
    my_bookings,
    my_ads,
    my_tickets,
    open_comms,
    open_marketplace,
    open_space,
    show_menu,
    start,
    ticket_description_input,
    ticket_photo_input,
    ticket_start,
    ticket_status_update,
    ticket_theme_input,
    type_selected,
    verify_code_input,
    verify_email_input,
    verify_start,
    verify_start_callback,
    zone_booking_slot_or_day_selected,
    zone_booking_start,
    zone_booking_zone_selected,
    announcements_list,
    language_menu,
    language_set_callback,
    retranslate_all,
)

from models import (
    LaundryStatus,
    Listing,
    LostFoundItem,
    OfficialAnnouncement,
    SupportTicket,
    UserProfile,
    ZoneBooking,
    db,
)

ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"
BOT_ENV = Path(__file__).resolve().parent / ".env"
load_dotenv(ROOT_ENV)
load_dotenv(BOT_ENV, override=False)
TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в .env!")


def _ensure_zonebooking_columns() -> None:
    """Backfill new ZoneBooking columns in existing sqlite DB."""
    try:
        rows = db.execute_sql("PRAGMA table_info(zonebooking);").fetchall()
        existing = {row[1] for row in rows}
    except Exception:
        existing = set()

    if "start_at" not in existing:
        try:
            db.execute_sql("ALTER TABLE zonebooking ADD COLUMN start_at DATETIME;")
        except Exception:
            pass
    if "end_at" not in existing:
        try:
            db.execute_sql("ALTER TABLE zonebooking ADD COLUMN end_at DATETIME;")
        except Exception:
            pass


def _table_columns_sqlite(table: str) -> set[str]:
    try:
        rows = db.execute_sql(f"PRAGMA table_info({table});").fetchall()
        return {row[1] for row in rows}
    except Exception:
        return set()


def _table_columns_postgres(table: str) -> set[str]:
    try:
        rows = db.execute_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s;",
            (table.lower(),),
        ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _ensure_translation_columns() -> None:
    """
    Add translation-related columns in existing DBs.
    Uses PRAGMA for SQLite and information_schema for Postgres.
    """
    is_postgres = isinstance(db, PostgresqlDatabase)
    if is_postgres:
        existing_listing = _table_columns_postgres("listing")
        existing_lf = _table_columns_postgres("lostfounditem")
        existing_profile = _table_columns_postgres("userprofile")
    else:
        existing_listing = _table_columns_sqlite("listing")
        existing_lf = _table_columns_sqlite("lostfounditem")
        existing_profile = _table_columns_sqlite("userprofile")

    listing_add = [
        ("description_lang", "VARCHAR(16)"),
        ("description_ru", "TEXT"),
        ("description_en", "TEXT"),
        ("description_zh", "TEXT"),
    ]
    lf_add = [
        ("text_lang", "VARCHAR(16)"),
        ("title_ru", "VARCHAR(255)"),
        ("title_en", "VARCHAR(255)"),
        ("title_zh", "VARCHAR(255)"),
        ("description_ru", "TEXT"),
        ("description_en", "TEXT"),
        ("description_zh", "TEXT"),
    ]
    profile_add = [
        ("preferred_language", "VARCHAR(8) DEFAULT 'ru'"),
    ]

    def add_if_missing(table: str, existing: set[str], col: str, ddl: str) -> None:
        if col in existing:
            return
        try:
            if is_postgres:
                db.execute_sql(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {col} {ddl};')
            else:
                db.execute_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl};")
        except Exception:
            pass

    for col, ddl in listing_add:
        add_if_missing("listing", existing_listing, col, ddl)
    for col, ddl in lf_add:
        add_if_missing("lostfounditem", existing_lf, col, ddl)
    for col, ddl in profile_add:
        add_if_missing("userprofile", existing_profile, col, ddl)


def main():
    if isinstance(db, PostgresqlDatabase):
        print("Подключение к PostgreSQL на Render")
    else:
        print("Локальная SQLite (для теста)")

    db.connect()
    db.create_tables(
        [
            Listing,
            UserProfile,
            LostFoundItem,
            ZoneBooking,
            LaundryStatus,
            OfficialAnnouncement,
            SupportTicket,
        ],
        safe=True,
    )
    _ensure_zonebooking_columns()
    _ensure_translation_columns()
    print("База данных готова")

    app = ApplicationBuilder().token(TOKEN).build()

    auth_conv = ConversationHandler(
        entry_points=[
            CommandHandler("verify", verify_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REGEX['VERIFY']}$"), verify_start),
            CallbackQueryHandler(verify_start_callback, pattern="^verify_start$"),
        ],
        states={
            AUTH_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_email_input)],
            AUTH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_code_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REGEX['ADD']}$"), add_start),
        ],
        states={
            TYPE: [CallbackQueryHandler(type_selected, pattern="^type_")],
            CATEGORY: [CallbackQueryHandler(category_selected, pattern="^cat_")],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_description)],
            CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_contact)],
            PHOTO: [
                MessageHandler(filters.PHOTO, add_photo),
                MessageHandler(filters.Document.ALL, add_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_photo),
                CallbackQueryHandler(add_photo, pattern="^skip_photo$"),
            ],

        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    lostfound_conv = ConversationHandler(
        entry_points=[
            CommandHandler("lostfound_add", lostfound_add_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REGEX['LOSTFOUND_ADD']}$"), lostfound_add_start),
        ],
        states={
            LF_TYPE: [CallbackQueryHandler(lostfound_type_selected, pattern="^lf_type_")],
            LF_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lostfound_title_input)],
            LF_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, lostfound_description_input)],
            LF_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lostfound_contact_input)],
            LF_PHOTO: [
                MessageHandler(filters.PHOTO, lostfound_photo_input),
                MessageHandler(filters.Document.ALL, lostfound_photo_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lostfound_photo_input),
                CallbackQueryHandler(lostfound_photo_input, pattern="^lf_skip_photo$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    booking_conv = ConversationHandler(
        entry_points=[
            CommandHandler("book_zone", zone_booking_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REGEX['BOOK_ZONE']}$"), zone_booking_start),
        ],
        states={
            BOOK_ZONE_NAME: [CallbackQueryHandler(zone_booking_zone_selected, pattern="^zone_pick_")],
            BOOK_ZONE_SLOT: [CallbackQueryHandler(zone_booking_slot_or_day_selected, pattern="^zone_(day|slot|noslot|back)_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    ticket_conv = ConversationHandler(
        entry_points=[
            CommandHandler("ticket_new", ticket_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REGEX['TICKET_NEW']}$"), ticket_start),
        ],
        states={
            TICKET_THEME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_theme_input)],
            TICKET_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_description_input)],
            TICKET_PHOTO: [
                MessageHandler(filters.PHOTO, ticket_photo_input),
                MessageHandler(filters.Document.ALL, ticket_photo_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_photo_input),
                CallbackQueryHandler(ticket_photo_input, pattern="^ticket_skip_photo$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(auth_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['START']}$"), start))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['MENU']}$"), show_menu))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['MARKETPLACE']}$"), open_marketplace))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['SPACE']}$"), open_space))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['COMMS']}$"), open_comms))
    app.add_handler(CallbackQueryHandler(dorm_chosen, pattern="^dorm_"))
    app.add_handler(CallbackQueryHandler(list_type_callback, pattern="^list_(buy|sell)$"))
    app.add_handler(CallbackQueryHandler(lostfound_done_callback, pattern="^lf_done_\\d+$"))
    app.add_handler(CallbackQueryHandler(lostfound_delete_callback, pattern="^lf_del_\\d+$"))
    app.add_handler(CallbackQueryHandler(booking_cancel_callback, pattern="^book_cancel_\\d+$"))
    app.add_handler(CallbackQueryHandler(mark_listing_callback, pattern="^mark_\\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_callback, pattern="^del_\\d+$"))
    app.add_handler(CommandHandler("change", change_dorm))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['CHANGE_DORM']}$"), change_dorm))
    app.add_handler(CommandHandler("lang", language_menu))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['LANG']}$"), language_menu))
    app.add_handler(CallbackQueryHandler(language_set_callback, pattern="^lang_(ru|en|zh)$"))


    app.add_handler(conv)
    app.add_handler(lostfound_conv)
    app.add_handler(booking_conv)
    app.add_handler(ticket_conv)

    app.add_handler(CommandHandler("list", list_listings))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['LIST']}$"), list_listings))
    app.add_handler(CommandHandler("my", my_ads))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['MY']}$"), my_ads))
    app.add_handler(CommandHandler("lostfound_list", lostfound_list))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['LOSTFOUND_LIST']}$"), lostfound_list))
    app.add_handler(CommandHandler("my_bookings", my_bookings))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['MY_BOOKINGS']}$"), my_bookings))
    app.add_handler(CommandHandler("laundry", laundry_status))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['LAUNDRY']}$"), laundry_status))
    app.add_handler(CommandHandler("announcements", announcements_list))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['ANNOUNCEMENTS']}$"), announcements_list))
    app.add_handler(CommandHandler("my_tickets", my_tickets))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['TICKET_MY']}$"), my_tickets))
    app.add_handler(CommandHandler("announce", announcement_create))
    app.add_handler(CommandHandler("ticket_status", ticket_status_update))
    app.add_handler(CommandHandler("delete", delete_listing))
    app.add_handler(CommandHandler("buy", buy_listing))
    app.add_handler(CommandHandler("retranslate", retranslate_all))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("help", info_command))
    app.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REGEX['INFO']}$"), info_command))

    print("Бот запущен. Ctrl+C — остановка")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()