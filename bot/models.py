import os
from datetime import datetime

from dotenv import load_dotenv
from peewee import *
from playhouse.db_url import connect

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    db = connect(DATABASE_URL)
else:
    db = SqliteDatabase("local.db")


class BaseModel(Model):
    class Meta:
        database = db


class UserProfile(BaseModel):
    telegram_id = BigIntegerField(unique=True)
    full_name = CharField()
    email = CharField(null=True)
    is_verified = BooleanField(default=False)
    selected_dorm = CharField(null=True)
    preferred_language = CharField(default="ru")  # ru | en
    verification_code = CharField(null=True)
    code_expires_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)


class Listing(BaseModel):
    id = AutoField()
    author_id = IntegerField()
    dorm = CharField(default="Общежитие 1")
    type = CharField()
    category = CharField()
    description = TextField()
    description_lang = CharField(null=True)  # detected language of original description
    description_ru = TextField(null=True)
    description_en = TextField(null=True)
    contact = CharField()
    status = CharField(default="активно")
    created_at = DateTimeField(constraints=[SQL("DEFAULT CURRENT_TIMESTAMP")])
    photo_file_id = CharField(null=True)
    photo_type = CharField(null=True)


class LostFoundItem(BaseModel):
    id = AutoField()
    author_id = IntegerField()
    dorm = CharField()
    item_type = CharField()  # Потеряно / Найдено
    title = CharField()
    description = TextField()
    text_lang = CharField(null=True)
    title_ru = CharField(null=True)
    title_en = CharField(null=True)
    description_ru = TextField(null=True)
    description_en = TextField(null=True)
    contact = CharField()
    status = CharField(default="активно")
    created_at = DateTimeField(constraints=[SQL("DEFAULT CURRENT_TIMESTAMP")])
    photo_file_id = CharField(null=True)
    photo_type = CharField(null=True)


class ZoneBooking(BaseModel):
    id = AutoField()
    user_id = IntegerField()
    dorm = CharField()
    zone_name = CharField()
    slot_text = CharField()
    start_at = DateTimeField(null=True)
    end_at = DateTimeField(null=True)
    status = CharField(default="ожидает подтверждения")
    created_at = DateTimeField(constraints=[SQL("DEFAULT CURRENT_TIMESTAMP")])


class LaundryStatus(BaseModel):
    id = AutoField()
    dorm = CharField()
    machine_name = CharField()
    status = CharField(default="свободна")
    updated_at = DateTimeField(default=datetime.utcnow)


class OfficialAnnouncement(BaseModel):
    id = AutoField()
    dorm = CharField(default="all")
    title = CharField()
    text = TextField()
    created_by = IntegerField()
    created_at = DateTimeField(constraints=[SQL("DEFAULT CURRENT_TIMESTAMP")])


class SupportTicket(BaseModel):
    id = AutoField()
    user_id = IntegerField()
    dorm = CharField()
    theme = CharField()
    description = TextField()
    status = CharField(default="новое")
    photo_file_id = CharField(null=True)
    photo_type = CharField(null=True)
    created_at = DateTimeField(constraints=[SQL("DEFAULT CURRENT_TIMESTAMP")])
