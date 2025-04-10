from flask import Blueprint, request, jsonify, session
from app.models.mysql import db, User
from app.models.redis import redis_client
from app.utils.security import hash_password, verify_password, validate_user_data, login_required, role_required
import uuid
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # 验证用户数据
    is_valid, message = validate_user_data(data)
    if not is_valid:
        return jsonify({'message': message}), 400
    
    # 检查用户名是否已存在
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'message': '用户名已存在'}), 400
    
    # 检查邮箱是否已存在（如果提供）
    if data.get('email') and User.query.filter_by(email=data['email']).first():
        return jsonify({'message': '邮箱已存在'}), 400
    
    # 哈希密码
    hashed_password = hash_password(data['password'])
    
    # 创建新用户
    new_user = User(
        username=data['username'],
        password=hashed_password,
        role=data.get('role', 'patient'),
        email=data.get('email'),
        phone=data.get('phone'),
        created_at=datetime.now()
    )
    
    # 保存到数据库
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({
        'message': '用户注册成功',
        'user': {
            'id': new_user.id,
            'username': new_user.username,
            'role': new_user.role
        }
    }), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'message': '缺少凭据'}), 400
    
    # 通过用户名查找用户
    user = User.query.filter_by(username=data['username']).first()
    
    # 如果用户未找到或密码不匹配
    if not user or not verify_password(user.password, data['password']):
        return jsonify({'message': '无效的凭据'}), 401
    
    # 生成会话令牌
    session_token = str(uuid.uuid4())
    
    # 存储在会话和Redis中
    session['user_id'] = user.id
    session['session_token'] = session_token
    
    # 在Redis中缓存用户数据以便更快访问
    user_data = {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'email': user.email,
        'session_token': session_token
    }
    redis_client.cache_user_data(user.id, user_data)
    
    return jsonify({
        'message': '登录成功',
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role,
            'session_token': session_token
        }
    }), 200

@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    user_id = session.get('user_id')
    session_token = session.get('session_token')
    
    # 清除会话
    session.pop('user_id', None)
    session.pop('session_token', None)
    
    # 如果令牌存在，从Redis中移除
    if session_token:
        redis_client.delete_session(session_token)
    
    return jsonify({'message': '已成功登出'}), 200

@auth_bp.route('/me', methods=['GET'])
@login_required
def get_current_user():
    user_id = session.get('user_id')
    
    # 首先尝试从Redis缓存获取
    user_data = redis_client.get_cached_user_data(user_id)
    
    if not user_data:
        # 回退到数据库
        user = User.query.get(user_id)
        if not user:
            return jsonify({'message': '用户未找到'}), 404
        
        user_data = {
            'id': user.id,
            'username': user.username,
            'role': user.role,
            'email': user.email
        }
        # 更新缓存
        redis_client.cache_user_data(user_id, user_data)
    
    return jsonify({'user': user_data}), 200

@auth_bp.route('/users', methods=['GET'])
@login_required
@role_required(['admin'])
def get_users():
    # 只有管理员可以列出所有用户
    users = User.query.all()
    
    users_data = [{
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'email': user.email,
        'created_at': user.created_at.isoformat() if user.created_at else None
    } for user in users]
    
    return jsonify({'users': users_data}), 200

@auth_bp.route('/users/<int:user_id>', methods=['GET'])
@login_required
def get_user(user_id):
    # 用户可以查看自己的个人资料，医生可以查看其患者，管理员可以查看任何人
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 授权检查
    if current_user.role != 'admin' and current_user_id != user_id:
        if current_user.role == 'doctor':
            # 检查用户是否是该医生的患者
            # （在实际系统中，这个检查会更复杂）
            pass
        else:
            return jsonify({'message': '未授权访问'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': '用户未找到'}), 404
    
    user_data = {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'email': user.email,
        'phone': user.phone,
        'created_at': user.created_at.isoformat() if user.created_at else None
    }
    
    return jsonify({'user': user_data}), 200

@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    # 用户可以更新自己的个人资料，管理员可以更新任何人
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 授权检查
    if current_user.role != 'admin' and current_user_id != user_id:
        return jsonify({'message': '未授权访问'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': '用户未找到'}), 404
    
    data = request.get_json()
    
    # 验证更新数据
    is_valid, message = validate_user_data(data, for_update=True)
    if not is_valid:
        return jsonify({'message': message}), 400
    
    # 如果提供，更新字段
    if 'username' in data and data['username'] != user.username:
        # 检查新用户名是否已存在
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'message': '用户名已存在'}), 400
        user.username = data['username']
    
    if 'email' in data and data['email'] != user.email:
        # 检查新邮箱是否已存在
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'message': '邮箱已存在'}), 400
        user.email = data['email']
    
    if 'password' in data:
        user.password = hash_password(data['password'])
    
    if 'phone' in data:
        user.phone = data['phone']
    
    if 'role' in data and current_user.role == 'admin':
        user.role = data['role']
    
    # 保存更改
    db.session.commit()
    
    # 更新Redis缓存
    user_data = {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'email': user.email
    }
    redis_client.cache_user_data(user.id, user_data)
    
    return jsonify({
        'message': '用户更新成功',
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role,
            'email': user.email
        }
    }), 200 