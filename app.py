from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pymysql, pymysql.cursors, os, re
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = 'campus_lf_secret_2024_secure_key'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Jinja Filter to safely handle datetime objects (MySQL) vs strings (SQLite)
@app.template_filter('format_datetime')
def format_datetime(value, length=None):
    if value is None:
        return ""
    
    # If it's already a string, keep it as is
    if isinstance(value, str):
        v = value
    else:
        # It's a datetime object (from MySQL)
        v = value.strftime('%Y-%m-%d %H:%M:%S')
        
    if length is not None:
        return v[:length]
    return v

# Database Configuration
DB_NAME = 'CampusFind'
DB_HOST = 'localhost'
DB_USER = 'root'
DB_PASSWORD = 'savio'  # UPDATE THIS to your actual MySQL password

class DBWrapperConn:
    def __init__(self, conn):
        self.conn = conn
        
    def execute(self, sql, params=None):
        # MySQL uses %s instead of ? for placeholders
        sql = sql.replace('?', '%s')
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor
        
    def commit(self):
        self.conn.commit()
        
    def close(self):
        self.conn.close()

CAMPUS_LOCATIONS = [
    "Central Library", "Main Cafeteria", "Computer Science Lab",
    "Physics Lab", "Chemistry Lab", "Biology Lab", "Engineering Block",
    "Arts Block", "Commerce Block", "Science Block", "Administration Block",
    "Auditorium", "Sports Ground", "Basketball Court", "Swimming Pool",
    "Gymnasium", "Boys Hostel", "Girls Hostel", "Parking Area",
    "Bus Stop", "Main Gate", "Medical Center", "Seminar Hall",
    "Workshop / Makerspace", "Garden / Open Area", "Restroom", "Other"
]

ITEM_CATEGORIES = [
    "Electronics", "Books & Stationery", "Clothing & Accessories",
    "ID Cards & Documents", "Keys", "Bags & Wallets",
    "Jewelry & Watches", "Sports Equipment", "Water Bottles & Tumblers",
    "Eyeglasses & Sunglasses", "Headphones & Earbuds", "Umbrellas", "Other"
]

def get_db():
    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )
    return DBWrapperConn(conn)

def init_db():
    # First connection to ensure database exists
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    cursor.close()
    conn.close()

    # Second connection to create tables
    db = get_db()
    cursor = db.conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            student_id VARCHAR(255) UNIQUE NOT NULL,
            department VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            type ENUM('lost','found') NOT NULL,
            status ENUM('active','returned','closed') NOT NULL DEFAULT 'active',
            title VARCHAR(255) NOT NULL,
            category VARCHAR(255) NOT NULL,
            description TEXT NOT NULL,
            location VARCHAR(255) NOT NULL,
            date_occurred VARCHAR(255) NOT NULL,
            contact_info VARCHAR(255),
            image_path VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sender_id INT NOT NULL,
            receiver_id INT NOT NULL,
            item_id INT NOT NULL,
            message TEXT NOT NULL,
            is_read TINYINT(1) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            body TEXT NOT NULL,
            link VARCHAR(255),
            is_read TINYINT(1) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    db.commit()
    db.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    db.close()
    return user

def find_matches(item_id, item_type, title, description, location, category):
    opposite_type = 'found' if item_type == 'lost' else 'lost'
    db = get_db()
    candidates = db.execute(
        "SELECT i.*, u.name as owner_name, u.email as owner_email FROM items i "
        "JOIN users u ON i.user_id=u.id "
        "WHERE i.type=? AND i.status='active' AND i.id!=?",
        (opposite_type, item_id)
    ).fetchall()
    db.close()

    keywords = set(re.findall(r'\w+', (title + ' ' + description + ' ' + category).lower()))
    stop_words = {'the','a','an','in','on','at','was','is','were','i','my','and','or','to','of','it','this','that'}
    keywords -= stop_words

    matches = []
    for c in candidates:
        c_text = (c['title'] + ' ' + c['description'] + ' ' + c['category']).lower()
        c_words = set(re.findall(r'\w+', c_text)) - stop_words
        common = keywords & c_words
        score = len(common)
        if c['location'] == location:
            score += 3
        if c['category'] == category:
            score += 2
        if score >= 2:
            matches.append({'item': dict(c), 'score': score})

    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches[:5]

def create_notification(user_id, title, body, link=None):
    db = get_db()
    db.execute(
        'INSERT INTO notifications (user_id, title, body, link) VALUES (?,?,?,?)',
        (user_id, title, body, link)
    )
    db.commit()
    db.close()

