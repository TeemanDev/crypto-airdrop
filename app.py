from flask import Flask, render_template, request, jsonify, redirect, url_for
import sqlite3
import re
import secrets
import string
import requests
import json
from datetime import datetime
from dotenv import load_dotenv
import os
import base64
import hashlib
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'fallback-secret-key')

# Twitter API Configuration
TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN')
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET')
TWITTER_USERNAME = os.getenv('TWITTER_USERNAME', '').replace('@', '').strip()

# Token distribution configuration
TOKEN_CONFIG = {
    'token_name': 'TEST',
    'token_symbol': 'TEST',
    'total_supply': 1000000,
    'points_to_tokens_ratio': 10,  # 10 points = 1 token
    'distribution_date': '2024-12-31',  # Future distribution date
    'min_points_for_distribution': 100
}

# Database initialization
def init_db():
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT UNIQUE NOT NULL,
            email TEXT,
            twitter_handle TEXT,
            twitter_id TEXT,
            twitter_verified BOOLEAN DEFAULT FALSE,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            points INTEGER DEFAULT 0,
            is_verified BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Twitter verification table
    c.execute('''
        CREATE TABLE IF NOT EXISTS twitter_verification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL,
            twitter_handle TEXT NOT NULL,
            twitter_id TEXT NOT NULL,
            follower_count INTEGER DEFAULT 0,
            following_project BOOLEAN DEFAULT FALSE,
            retweeted_post BOOLEAN DEFAULT FALSE,
            verified_at TIMESTAMP,
            FOREIGN KEY (wallet_address) REFERENCES users (wallet_address),
            UNIQUE(wallet_address, twitter_handle)
        )
    ''')
    
    # Tasks table
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL,
            task_name TEXT NOT NULL,
            completed BOOLEAN DEFAULT FALSE,
            completed_at TIMESTAMP,
            proof TEXT,
            FOREIGN KEY (wallet_address) REFERENCES users (wallet_address),
            UNIQUE(wallet_address, task_name)
        )
    ''')
    
    # Referrals table
    c.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_wallet TEXT NOT NULL,
            referred_wallet TEXT NOT NULL,
            referral_code TEXT NOT NULL,
            completed_tasks INTEGER DEFAULT 0,
            earned_points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referred_wallet)
        )
    ''')
    
    # Token distribution table
    c.execute('''
        CREATE TABLE IF NOT EXISTS token_distribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL,
            tokens_earned INTEGER DEFAULT 0,
            tokens_distributed INTEGER DEFAULT 0,
            distribution_tx_hash TEXT,
            distribution_status TEXT DEFAULT 'pending',
            distribution_date TIMESTAMP,
            points_used INTEGER DEFAULT 0,
            FOREIGN KEY (wallet_address) REFERENCES users (wallet_address),
            UNIQUE(wallet_address)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Database initialized successfully!")

# Initialize database
init_db()

# Helper functions
def generate_referral_code():
    characters = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(characters) for _ in range(8))

def add_user(wallet_address, email=None, twitter_handle=None, referral_code=None):
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    user_referral_code = generate_referral_code()
    
    try:
        referred_by = None
        if referral_code and referral_code.strip():
            c.execute('SELECT wallet_address FROM users WHERE referral_code = ?', (referral_code,))
            result = c.fetchone()
            if result:
                referred_by = result[0]
        
        c.execute('''
            INSERT INTO users (wallet_address, email, twitter_handle, referral_code, referred_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (wallet_address, email, twitter_handle, user_referral_code, referred_by))
        
        # If referred, add to referrals table and give points
        if referred_by:
            c.execute('''
                INSERT INTO referrals (referrer_wallet, referred_wallet, referral_code)
                VALUES (?, ?, ?)
            ''', (referred_by, wallet_address, referral_code))
            
            # Give points to referrer
            c.execute('UPDATE users SET points = points + 50 WHERE wallet_address = ?', (referred_by,))
        
        conn.commit()
        return True, user_referral_code
    except sqlite3.IntegrityError:
        return False, None
    except Exception as e:
        print(f"❌ Error adding user: {e}")
        return False, None
    finally:
        conn.close()

def initialize_user_tasks(wallet_address):
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    tasks = [
        ('join_airdrop', 'Join Airdrop'),
        ('follow_twitter', 'Follow us on Twitter'),
        ('retweet', 'Retweet our pinned post'),
        ('join_telegram', 'Join our Telegram'),
        ('invite_friends', 'Invite 3 friends')
    ]
    
    for task_id, task_name in tasks:
        c.execute('''
            INSERT OR IGNORE INTO user_tasks (wallet_address, task_name)
            VALUES (?, ?)
        ''', (wallet_address, task_id))
    
    conn.commit()
    conn.close()

def complete_task(wallet_address, task_name, proof_data=None):
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    try:
        # Determine points based on task
        task_points = {
            'join_airdrop': 100,
            'follow_twitter': 50,
            'retweet': 75,
            'join_telegram': 50,
            'invite_friends': 150
        }
        
        points_earned = task_points.get(task_name, 0)
        
        if proof_data:
            c.execute('''
                UPDATE user_tasks 
                SET completed = TRUE, completed_at = CURRENT_TIMESTAMP, proof = ?
                WHERE wallet_address = ? AND task_name = ?
            ''', (json.dumps(proof_data), wallet_address, task_name))
        else:
            c.execute('''
                UPDATE user_tasks 
                SET completed = TRUE, completed_at = CURRENT_TIMESTAMP
                WHERE wallet_address = ? AND task_name = ?
            ''', (wallet_address, task_name))
        
        # Add points to user
        c.execute('UPDATE users SET points = points + ? WHERE wallet_address = ?', (points_earned, wallet_address))
        
        conn.commit()
        success = c.rowcount > 0
        
        # Update token earnings if task completed successfully
        if success:
            update_token_earnings(wallet_address)
            
        conn.close()
        return success
    except Exception as e:
        print(f"Error completing task: {e}")
        return False

def get_user_tasks(wallet_address):
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    c.execute('''
        SELECT task_name, completed, completed_at 
        FROM user_tasks 
        WHERE wallet_address = ?
    ''', (wallet_address,))
    
    tasks = c.fetchall()
    conn.close()
    
    task_dict = {}
    for task_name, completed, completed_at in tasks:
        task_dict[task_name] = {
            'completed': bool(completed),
            'completed_at': completed_at
        }
    
    return task_dict

def is_valid_wallet_address(address):
    if not address:
        return False
    if not re.match(r'^0x[a-fA-F0-9]{40}$', address):
        return False
    return True

# Token Distribution Functions
def calculate_tokens_from_points(points):
    """Calculate tokens based on points"""
    return points // TOKEN_CONFIG['points_to_tokens_ratio']

def initialize_token_distribution(wallet_address):
    """Initialize token distribution for a user"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT OR IGNORE INTO token_distribution (wallet_address)
            VALUES (?)
        ''', (wallet_address,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error initializing token distribution: {e}")
        return False
    finally:
        conn.close()

def update_token_earnings(wallet_address):
    """Update token earnings based on current points"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    try:
        # Get user points
        c.execute('SELECT points FROM users WHERE wallet_address = ?', (wallet_address,))
        result = c.fetchone()
        
        if result:
            points = result[0]
            tokens_earned = calculate_tokens_from_points(points)
            
            c.execute('''
                UPDATE token_distribution 
                SET tokens_earned = ?
                WHERE wallet_address = ?
            ''', (tokens_earned, wallet_address))
            
            conn.commit()
            return tokens_earned
        return 0
    except Exception as e:
        print(f"Error updating token earnings: {e}")
        return 0
    finally:
        conn.close()

def simulate_token_distribution(wallet_address):
    """Simulate token distribution with fake transaction hash"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    try:
        # Get tokens earned
        c.execute('SELECT tokens_earned FROM token_distribution WHERE wallet_address = ?', (wallet_address,))
        result = c.fetchone()
        
        if result and result[0] > 0:
            tokens = result[0]
            
            # Generate fake transaction hash
            tx_data = f"{wallet_address}{tokens}{time.time()}"
            fake_tx_hash = hashlib.sha256(tx_data.encode()).hexdigest()[:64]
            
            c.execute('''
                UPDATE token_distribution 
                SET tokens_distributed = ?,
                    distribution_tx_hash = ?,
                    distribution_status = 'completed',
                    distribution_date = CURRENT_TIMESTAMP,
                    points_used = ?
                WHERE wallet_address = ?
            ''', (tokens, f"0x{fake_tx_hash}", tokens * TOKEN_CONFIG['points_to_tokens_ratio'], wallet_address))
            
            conn.commit()
            
            # Reset user points after distribution
            c.execute('UPDATE users SET points = 0 WHERE wallet_address = ?', (wallet_address,))
            conn.commit()
            
            return {
                'success': True,
                'tokens': tokens,
                'tx_hash': f"0x{fake_tx_hash}",
                'message': f'✅ {tokens} {TOKEN_CONFIG["token_symbol"]} tokens distributed successfully!'
            }
        else:
            return {
                'success': False,
                'message': 'No tokens available for distribution'
            }
    except Exception as e:
        print(f"Error simulating token distribution: {e}")
        return {
            'success': False,
            'message': 'Distribution failed'
        }
    finally:
        conn.close()

# Twitter API Functions
def get_twitter_bearer_token():
    """Get Bearer token using API key and secret"""
    if not TWITTER_API_KEY or not TWITTER_API_SECRET:
        return None
    
    try:
        # Encode credentials
        credentials = base64.b64encode(f"{TWITTER_API_KEY}:{TWITTER_API_SECRET}".encode()).decode()
        
        headers = {
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
        }
        
        data = {
            'grant_type': 'client_credentials'
        }
        
        response = requests.post(
            'https://api.twitter.com/oauth2/token',
            headers=headers,
            data=data
        )
        
        if response.status_code == 200:
            token_data = response.json()
            return token_data.get('access_token')
        else:
            print(f"❌ Failed to get Bearer token: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error getting Bearer token: {e}")
        return None

def verify_twitter_follow(twitter_handle):
    """Verify if user follows our project on Twitter using real API"""
    # Use provided Bearer token or generate one
    bearer_token = TWITTER_BEARER_TOKEN
    if not bearer_token and TWITTER_API_KEY and TWITTER_API_SECRET:
        bearer_token = get_twitter_bearer_token()
    
    if not bearer_token or not TWITTER_USERNAME:
        print("🐦 Twitter API not configured - using simulation")
        return True, f"simulated_{twitter_handle}_id"
    
    try:
        headers = {
            'Authorization': f'Bearer {bearer_token}'
        }
        
        # Get our project's Twitter ID
        project_url = f'https://api.twitter.com/2/users/by/username/{TWITTER_USERNAME}'
        project_response = requests.get(project_url, headers=headers)
        
        if project_response.status_code != 200:
            print(f"❌ Could not find project Twitter account: {TWITTER_USERNAME}")
            return False, None
            
        project_data = project_response.json()
        project_id = project_data['data']['id']
        print(f"✅ Found project ID: {project_id} for @{TWITTER_USERNAME}")
        
        # Get user's Twitter ID
        user_url = f'https://api.twitter.com/2/users/by/username/{twitter_handle}'
        user_response = requests.get(user_url, headers=headers)
        
        if user_response.status_code != 200:
            print(f"❌ Could not find user: {twitter_handle}")
            return False, None
            
        user_data = user_response.json()
        user_id = user_data['data']['id']
        print(f"✅ Found user ID: {user_id} for @{twitter_handle}")
        
        # Check if user follows our project
        following_url = f'https://api.twitter.com/2/users/{user_id}/following'
        following_response = requests.get(following_url, headers=headers, 
                                        params={'max_results': 1000})
        
        if following_response.status_code == 200:
            following_data = following_response.json()
            if 'data' in following_data:
                for followed_user in following_data['data']:
                    if followed_user['id'] == project_id:
                        print(f"✅ User @{twitter_handle} follows @{TWITTER_USERNAME}")
                        return True, user_id
            
            print(f"❌ User @{twitter_handle} does not follow @{TWITTER_USERNAME}")
            return False, user_id
        else:
            print(f"❌ Error checking follows: {following_response.status_code}")
            return False, user_id
        
    except Exception as e:
        print(f"❌ Twitter API Error: {e}")
        return False, None

def verify_twitter_retweet(twitter_handle, tweet_id):
    """Verify if user retweeted specific tweet"""
    bearer_token = TWITTER_BEARER_TOKEN
    if not bearer_token and TWITTER_API_KEY and TWITTER_API_SECRET:
        bearer_token = get_twitter_bearer_token()
    
    if not bearer_token:
        print("🐦 Twitter API not configured - simulating retweet verification")
        return True
    
    try:
        headers = {
            'Authorization': f'Bearer {bearer_token}'
        }
        
        # Get user ID
        user_url = f'https://api.twitter.com/2/users/by/username/{twitter_handle}'
        user_response = requests.get(user_url, headers=headers)
        
        if user_response.status_code != 200:
            return False
            
        user_data = user_response.json()
        user_id = user_data['data']['id']
        
        # Check user's retweets (this endpoint might need additional permissions)
        retweets_url = f'https://api.twitter.com/2/users/{user_id}/tweets'
        retweets_response = requests.get(retweets_url, headers=headers, 
                                       params={'max_results': 100, 'exclude': 'replies'})
        
        if retweets_response.status_code == 200:
            tweets_data = retweets_response.json()
            if 'data' in tweets_data:
                for tweet in tweets_data['data']:
                    # Check if this is a retweet of our tweet
                    if 'retweeted_status' in tweet and tweet['retweeted_status']['id'] == tweet_id:
                        return True
        
        return False
        
    except Exception as e:
        print(f"❌ Twitter Retweet Error: {e}")
        return False

def save_twitter_verification(wallet_address, twitter_handle, twitter_id, follows_project=False, retweeted=False):
    """Save Twitter verification data"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT OR REPLACE INTO twitter_verification 
            (wallet_address, twitter_handle, twitter_id, following_project, retweeted_post, verified_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (wallet_address, twitter_handle, twitter_id, follows_project, retweeted))
        
        # Update user table
        c.execute('''
            UPDATE users 
            SET twitter_handle = ?, twitter_id = ?, twitter_verified = TRUE
            WHERE wallet_address = ?
        ''', (twitter_handle, twitter_id, wallet_address))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"❌ Save Twitter Verification Error: {e}")
        return False
    finally:
        conn.close()

