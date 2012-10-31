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

mc = memcache.Client(['cluster-1.hiyqy9.0001.use1.cache.amazonaws.com:11211'], debug=0)
all_bytes = string.maketrans('', '')

"""
TODO: 
don't use the query as the key, rather use the user and the type of query.
from this, invalidate cache entries after particular writes

NEW PLAN:
the idea will be, rather than invalidate, carefully keep track of when we 
need to not retreive from cache, this will pull from database and update cache
implicitly

GETTING RENDERING WORKING
look into returning list instead of tuple, converting strings to utf-8

query1: get user id based on username
query2: select user given user_id 
query3: select message and user given user_id
query4: show latest messages of all users
query5: see is user has followers
"""


def get_db():
    """Opens a new database connection if there is none yet for the
    current application context.
    """
    top = _app_ctx_stack.top
    if not hasattr(top, 'mysql_db'):
        #top.mysql_db = MySQLdb.connect(host=DATABASE, port=PORT, user=DB_USER, passwd=DB_PASS, db=DB_NAME)
        top.mysql_db = MySQLdb.connect(host=DATABASE, port=PORT, user=DB_USER, passwd=DB_PASS, db=DB_NAME, cursorclass=MySQLdb.cursors.DictCursor)
	#sqlite3.connect(app.config['DATABASE'])
    return top.mysql_db

def get_cursor():
    top = _app_ctx_stack.top
    if not hasattr(top, 'cursor'):
        top.cursor = get_db().cursor()
    return top.cursor

@app.teardown_appcontext
def close_database(exception):
    """Closes the database again at the end of the request."""
    top = _app_ctx_stack.top
    if hasattr(top, 'mysql_db'):
        top.mysql_db.close()


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

# TODO: optimize for one=True
def query_db(query, args=(), one=False, cache=False):
    """Queries the database and returns a list of dictionaries."""

    if cache:
        key = (query % args).encode('ascii','ignore').replace(" ", "")
        key = key.translate(all_bytes, all_bytes[:32])
        rv = mc.get(key)
    else:
        rv = None
    if not rv:
        cursor = get_cursor()
        cursor.execute(query, args)
        rv = cursor.fetchall()
        if cache:
            mc.set(key, rv, 60)
    return (rv[0] if rv else None) if one else rv


def get_user_id(username):
    """Convenience method to look up the id for a username."""
    rv = query_db('select user_id from user where username=%s', (username,)
                  , one=True, cache=False)
    return rv['user_id'] if rv else None


def format_datetime(timestamp):
    """Format a timestamp for display."""
    #timestamp = time.time()
    return datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d @ %H:%M')


def gravatar_url(email, size=80):
    """Return the gravatar image for the given email address."""
    #email = "foo@example.com"
    return 'http://www.gravatar.com/avatar/%s?d=identicon&s=%d' % \
        (md5(email.strip().lower().encode('utf-8')).hexdigest(), size)


@app.before_request
def before_request():
    g.user = None
    if 'user_id' in session:
        g.user = query_db('select * from user where user_id = %s',
                          (session['user_id'],), one=True)


@app.route('/')
def timeline():
    """Shows a users timeline or if no user is logged in it will
    redirect to the public timeline.  This timeline shows the user's
    messages as well as all the messages of followed users.
    """
    if not g.user:
        return redirect(url_for('public_timeline'))
    uid = session['user_id']
    m = query_db('''
        select message.*, user.* from message, user
        where message.author_id = user.user_id and (
            user.user_id = %s or
            user.user_id in (select whom_id from follower
                                    where who_id = %s))
        order by message.pub_date desc limit %s''',
        (uid, uid, PER_PAGE))
    return render_template('timeline.html', messages=m)


@app.route('/public')
def public_timeline():
    """Displays the latest messages of all users."""
    return render_template('timeline.html', messages=query_db('''
        select message.*, user.* from message, user
        where message.author_id = user.user_id
        order by message.pub_date desc limit %s''', (PER_PAGE,)))


@app.route('/<username>')
def user_timeline(username):
    """Display's a users tweets."""
    profile_user = query_db('select * from user where username = %s',
                            (username,), one=True)
    if profile_user is None:
        abort(404)
    followed = False
    if g.user:
        followed = query_db('''select 1 from follower where
            follower.who_id = %s and follower.whom_id = %s''',
            (session['user_id'], profile_user['user_id']),
            one=True) is not None
        #TODO: eliminate above query
        #dict type below
        # keep a num of followers counter in memcache
    return render_template('timeline.html', messages=query_db('''
            select message.*, user.* from message, user where
            user.user_id = message.author_id and user.user_id =%s
            order by message.pub_date desc limit %s''',
            [profile_user['user_id'], PER_PAGE]), followed=followed,
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
    #TODO invalidate here
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
    #TODO invalidate here
    db.commit()
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
        #TODO: invalidate here
        flash('Your message was recorded')
        """
        TODO: make the query in timeline not retreive from cache
        if coming from here
        """
    return redirect(url_for('timeline'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Logs the user in."""
    if g.user:
        return redirect(url_for('timeline'))
    error = None
    if request.method == 'POST':
        user = query_db('''select * from user where
            username = %s''', (request.form['username']), one=True)
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
            #TODO invalidate user id key here
            flash('You were successfully registered and can login now')
            return redirect(url_for('login'))
    return render_template('register.html', error=error)


@app.route('/logout')
def logout():
    """Logs the user out."""
    flash('You were logged out')
    session.pop('user_id', None)
    return redirect(url_for('public_timeline'))


# add some filters to jinja
app.jinja_env.filters['datetimeformat'] = format_datetime
app.jinja_env.filters['gravatar'] = gravatar_url


if __name__ == '__main__':
    init_db()
    app.run()
