from peewee import *
from playhouse.pool import SqliteDatabase

db = SqliteDatabase('aie.db')


class Member(Model):
    uuid = UUIDField(index=True, unique=True)
    name = CharField(unique=True, default='')
    email = CharField(index=True, unique=True, default='')
    phone = CharField(default='')
    infos = TextField(default='')
    admin = BooleanField(default=False)    
    class Meta:
        database = db


class Membership(Model):
    member = ForeignKeyField(Member, index=True, backref='memberships')
    class Meta:
        database = db


class Session(Model):
    place = CharField(default='')
    when = DateTimeField()
    notes = TextField(default='')
    public = BooleanField(default=False)
    max_attendees = IntegerField()
    class Meta:
        database = db

    
class SessionAttendee(Model):    
    session = ForeignKeyField(Session, index=True, backref='attendees')
    member = ForeignKeyField(Member)
    lead = BooleanField()
    class Meta:
        database = db

    
db.create_tables([Member, Membership, Session, SessionAttendee])