def get_twitter_user_info(twitter_handle):
    """Get Twitter user public information"""
    bearer_token = TWITTER_BEARER_TOKEN
    if not bearer_token and TWITTER_API_KEY and TWITTER_API_SECRET:
        bearer_token = get_twitter_bearer_token()
    
    if not bearer_token:
        return None
    
    try:
        headers = {
            'Authorization': f'Bearer {bearer_token}'
        }
        
        url = f'https://api.twitter.com/2/users/by/username/{twitter_handle}'
        response = requests.get(url, headers=headers, 
                              params={'user.fields': 'created_at,public_metrics,verified,description'})
        
        if response.status_code == 200:
            return response.json()['data']
        return None
        
    except Exception as e:
        print(f"❌ Twitter User Info Error: {e}")
        return None

# Routes
@app.route('/')
def index():
    referral_code = request.args.get('ref', '')
    return render_template('index.html', referral_code=referral_code)

@app.route('/join-airdrop', methods=['POST'])
def join_airdrop():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'})
        
        wallet_address = data.get('wallet_address', '').strip()
        email = data.get('email', '').strip()
        twitter_handle = data.get('twitter_handle', '').strip()
        referral_code = data.get('referral_code', '').strip()
        
        print(f"📨 Received: {wallet_address}")
        
        if not wallet_address:
            return jsonify({'success': False, 'message': 'Wallet address is required'})
        
        if not is_valid_wallet_address(wallet_address):
            return jsonify({'success': False, 'message': 'Invalid wallet address. Must start with 0x and be 42 characters.'})
        
        success, user_referral_code = add_user(wallet_address, email, twitter_handle, referral_code)
        
        if success:
            initialize_user_tasks(wallet_address)
            complete_task(wallet_address, 'join_airdrop')
            initialize_token_distribution(wallet_address)  # Initialize token distribution
            print(f"✅ User registered: {wallet_address}")
            return jsonify({
                'success': True, 
                'message': 'Successfully joined airdrop! Redirecting to tasks...',
                'referral_code': user_referral_code
            })
        else:
            return jsonify({'success': False, 'message': 'Wallet already registered'})
            
    except Exception as e:
        print(f"❌ Error in join_airdrop: {e}")
        return jsonify({'success': False, 'message': 'Server error. Please try again.'})

