# -*- coding: utf-8 -*-
"""
    MiniTwit
    ~~~~~~~~

    :copyright: (c) 2010 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
"""
from __future__ import with_statement
import time
#from sqlite3 import dbapi2 as sqlite3
from hashlib import md5
from datetime import datetime
from flask import Flask, request, session, url_for, redirect, \
     render_template, abort, g, flash, _app_ctx_stack 
from werkzeug import check_password_hash, generate_password_hash
import MySQLdb
import memcache
import string
import MySQLdb.cursors

# configuration
DATABASE = 'minitwitdb1.c9ti5icjkf8h.us-east-1.rds.amazonaws.com'
PORT = 3306
DB_NAME = 'minitwitdb'
DB_USER = 'tmnebel'
DB_PASS = 'numbers1'
PER_PAGE = 30
DEBUG = True
SECRET_KEY = 'development key'

# create our little application :)
app = Flask(__name__)
app.config.from_object(__name__)
app.config.from_envvar('MINITWIT_SETTINGS', silent=True)

mc = memcache.Client(['cluster-1.hiyqy9.0001.use1.cache.amazonaws.com:11211',
                    'cluster-1.hiyqy9.0002.use1.cache.amazonaws.com:11211',
                    'cluster-1.hiyqy9.0003.use1.cache.amazonaws.com:11211'], debug=0)
NO_CACHE = 0
TIMELINE = 1
GET_USER = 2
GET_USER_NAME = 3
USER_TIMELINE = 4
FOLLOW = 5
SELF_TWEETS = 6

def get_db():
    """Opens a new database connection if there is none yet for the
    current application context.
    """
    top = _app_ctx_stack.top
    if not hasattr(top, 'mysql_db'):
        top.mysql_db = MySQLdb.connect(host=DATABASE, port=PORT, user=DB_USER, passwd=DB_PASS, db=DB_NAME, cursorclass=MySQLdb.cursors.DictCursor)
    return top.mysql_db

def get_cursor():
    """ create the cursor object for the context if none exist, or return
    the current cursor.
    """
    top = _app_ctx_stack.top
    if not hasattr(top, 'cursor'):
        top.cursor = get_db().cursor()
    return top.cursor

@app.teardown_appcontext
def close_database(exception):
    """Closes the database and cursor again at the end of the request."""
    top = _app_ctx_stack.top
    if hasattr(top, 'mysql_db'):
        top.mysql_db.close()
    if hasattr(top, 'cursor'):
        top.cursor.close()

def flush_cache():
    mc.flush_all()

def init_db():
    """Creates the database tables."""
    with app.app_context():
        db = get_db()
        cursor = get_cursor()
        cursor.execute("drop table if exists user;")
        cursor.execute("create table user (user_id int primary key auto_increment, username varchar(255) not null, email varchar(255) not null, pw_hash varchar(255) not null);")
        cursor.execute("drop table if exists follower;")
        cursor.execute("create table follower (who_id int, whom_id int);")
        cursor.execute("drop table if exists message;")
        cursor.execute("create table message (message_id int primary key auto_increment, author_id int not null, text varchar(255) not null, pub_date int);")
        db.commit()
        mc.flush_all()

# TODO: optimize for one=True
def query_db(query, args=(), one=False, time=0, use=NO_CACHE):
    """Queries the database and returns a list of dictionaries."""

    key = ''
    rv = None
    if use != NO_CACHE:
        key = generate_key(use, args) 
        rv = mc.get(key)

    if not rv:
        cursor = get_cursor()
        cursor.execute(query, args)
        rv = cursor.fetchall()
        if use != NO_CACHE:
            mc.set(key, rv, time)
    return (rv[0] if rv else None) if one else rv

def generate_key(use, args=()):
    key = ''
    if use == TIMELINE:
        key = 'timeline'
    elif use == GET_USER_NAME or use == USER_TIMELINE or use == USER_TIMELINE \
            or use == GET_USER or use == SELF_TWEETS:
        key = str(args[0]) + ':' + str(use)
    elif use == FOLLOW:
        key = str(args[0]) + ':' + str(args[1]) + ':' + str(use)
    else:
        raise Exception('no user and not timeline request')

    key = key.encode('ascii','ignore')
    return key

def multi_invalidate_memcache(uses, args=()):
    l = list(generate_key(use, args) for use in uses)
    mc.delete_multi(l)

def invalidate_memcache(use, args=()):
    key = generate_key(use, args)
    mc.delete(key)

def get_user_id(username):
    """Convenience method to look up the id for a username."""
    rv = query_db('select * from user where username=%s', (username,)
                  , one=True, time=30, use=GET_USER)
    return rv['user_id'] if rv else None


def format_datetime(timestamp):
    """Format a timestamp for display."""
    return datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d @ %H:%M')


def gravatar_url(email, size=80):
    """Return the gravatar image for the given email address."""
    return 'http://www.gravatar.com/avatar/%s?d=identicon&s=%d' % \
        (md5(email.strip().lower().encode('utf-8')).hexdigest(), size)


@app.before_request
def before_request():
    g.user = None
    if 'user_id' in session:
        g.user = query_db('select * from user where user_id = %s',
                          (session['user_id'],), one=True, time=30, use=GET_USER_NAME)


