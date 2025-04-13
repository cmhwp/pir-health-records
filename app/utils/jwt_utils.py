from flask import request, current_app, g
from flask_login import login_user
from functools import wraps
import jwt
from ..models import User
from datetime import datetime

def init_jwt_loader(app):
    """
    初始化JWT认证拦截器
    """
    @app.before_request
    def load_user_from_jwt():
        """在每个请求前尝试从JWT令牌加载用户"""
        # 检查Authorization头
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return
        
        token = auth_header.split(' ')[1]
        try:
            # 解码JWT
            payload = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=['HS256']
            )
            
            # 检查令牌是否过期
            if 'exp' in payload and datetime.now().timestamp() > payload['exp']:
                return
            
            # 获取用户并登录
            user_id = payload.get('sub')
            user = User.query.get(user_id)
            if user:
                login_user(user)  # 使用Flask-Login登录用户
            
            # 将用户ID存储在g对象中，以便后续使用
            g.current_user_id = user_id
            g.current_user_role = payload.get('role')
        except Exception as e:
            current_app.logger.error(f"JWT解析错误: {str(e)}")

def jwt_required(fn):
    """
    JWT认证装饰器，可用于API endpoints
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # 检查Authorization头
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return {
                'success': False,
                'message': '未提供令牌'
            }, 401
        
        token = auth_header.split(' ')[1]
        try:
            # 解码JWT
            payload = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=['HS256']
            )
            
            # 检查令牌是否过期
            if 'exp' in payload and datetime.now().timestamp() > payload['exp']:
                return {
                    'success': False,
                    'message': '令牌已过期'
                }, 401
            
            # 检查用户是否存在
            user_id = payload.get('sub')
            user = User.query.get(user_id)
            if not user:
                return {
                    'success': False,
                    'message': '无效的用户'
                }, 401
            
            # 将用户存储在g对象中，以便后续使用
            g.current_user = user
            
            # 调用原始函数
            return fn(*args, **kwargs)
        except Exception as e:
            current_app.logger.error(f"JWT认证错误: {str(e)}")
            return {
                'success': False,
                'message': '认证失败'
            }, 401
    
    return wrapper 