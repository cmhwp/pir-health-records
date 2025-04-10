import hashlib
import base64
import os
import json
import time
import uuid
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import request, jsonify, session, current_app

# 加密和哈希函数
def hash_password(password):
    """生成密码的安全哈希值"""
    return generate_password_hash(password)

def verify_password(hashed_password, password):
    """验证密码是否与哈希值匹配"""
    return check_password_hash(hashed_password, password)

def generate_token():
    """生成随机令牌"""
    return base64.b64encode(os.urandom(64)).decode('utf-8')

def encrypt_data(data, key=None):
    """使用AES加密数据"""
    if key is None:
        key = current_app.config.get('SECRET_KEY', 'default_key').encode('utf-8')
        key = hashlib.sha256(key).digest()
    
    # 如果数据不是字符串，转换为字符串
    if not isinstance(data, str):
        data = json.dumps(data)
    
    # 生成随机IV
    iv = get_random_bytes(16)
    
    # 创建密码对象并加密数据
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct_bytes = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
    
    # 将IV和密文编码为base64
    iv_b64 = base64.b64encode(iv).decode('utf-8')
    ct_b64 = base64.b64encode(ct_bytes).decode('utf-8')
    
    # 返回编码后的数据
    return f"{iv_b64}:{ct_b64}"

def decrypt_data(encrypted_data, key=None):
    """使用AES解密数据"""
    if key is None:
        key = current_app.config.get('SECRET_KEY', 'default_key').encode('utf-8')
        key = hashlib.sha256(key).digest()
    
    # 分离IV和密文
    iv_b64, ct_b64 = encrypted_data.split(':')
    iv = base64.b64decode(iv_b64)
    ct = base64.b64decode(ct_b64)
    
    # 创建密码对象并解密数据
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pt = unpad(cipher.decrypt(ct), AES.block_size)
    
    # 返回解密后的数据
    return pt.decode('utf-8')

def hash_content(content):
    """生成内容的哈希值用于完整性验证"""
    if not isinstance(content, str):
        content = json.dumps(content)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

# 认证装饰器
def login_required(f):
    """要求路由进行认证的装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'message': '需要认证'}), 401
        return f(*args, **kwargs)
    return decorated_function

def role_required(roles):
    """要求路由具有特定角色的装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from app.models.mysql import User
            
            if 'user_id' not in session:
                return jsonify({'message': '需要认证'}), 401
            
            user = User.query.get(session['user_id'])
            if not user:
                return jsonify({'message': '用户未找到'}), 404
            
            if user.role not in roles:
                return jsonify({'message': '权限不足'}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# 数据验证函数
def validate_health_record(data):
    """验证健康记录数据"""
    if not data:
        return False, "未提供数据"
    
    required_fields = ['patient_id', 'record_type', 'content']
    for field in required_fields:
        if field not in data:
            return False, f"缺少必填字段: {field}"
    
    if not isinstance(data['patient_id'], int):
        return False, "patient_id必须是整数"
    
    if not isinstance(data['record_type'], str):
        return False, "record_type必须是字符串"
    
    if not isinstance(data['content'], dict):
        return False, "content必须是字典"
    
    return True, "有效"

def validate_user_data(data, for_update=False):
    """验证用户数据用于创建或更新"""
    if not data:
        return False, "未提供数据"
    
    if not for_update:
        required_fields = ['username', 'password']
        for field in required_fields:
            if field not in data:
                return False, f"缺少必填字段: {field}"
        
        if not isinstance(data['username'], str) or len(data['username']) < 3:
            return False, "用户名必须是至少3个字符的字符串"
        
        if not isinstance(data['password'], str) or len(data['password']) < 8:
            return False, "密码必须是至少8个字符的字符串"
    
    if 'email' in data and data['email']:
        if not isinstance(data['email'], str) or '@' not in data['email']:
            return False, "邮箱必须是有效的电子邮件地址"
    
    if 'role' in data:
        valid_roles = ['patient', 'doctor', 'admin']
        if data['role'] not in valid_roles:
            return False, f"角色必须是以下之一: {', '.join(valid_roles)}"
    
    return True, "有效" 