@app.route('/')
def timeline():
    """Shows a users timeline or if no user is logged in it will
    redirect to the public timeline.  This timeline shows the user's
    messages as well as all the messages of followed users.
    """
    if not g.user:
        return redirect(url_for('public_timeline'))
    uid = session['user_id']
    key = generate_key(USER_TIMELINE, args=(uid,))
    page = mc.get(key)
    if page is None:
        m = query_db('''
            select message.*, user.* from message, user
            where message.author_id = user.user_id and (
                user.user_id = %s or
                user.user_id in (select whom_id from follower
                                        where who_id = %s))
            order by message.pub_date desc limit %s''',
            (uid, uid, PER_PAGE))
        page = render_template('timeline.html', messages=m)
        mc.set(key, page, 30)
        return page
    return page


@app.route('/public')
def public_timeline():
    """Displays the latest messages of all users."""
    key = generate_key(TIMELINE)
    page = mc.get(key)
    if page is None:
        page = render_template('timeline.html', messages=query_db('''
            select message.*, user.* from message, user
            where message.author_id = user.user_id
            order by message.pub_date desc limit %s''', (PER_PAGE,)))
        mc.set(key, page, 30)
        return page
    return page


@app.route('/<username>')
def user_timeline(username):
    """Display's a users tweets."""
    profile_user = query_db('select * from user where username = %s',
                            (username,), one=True, time=30, use=GET_USER)
    if profile_user is None:
        abort(404)
    followed = False
    if g.user:
        followed = query_db('''select 1 from follower where
            follower.who_id = %s and follower.whom_id = %s''',
            (session['user_id'], profile_user['user_id']),
            one=True, time=30, use=FOLLOW) is not None
    return render_template('timeline.html', messages=query_db('''
            select message.*, user.* from message, user where
            user.user_id = message.author_id and user.user_id =%s
            order by message.pub_date desc limit %s''',
            [profile_user['user_id'], PER_PAGE], time=30, use=SELF_TWEETS), followed=followed,
            profile_user=profile_user)


@app.route('/<username>/follow')
def follow_user(username):
    """Adds the current user as follower of the given user."""
    if not g.user:
        abort(401)
    whom_id = get_user_id(username)
    if whom_id is None:
        abort(404)
    db = get_db()
    cursor = get_cursor()
    cursor.execute('insert into follower (who_id, whom_id) values (%s, %s)',
              (session['user_id'], whom_id))
    db.commit()
    multi_invalidate_memcache((FOLLOW, USER_TIMELINE), (session['user_id'], whom_id))
    flash('You are now following "%s"' % username)
    return redirect(url_for('user_timeline', username=username))


@app.route('/<username>/unfollow')
def unfollow_user(username):
    """Removes the current user as follower of the given user."""
    if not g.user:
        abort(401)
    whom_id = get_user_id(username)
    if whom_id is None:
        abort(404)
    db = get_db()
    cursor = get_cursor()
    cursor.execute('delete from follower where who_id=%s and whom_id=%s',
              (session['user_id'], whom_id))
    db.commit()
    multi_invalidate_memcache((FOLLOW, USER_TIMELINE), (session['user_id'], whom_id))
    flash('You are no longer following "%s"' % username)
    return redirect(url_for('user_timeline', username=username))


@app.route('/add_message', methods=['POST'])
def add_message():
    """Registers a new message for the user."""
    if 'user_id' not in session:
        abort(401)
    if request.form['text']:
        db = get_db()
        cursor = get_cursor()
        cursor.execute('''insert into message (author_id, text, pub_date)
          values (%s, %s, %s)''', (session['user_id'], request.form['text'],
                                int(time.time())))
        db.commit()
        multi_invalidate_memcache((SELF_TWEETS, USER_TIMELINE, TIMELINE), args=(session['user_id'],))
        flash('Your message was recorded')
    return redirect(url_for('timeline'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Logs the user in."""
    if g.user:
        return redirect(url_for('timeline'))
    error = None
    if request.method == 'POST':
        user = query_db('''select * from user where
            username = %s''', (request.form['username'],), one=True, time=30, use=GET_USER)
        if user is None:
            error = 'Invalid username'
        elif not check_password_hash(user['pw_hash'],
                                     request.form['password']):
            error = 'Invalid password'
        else:
            flash('You were logged in')
            session['user_id'] = user['user_id']
            return redirect(url_for('timeline'))
    return render_template('login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Registers the user."""
    if g.user:
        return redirect(url_for('timeline'))
    error = None
    if request.method == 'POST':
        if not request.form['username']:
            error = 'You have to enter a username'
        elif not request.form['email'] or \
                 '@' not in request.form['email']:
            error = 'You have to enter a valid email address'
        elif not request.form['password']:
            error = 'You have to enter a password'
        elif request.form['password'] != request.form['password2']:
            error = 'The two passwords do not match'
        elif get_user_id(request.form['username']) is not None:
            error = 'The username is already taken'
        else:
            db = get_db()
            cursor = get_cursor()
            cursor.execute('''insert into user (
              username, email, pw_hash) values (%s, %s, %s)''',
              (request.form['username'], request.form['email'],
               generate_password_hash(request.form['password'])))
            db.commit()
            invalidate_memcache(GET_USER, args=(request.form['username'],))
            flash('You were successfully registered and can login now')
            return redirect(url_for('login'))
    return render_template('register.html', error=error)


@app.route('/logout')
def logout():
    """Logs the user out."""
    flash('You were logged out')
    session.pop('user_id', None)
    # could just invalidate the user relevant items here,
    # for our purposes this works fine
    mc.flush_all()
    return redirect(url_for('public_timeline'))


# add some filters to jinja
app.jinja_env.filters['datetimeformat'] = format_datetime
app.jinja_env.filters['gravatar'] = gravatar_url


if __name__ == '__main__':
    init_db()
    app.run()