@app.route('/dashboard')
def dashboard():
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    user_count = c.fetchone()[0]
    
    c.execute('SELECT wallet_address, twitter_handle, points, registered_at FROM users ORDER BY registered_at DESC')
    users = c.fetchall()
    conn.close()
    
    html = f'''
    <html>
    <head>
        <title>Dashboard</title>
        <style>
            body {{ font-family: Arial; padding: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
            .user-card {{ background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #667eea; }}
            .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
            .stat-card {{ background: white; padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .stat-number {{ font-size: 2rem; font-weight: bold; color: #667eea; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Airdrop Dashboard</h1>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-number">{user_count}</div>
                    <div>Total Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{sum(user[2] for user in users)}</div>
                    <div>Total Points</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{len([u for u in users if u[1]])}</div>
                    <div>Twitter Connected</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">
                        <a href="/token-dashboard" style="color: #667eea; text-decoration: none;">
                            💰 Token Dashboard
                        </a>
                    </div>
                    <div>Token Management</div>
                </div>
            </div>
            
            <h2>Registered Users:</h2>
    '''
    for user in users:
        twitter_info = f" | Twitter: @{user[1]}" if user[1] else ""
        points_info = f" | Points: {user[2]}" if user[2] else ""
        html += f'<div class="user-card">{user[0]}{twitter_info}{points_info} | Joined: {user[3]}</div>'
    
    html += '</div></body></html>'
    return html

