#!/bin/env python

import smtplib
import local_settings
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, url_for, redirect, abort
from email_validator import validate_email, EmailNotValidError
from models import db, DoesNotExist, Member, Membership, Session, SessionAttendee, fn
from uuid import uuid4
from datetime import datetime
import traceback
import locale


app = Flask(__name__)
app.config['SERVER_NAME'] = local_settings.SERVER_NAME
locale.setlocale(locale.LC_ALL, 'fr_FR.utf8')


@app.before_request
def _db_connect():
    db.connect(reuse_if_open=True)


@app.teardown_request
def _db_close(exc):
    if not db.is_closed():
        db.close()
    

def send_mail(recipient, uuid):
    connection = smtplib.SMTP(local_settings.SMTP_HOST,
                              local_settings.SMTP_PORT)
    connection.starttls()
    connection.set_debuglevel(True)
    connection.login(local_settings.SMTP_USER,
                     local_settings.SMTP_PASSWD)
    html = f'''
    <html>
    <body>
    Cher ami de la chose sonore,<br/><br/>
    Voici ton lien perso pour accéder aux rendez-vous de l'Atelier Impro/Électro,
    il n'est rien qu'à toi, garde le bien au chaud :<br/><br/>
    <a href="{url_for('index', uuid=uuid, _external=True)}">clique ici !</a>
    </body>
    </html>
    '''
    msg = MIMEMultipart()
    msg['From'] = local_settings.SMTP_USER
    msg['To'] = recipient
    msg['Subject'] = 'Accès aux rendez-vous AIE'
    msg.attach(MIMEText(html, 'html'))
    email_string = msg.as_string()
    connection.sendmail(local_settings.SMTP_USER,
                        [recipient],
                        msg.as_string())


@app.route('/', methods=['GET', 'POST'], defaults={'uuid': None})
@app.route('/<uuid>/')
def index(uuid):
    if uuid:
        member = Member.get(Member.uuid == uuid)
        if member.name and member.phone and member.infos:
            return redirect(url_for('sessions', uuid=uuid))
        else:
            return redirect(url_for('profile', uuid=uuid))
    if request.method == 'POST':
        try:
            emailinfo = validate_email(request.form.get('email'),
                                       check_deliverability=True)
            email = emailinfo.normalized
            try:
                member = Member.get(Member.email == email)
            except DoesNotExist:
                member = Member.create(uuid=uuid4(), email=email)
                member.save()
            send_mail(emailinfo.normalized, uuid=member.uuid)            
            return render_template('ok.html')
        except Exception:
            return render_template('error.html', error=traceback.format_exc())
    return render_template('index.html')


@app.route('/<uuid>/profile/', methods=['GET', 'POST'])
def profile(uuid):
    member = Member.get(Member.uuid == uuid)
    if request.method == "POST":
        member.name = request.form.get('name')
        member.phone = request.form.get('phone')
        member.infos = request.form.get('infos')
        member.save()
        return redirect(url_for('sessions', uuid=uuid))
    member = Member.get(Member.uuid == uuid)
    return render_template('profile.html', member=member)


@app.route('/<uuid>/sessions/', methods=['GET', 'POST'])
def sessions(uuid):
    member = Member.get(Member.uuid == uuid)
    if request.method == 'POST':
        session_id = request.form.get('session_id')
        try:
            attendee = SessionAttendee.get(SessionAttendee.member == member, SessionAttendee.session == session_id)
            attendee.delete_instance()
        except DoesNotExist:
            attendee = SessionAttendee(session=session_id, member=member, lead=False)
            attendee.save()
        return redirect(url_for("sessions", uuid=uuid))
    sessions = Session.select().where(Session.when > datetime.now().date()).order_by(Session.when.asc())
    return render_template('sessions.html', sessions=sessions, uuid=uuid, member=member, SessionAttendee=SessionAttendee)


@app.route('/<uuid>/edit_session/<session_id>/', methods=['GET', 'POST'])
@app.route('/<uuid>/create_session/', methods=['GET', 'POST'])
def edit_session(uuid, session_id=None):
    member = Member.get(Member.uuid == uuid)
    if session_id:
        session = Session.get(Session.id == session_id)
        participation = SessionAttendee.get_or_none(SessionAttendee.member == member, SessionAttendee.session == session)
        if not member.admin and not (participation and participation.lead):
            abort(401)
    else:
        session = Session()
    if request.method == 'GET':
        return render_template('edit_session.html', member=member, session=session)
    elif request.method == 'POST':
        if 'delete' in request.form:
            session.delete_instance()
        else:
            session.place = request.form.get('place')
            session.when = datetime.fromisoformat(request.form.get('when'))
            session.notes = request.form.get('notes')
            session.max_attendees = request.form.get('max_attendees')
            session.public = 'public' in request.form        
            session.save()
            if len(session.attendees) == 0:
                attendee = SessionAttendee(session=session, member=member, lead=True)
                attendee.save()
        return redirect(url_for('sessions', uuid=uuid))
    else:
        raise NotImplementedError()


@app.route('/<uuid>/create_member/', methods=['POST'])
def create_member(uuid):
    member = Member.get(Member.uuid == uuid)
    if not member.admin:
        abort(401)
    new_member = Member.create(uuid=uuid4(), email=request.form.get('email'))
    new_member.save()
    return redirect(url_for('profile', uuid=new_member.uuid))


@app.route('/<uuid>/members/')
def members(uuid):
    member = Member.get(Member.uuid == uuid)
    members = Member.select().order_by(fn.Lower(Member.name))
    return render_template("members.html", member=member, members=members)
