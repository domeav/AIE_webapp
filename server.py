#!/bin/env python

import smtplib
import local_settings
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, url_for, redirect, abort
from email_validator import validate_email, EmailNotValidError
from models import db, DoesNotExist, Member, Membership, Session, SessionAttendee, fn
from uuid import uuid4
from datetime import datetime, date
import logging
import time
import locale
from collections import defaultdict, deque
from threading import Lock
from uuid import UUID


app = Flask(__name__)
app.config['SERVER_NAME'] = local_settings.SERVER_NAME
locale.setlocale(locale.LC_ALL, 'fr_FR.utf8')
log = logging.getLogger('gunicorn.error')

# Rate limit for the login form (in memory: the app runs a single process).
LOGIN_LIMITS = {'ip': (5, 3600), 'email': (2, 600)}
_login_hits = defaultdict(deque)
_login_lock = Lock()


def client_ip():
    # nginx appends the real client address as the last X-Forwarded-For entry.
    forwarded = request.headers.get('X-Forwarded-For', '')
    return forwarded.split(',')[-1].strip() or request.remote_addr


def login_allowed(kind, key):
    limit, window = LOGIN_LIMITS[kind]
    now = time.monotonic()
    with _login_lock:
        hits = _login_hits[(kind, key)]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def get_member(uuid):
    try:
        uuid = UUID(str(uuid))
    except ValueError:
        abort(404)
    member = Member.get_or_none(Member.uuid == uuid)
    if member is None:
        abort(404)
    return member


@app.before_request
def _db_connect():
    db.connect(reuse_if_open=True)


@app.teardown_request
def _db_close(exc):
    if not db.is_closed():
        db.close()
    

def send_mail(recipient, uuid):
    connection = smtplib.SMTP(local_settings.SMTP_HOST,
                              local_settings.SMTP_PORT, timeout=20)
    connection.starttls()
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
        member = get_member(uuid)
        if member.name and member.phone and member.infos:
            return redirect(url_for('sessions', uuid=uuid))
        else:
            return redirect(url_for('profile', uuid=uuid))
    if request.method == 'POST':
        if not login_allowed('ip', client_ip()):
            return render_template('error.html', error="Trop de demandes, réessaie plus tard."), 429
        try:
            emailinfo = validate_email(request.form.get('email'),
                                       check_deliverability=True)
            email = emailinfo.normalized.lower()
        except EmailNotValidError as e:
            return render_template('error.html', error=f"Adresse mail invalide : {e}"), 400
        if not login_allowed('email', email):
            return render_template('error.html', error="Un lien a déjà été envoyé à cette adresse, patiente quelques minutes."), 429
        try:
            try:
                member = Member.get(Member.email == email)
            except DoesNotExist:
                member = Member.create(uuid=uuid4(), email=email, name=email)
                member.save()
            send_mail(email, uuid=member.uuid)
            return render_template('ok.html')
        except Exception:
            log.exception('login mail failed')
            return render_template('error.html', error="L'envoi du mail a échoué."), 500
    return render_template('index.html')


@app.route('/<uuid>/profile/', methods=['GET', 'POST'])
def profile(uuid):
    member = get_member(uuid)
    if request.method == "POST":
        member.name = request.form.get('name')
        member.phone = request.form.get('phone')
        member.infos = request.form.get('infos')
        member.save()
        return redirect(url_for('sessions', uuid=uuid))
    member = get_member(uuid)
    return render_template('profile.html', member=member)


@app.route('/<uuid>/sessions/', methods=['GET', 'POST'])
def sessions(uuid):
    member = get_member(uuid)
    if request.method == 'POST':
        session_id = request.form.get('session_id')
        if Session.get_or_none(Session.id == session_id) is None:
            abort(404)
        try:
            attendee = SessionAttendee.get(SessionAttendee.member_id == member.id, SessionAttendee.session_id == session_id)
            attendee.delete_instance()
        except DoesNotExist:
            attendee = SessionAttendee(session=session_id, member_id=member.id, lead=False)
            attendee.save()
        return redirect(url_for("sessions", uuid=uuid))
    sessions = Session.select().where(Session.when > datetime.now().date()).order_by(Session.when.asc())
    return render_template('sessions.html', sessions=sessions, uuid=uuid, member=member, SessionAttendee=SessionAttendee, today=date.today())


@app.route('/<uuid>/edit_session/<session_id>/', methods=['GET', 'POST'])
@app.route('/<uuid>/create_session/', methods=['GET', 'POST'])
def edit_session(uuid, session_id=None):
    member = get_member(uuid)
    if session_id:
        session = Session.get_or_none(Session.id == session_id)
        if session is None:
            abort(404)
        participation = SessionAttendee.get_or_none(SessionAttendee.member == member, SessionAttendee.session == session)
        if not member.admin and not (participation and participation.lead):
            abort(401)
    else:
        session = Session()
    if request.method == 'GET':
        members = Member.select().order_by(fn.Lower(Member.name))
        return render_template('edit_session.html', member=member, session=session, members=members)
    elif request.method == 'POST':
        if 'delete' in request.form:
            session.delete_instance()
        else:
            session.place = request.form.get('place')
            session.when = datetime.fromisoformat(request.form.get('when'))
            session.notes = request.form.get('notes')
            session.max_attendees = request.form.get('max_attendees')
            session.confirmed = 'confirmed' in request.form
            session.public = 'public' in request.form
            session.save()
            if request.form.get('lead'):
                try:
                    attendee = SessionAttendee.get(SessionAttendee.member_id == request.form.get('lead'),
                                                   SessionAttendee.session == session)
                    attendee.lead = True
                except DoesNotExist:
                    attendee = SessionAttendee(session=session, member_id=request.form.get('lead'), lead=True)
                attendee.save()
            elif len(session.attendees) == 0:
                attendee = SessionAttendee(session=session, member=member, lead=True)
                attendee.save()                
        return redirect(url_for('sessions', uuid=uuid))
    else:
        raise NotImplementedError()


@app.route('/<uuid>/create_member/', methods=['POST'])
def create_member(uuid):
    member = get_member(uuid)
    if not member.admin:
        abort(401)
    try:
        emailinfo = validate_email(request.form.get('email'),
                                   check_deliverability=True)
    except EmailNotValidError as e:
        return render_template('error.html', error=f"Adresse mail invalide : {e}"), 400
    email = emailinfo.normalized.lower()
    new_member = Member.get_or_none(Member.email == email)
    if new_member is None:
        new_member = Member.create(uuid=uuid4(), email=email, name=email)
    return redirect(url_for('profile', uuid=new_member.uuid))


@app.route('/<uuid>/members/')
def members(uuid):
    member = get_member(uuid)
    members = Member.select().order_by(fn.Lower(Member.name))
    return render_template("members.html", member=member, members=members)