@app.route('/tasks')
def tasks_page():
    return render_template('tasks.html')

@app.route('/profile')
def profile_page():
    return render_template('profile.html')

@app.route('/token-dashboard')
def token_dashboard():
    """Token distribution dashboard"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    # Get distribution stats
    c.execute('''
        SELECT 
            COUNT(*) as total_users,
            SUM(tokens_earned) as total_tokens_earned,
            SUM(tokens_distributed) as total_tokens_distributed,
            COUNT(CASE WHEN distribution_status = 'completed' THEN 1 END) as distributions_completed
        FROM token_distribution
    ''')
    stats = c.fetchone()
    
    # Get recent distributions
    c.execute('''
        SELECT wallet_address, tokens_distributed, distribution_tx_hash, distribution_date
        FROM token_distribution 
        WHERE distribution_status = 'completed'
        ORDER BY distribution_date DESC 
        LIMIT 10
    ''')
    recent_distributions = c.fetchall()
    
    conn.close()
    
    return render_template('token_dashboard.html', 
                         stats=stats,
                         recent_distributions=recent_distributions,
                         config=TOKEN_CONFIG)

@app.route('/complete-task', methods=['POST'])
def complete_user_task():
    try:
        data = request.get_json()
        wallet_address = data.get('wallet_address', '').strip()
        task_name = data.get('task_name', '')
        
        if not wallet_address or not task_name:
            return jsonify({'success': False, 'message': 'Missing parameters'})
        
        if complete_task(wallet_address, task_name):
            return jsonify({'success': True, 'message': 'Task completed successfully!'})
        else:
            return jsonify({'success': False, 'message': 'Task not found'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@app.route('/tasks/<wallet_address>')
def get_tasks(wallet_address):
    try:
        tasks = get_user_tasks(wallet_address)
        
        task_descriptions = {
            'join_airdrop': {'name': 'Join Airdrop', 'points': 100},
            'follow_twitter': {'name': 'Follow us on Twitter', 'points': 50},
            'retweet': {'name': 'Retweet our pinned post', 'points': 75},
            'join_telegram': {'name': 'Join our Telegram', 'points': 50},
            'invite_friends': {'name': 'Invite 3 friends', 'points': 150}
        }
        
        completed_tasks = sum(1 for task in tasks.values() if task['completed'])
        total_tasks = len(task_descriptions)
        total_points = sum(task_descriptions[task_id]['points'] for task_id, task in tasks.items() if task['completed'])
        max_points = sum(task['points'] for task in task_descriptions.values())
        
        return jsonify({
            'tasks': tasks,
            'descriptions': task_descriptions,
            'progress': {
                'completed': completed_tasks,
                'total': total_tasks,
                'percentage': int((completed_tasks / total_tasks) * 100) if total_tasks > 0 else 0,
                'points': total_points,
                'max_points': max_points
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/user-tokens/<wallet_address>')
def get_user_tokens(wallet_address):
    """Get user token information"""
    conn = sqlite3.connect('airdrop.db')
    c = conn.cursor()
    
    # Get user points and tokens
    c.execute('''
        SELECT u.points, COALESCE(td.tokens_earned, 0), COALESCE(td.tokens_distributed, 0), 
               td.distribution_status, td.distribution_tx_hash
        FROM users u
        LEFT JOIN token_distribution td ON u.wallet_address = td.wallet_address
        WHERE u.wallet_address = ?
    ''', (wallet_address,))
    
    result = c.fetchone()
    conn.close()
    
    if result:
        points, tokens_earned, tokens_distributed, status, tx_hash = result
        return jsonify({
            'points': points,
            'tokens_earned': tokens_earned,
            'tokens_distributed': tokens_distributed,
            'distribution_status': status,
            'tx_hash': tx_hash,
            'points_to_tokens_ratio': TOKEN_CONFIG['points_to_tokens_ratio'],
            'next_tokens': calculate_tokens_from_points(points)
        })
    else:
        return jsonify({'error': 'User not found'})

@app.route('/claim-tokens', methods=['POST'])
def claim_tokens():
    """Simulate token claim"""
    data = request.get_json()
    wallet_address = data.get('wallet_address', '').strip()
    
    if not wallet_address:
        return jsonify({'success': False, 'message': 'Wallet address required'})
    
    # Initialize distribution if not exists
    initialize_token_distribution(wallet_address)
    
    # Update token earnings
    tokens_earned = update_token_earnings(wallet_address)
    
    if tokens_earned >= 10:  # Minimum tokens to claim
        result = simulate_token_distribution(wallet_address)
        return jsonify(result)
    else:
        return jsonify({
            'success': False,
            'message': f'Minimum {10} tokens required for distribution. You have {tokens_earned}.'
        })

@app.route('/verify-twitter', methods=['POST'])
def verify_twitter():
    try:
        data = request.get_json()
        wallet_address = data.get('wallet_address', '').strip()
        twitter_handle = data.get('twitter_handle', '').strip().replace('@', '')
        
        if not wallet_address or not twitter_handle:
            return jsonify({'success': False, 'message': 'Wallet address and Twitter handle are required'})
        
        print(f"🐦 Verifying Twitter follow for: {twitter_handle}")
        
        # Verify Twitter follow
        follows_project, twitter_id = verify_twitter_follow(twitter_handle)
        
        if follows_project:
            # Get additional user info
            user_info = get_twitter_user_info(twitter_handle)
            follower_count = user_info.get('public_metrics', {}).get('followers_count', 0) if user_info else 0
            
            # Save verification data
            save_twitter_verification(wallet_address, twitter_handle, twitter_id, True, False)
            
            # Complete follow task
            complete_task(wallet_address, 'follow_twitter', {
                'twitter_handle': twitter_handle,
                'twitter_id': twitter_id,
                'follower_count': follower_count,
                'follows_project': True,
                'verified_at': datetime.now().isoformat(),
                'method': 'twitter_api' if TWITTER_BEARER_TOKEN or (TWITTER_API_KEY and TWITTER_API_SECRET) else 'simulated'
            })
            
            return jsonify({
                'success': True, 
                'message': f'✅ Twitter follow verified! +50 points (Followers: {follower_count})',
                'verified': True,
                'follower_count': follower_count
            })
        else:
            return jsonify({
                'success': False, 
                'message': f'❌ Please follow @{TWITTER_USERNAME} on Twitter and try again.',
                'verified': False
            })
            
    except Exception as e:
        print(f"❌ Twitter Verification Error: {e}")
        return jsonify({'success': False, 'message': 'Twitter verification failed. Please try again.'})

@app.route('/verify-retweet', methods=['POST'])
def verify_retweet():
    try:
        data = request.get_json()
        wallet_address = data.get('wallet_address', '').strip()
        twitter_handle = data.get('twitter_handle', '').strip().replace('@', '')
        tweet_url = data.get('tweet_url', '')
        
        if not wallet_address or not twitter_handle or not tweet_url:
            return jsonify({'success': False, 'message': 'Wallet address, Twitter handle, and tweet URL are required'})
        
        # Extract tweet ID from URL
        tweet_id = None
        if 'status/' in tweet_url:
            tweet_id = tweet_url.split('status/')[-1].split('?')[0]
        
        if not tweet_id:
            return jsonify({'success': False, 'message': 'Invalid tweet URL'})
        
        print(f"🔁 Verifying retweet for: {twitter_handle}, Tweet: {tweet_id}")
        
        # Verify retweet
        retweeted = verify_twitter_retweet(twitter_handle, tweet_id)
        
        if retweeted:
            # Get Twitter user info
            user_info = get_twitter_user_info(twitter_handle)
            twitter_id = user_info['id'] if user_info else f"retweet_{twitter_handle}_id"
            
            # Save verification data
            save_twitter_verification(wallet_address, twitter_handle, twitter_id, True, True)
            
            # Complete retweet task
            complete_task(wallet_address, 'retweet', {
                'twitter_handle': twitter_handle,
                'tweet_url': tweet_url,
                'tweet_id': tweet_id,
                'retweeted': True,
                'verified_at': datetime.now().isoformat()
            })
            
            return jsonify({
                'success': True, 
                'message': '✅ Retweet verified successfully! +75 points',
                'verified': True
            })
        else:
            return jsonify({
                'success': False, 
                'message': '❌ Could not verify retweet. Please make sure you retweeted our pinned post.',
                'verified': False
            })
            
    except Exception as e:
        print(f"❌ Retweet Verification Error: {e}")
        return jsonify({'success': False, 'message': 'Retweet verification failed. Please try again.'})

@app.route('/twitter-status')
def twitter_status():
    """Check Twitter API status"""
    bearer_token = TWITTER_BEARER_TOKEN
    if not bearer_token and TWITTER_API_KEY and TWITTER_API_SECRET:
        bearer_token = get_twitter_bearer_token()
    
    status = {
        'twitter_configured': bool(TWITTER_BEARER_TOKEN or (TWITTER_API_KEY and TWITTER_API_SECRET)),
        'bearer_token_available': bool(bearer_token),
        'project_username': TWITTER_USERNAME,
        'api_ready': bool(bearer_token and TWITTER_USERNAME)
    }
    
    # Test API connection
    if status['api_ready']:
        try:
            headers = {'Authorization': f'Bearer {bearer_token}'}
            test_url = f'https://api.twitter.com/2/users/by/username/{TWITTER_USERNAME}'
            response = requests.get(test_url, headers=headers)
            status['api_test'] = response.status_code == 200
            status['api_test_message'] = '✅ Twitter API connected successfully!' if status['api_test'] else f'❌ API test failed: {response.status_code}'
        except Exception as e:
            status['api_test'] = False
            status['api_test_message'] = f'❌ API test error: {str(e)}'
    
    return jsonify(status)

@app.route('/test')
def test_route():
    status = {
        'server': '✅ Running',
        'database': '✅ Connected', 
        'twitter_api': '✅ Ready' if (TWITTER_BEARER_TOKEN or (TWITTER_API_KEY and TWITTER_API_SECRET)) else '❌ Not configured',
        'token_system': '✅ Active'
    }
    return jsonify(status)

if __name__ == '__main__':
    print("🚀 Starting Crypto Airdrop Server...")
    print("📍 Access your airdrop at: http://localhost:5000")
    print("📊 Dashboard: http://localhost:5000/dashboard") 
    print("💰 Token Dashboard: http://localhost:5000/token-dashboard")
    print("🐦 Twitter Status: http://localhost:5000/twitter-status")
    
    # Check Twitter configuration
    bearer_token = TWITTER_BEARER_TOKEN
    if not bearer_token and TWITTER_API_KEY and TWITTER_API_SECRET:
        bearer_token = get_twitter_bearer_token()
        print("🔑 Generated Twitter Bearer token from API keys")
    
    if bearer_token and TWITTER_USERNAME:
        print(f"✅ Twitter API: Configured for @{TWITTER_USERNAME}")
    else:
        print("❌ Twitter API: Not configured (using simulation mode)")
    
    print(f"✅ Token System: {TOKEN_CONFIG['token_symbol']} tokens ready for distribution")
    print(f"✅ Token Ratio: {TOKEN_CONFIG['points_to_tokens_ratio']} points = 1 token")
    
    app.run(debug=True, host='0.0.0.0', port=5000)

    # Production configuration
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)