# ─── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        student_id = request.form.get('student_id','').strip()
        department = request.form.get('department','').strip()
        password = request.form.get('password','')
        confirm = request.form.get('confirm_password','')

        errors = []
        if not all([name, email, student_id, department, password]):
            errors.append('All fields are required.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            errors.append('Enter a valid email address.')

        if not errors:
            db = get_db()
            existing = db.execute('SELECT id FROM users WHERE email=? OR student_id=?', (email, student_id)).fetchone()
            if existing:
                errors.append('Email or Student ID already registered.')
            else:
                hashed = generate_password_hash(password)
                db.execute('INSERT INTO users (name,email,password,student_id,department) VALUES (?,?,?,?,?)',
                             (name, email, hashed, student_id, department))
                db.commit()
                db.close()
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login'))
            db.close()

        for e in errors:
            flash(e, 'danger')
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        db.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('index'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ─── MAIN ROUTES ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    db = get_db()
    lost_items = db.execute(
        "SELECT i.*,u.name as owner_name FROM items i JOIN users u ON i.user_id=u.id "
        "WHERE i.type='lost' AND i.status='active' ORDER BY i.created_at DESC LIMIT 6"
    ).fetchall()
    found_items = db.execute(
        "SELECT i.*,u.name as owner_name FROM items i JOIN users u ON i.user_id=u.id "
        "WHERE i.type='found' AND i.status='active' ORDER BY i.created_at DESC LIMIT 6"
    ).fetchall()
    stats = db.execute(
        "SELECT "
        "SUM(CASE WHEN type='lost' AND status='active' THEN 1 ELSE 0 END) as lost_count,"
        "SUM(CASE WHEN type='found' AND status='active' THEN 1 ELSE 0 END) as found_count,"
        "SUM(CASE WHEN status='returned' THEN 1 ELSE 0 END) as returned_count "
        "FROM items"
    ).fetchone()
    db.close()
    return render_template('index.html', lost_items=lost_items, found_items=found_items,
                           stats=stats, locations=CAMPUS_LOCATIONS, categories=ITEM_CATEGORIES)

@app.route('/report/<item_type>', methods=['GET','POST'])
@login_required
def report_item(item_type):
    if item_type not in ('lost','found'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = request.form.get('title','').strip()
        category = request.form.get('category','').strip()
        description = request.form.get('description','').strip()
        location = request.form.get('location','').strip()
        date_occurred = request.form.get('date_occurred','').strip()
        contact_info = request.form.get('contact_info','').strip()
        image_path = None

        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_path = filename

        if not all([title, category, description, location, date_occurred]):
            flash('Please fill in all required fields.', 'danger')
            return render_template('report.html', item_type=item_type,
                                   locations=CAMPUS_LOCATIONS, categories=ITEM_CATEGORIES)

        db = get_db()
        cursor = db.execute(
            'INSERT INTO items (user_id,type,title,category,description,location,date_occurred,contact_info,image_path) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (session['user_id'], item_type, title, category, description,
             location, date_occurred, contact_info, image_path)
        )
        item_id = cursor.lastrowid
        db.commit()
        db.close()

        matches = find_matches(item_id, item_type, title, description, location, category)
        if matches:
            match_titles = ', '.join([m['item']['title'] for m in matches[:3]])
            create_notification(
                session['user_id'],
                f"🎯 Potential match found for your {item_type} item!",
                f"Your item '{title}' may match: {match_titles}. Check the suggestions.",
                url_for('item_detail', item_id=item_id)
            )
            for m in matches:
                if m['item']['user_id'] != session['user_id']:
                    create_notification(
                        m['item']['user_id'],
                        f"🔍 Someone reported a {item_type} item matching yours!",
                        f"A new '{item_type}' report for '{title}' may match your item '{m['item']['title']}'.",
                        url_for('item_detail', item_id=item_id)
                    )

        flash(f'Your {item_type} item has been reported successfully!', 'success')
        return redirect(url_for('item_detail', item_id=item_id))

    return render_template('report.html', item_type=item_type,
                           locations=CAMPUS_LOCATIONS, categories=ITEM_CATEGORIES)

@app.route('/item/<int:item_id>')
def item_detail(item_id):
    db = get_db()
    item = db.execute(
        "SELECT i.*,u.name as owner_name,u.email as owner_email,u.department,u.student_id "
        "FROM items i JOIN users u ON i.user_id=u.id WHERE i.id=?", (item_id,)
    ).fetchone()
    if not item:
        db.close()
        flash('Item not found.', 'danger')
        return redirect(url_for('index'))

    messages = []
    if 'user_id' in session:
        messages = db.execute(
            "SELECT m.*,u.name as sender_name FROM messages m JOIN users u ON m.sender_id=u.id "
            "WHERE m.item_id=? AND (m.sender_id=? OR m.receiver_id=?) ORDER BY m.created_at ASC",
            (item_id, session['user_id'], session['user_id'])
        ).fetchall()
        db.execute(
            'UPDATE messages SET is_read=1 WHERE item_id=? AND receiver_id=?',
            (item_id, session['user_id'])
        )
        db.commit()

    matches = find_matches(item_id, item['type'], item['title'],
                           item['description'], item['location'], item['category'])
    db.close()
    return render_template('item_detail.html', item=item, messages=messages,
                           matches=matches, locations=CAMPUS_LOCATIONS)

@app.route('/search')
def search():
    q = request.args.get('q','').strip()
    category = request.args.get('category','')
    location = request.args.get('location','')
    item_type = request.args.get('type','')
    date_from = request.args.get('date_from','')
    date_to = request.args.get('date_to','')

    db = get_db()
    sql = ("SELECT i.*,u.name as owner_name FROM items i JOIN users u ON i.user_id=u.id "
           "WHERE i.status='active'")
    params = []

    if q:
        sql += " AND (i.title LIKE ? OR i.description LIKE ? OR i.category LIKE ?)"
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    if category:
        sql += " AND i.category=?"
        params.append(category)
    if location:
        sql += " AND i.location=?"
        params.append(location)
    if item_type:
        sql += " AND i.type=?"
        params.append(item_type)
    if date_from:
        sql += " AND i.date_occurred >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND i.date_occurred <= ?"
        params.append(date_to)

    sql += " ORDER BY i.created_at DESC"
    results = db.execute(sql, params).fetchall()
    db.close()

    return render_template('search.html', results=results, q=q, category=category,
                           location=location, item_type=item_type, date_from=date_from,
                           date_to=date_to, locations=CAMPUS_LOCATIONS, categories=ITEM_CATEGORIES)

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    item_id = request.form.get('item_id', type=int)
    receiver_id = request.form.get('receiver_id', type=int)
    message_text = request.form.get('message','').strip()

    if not all([item_id, receiver_id, message_text]):
        flash('Message cannot be empty.', 'danger')
        return redirect(url_for('item_detail', item_id=item_id))

    if receiver_id == session['user_id']:
        flash('You cannot message yourself.', 'warning')
        return redirect(url_for('item_detail', item_id=item_id))

    db = get_db()
    db.execute(
        'INSERT INTO messages (sender_id,receiver_id,item_id,message) VALUES (?,?,?,?)',
        (session['user_id'], receiver_id, item_id, message_text)
    )
    item = db.execute('SELECT title FROM items WHERE id=?', (item_id,)).fetchone()
    db.commit()
    db.close()

    create_notification(
        receiver_id,
        f"💬 New message about '{item['title']}'",
        f"{session['user_name']} sent you a message regarding an item.",
        url_for('item_detail', item_id=item_id)
    )
    flash('Message sent!', 'success')
    return redirect(url_for('item_detail', item_id=item_id))

@app.route('/update_status/<int:item_id>', methods=['POST'])
@login_required
def update_status(item_id):
    new_status = request.form.get('status')
    if new_status not in ('active','returned','closed'):
        flash('Invalid status.', 'danger')
        return redirect(url_for('item_detail', item_id=item_id))

    db = get_db()
    item = db.execute('SELECT * FROM items WHERE id=? AND user_id=?',
                        (item_id, session['user_id'])).fetchone()
    if not item:
        db.close()
        flash('Not authorized.', 'danger')
        return redirect(url_for('item_detail', item_id=item_id))

    db.execute('UPDATE items SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (new_status, item_id))
    db.commit()
    db.close()
    flash(f'Item status updated to "{new_status}".', 'success')
    return redirect(url_for('item_detail', item_id=item_id))

@app.route('/profile')
@login_required
def profile():
    user = get_current_user()
    db = get_db()
    my_items = db.execute(
        "SELECT * FROM items WHERE user_id=? ORDER BY created_at DESC",
        (session['user_id'],)
    ).fetchall()
    notifications = db.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (session['user_id'],)
    ).fetchall()
    unread_msgs = db.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE receiver_id=? AND is_read=0",
        (session['user_id'],)
    ).fetchone()
    db.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (session['user_id'],))
    db.commit()
    db.close()
    return render_template('profile.html', user=user, my_items=my_items,
                           notifications=notifications, unread_msgs=unread_msgs)

@app.route('/api/notifications')
@login_required
def api_notifications():
    db = get_db()
    notifs = db.execute(
        "SELECT COUNT(*) as cnt FROM notifications WHERE user_id=? AND is_read=0",
        (session['user_id'],)
    ).fetchone()
    msgs = db.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE receiver_id=? AND is_read=0",
        (session['user_id'],)
    ).fetchone()
    db.close()
    return jsonify({'notifications': notifs['cnt'], 'messages': msgs['cnt']})

@app.route('/all_items')
def all_items():
    item_type = request.args.get('type', 'lost')
    db = get_db()
    items = db.execute(
        "SELECT i.*,u.name as owner_name FROM items i JOIN users u ON i.user_id=u.id "
        "WHERE i.type=? AND i.status='active' ORDER BY i.created_at DESC",
        (item_type,)
    ).fetchall()
    db.close()
    return render_template('all_items.html', items=items, item_type=item_type,
                           locations=CAMPUS_LOCATIONS, categories=ITEM_CATEGORIES)

if __name__ == '__main__':
    os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)
    init_db()
    app.run(debug=True, port=5000)