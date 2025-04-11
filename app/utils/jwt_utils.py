from flask import request, current_app, g
from flask_login import login_user
from functools import wraps
import jwt
from ..models import User

def init_jwt_loader(app):
    @app.before_request
    def load_user_from_token():
        # 从Authorization头获取token
        auth_header = request.headers.get('Authorization')
        if auth_header:
            try:
                # 提取token
                token_type, token = auth_header.split(' ', 1)
                if token_type.lower() != 'bearer':
                    return
                
                # 解码token
                payload = jwt.decode(
                    token, 
                    current_app.config['SECRET_KEY'],
                    algorithms=['HS256']
                )
                
                # 获取用户
                user_id = payload['sub']
                user = User.query.get(user_id)
                
                if user and user.is_active:
                    # 如果用户有效，使用Flask-Login登录
                    login_user(user)
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError):
                # Token无效或过期，继续请求，由视图函数中的装饰器处理认证失败
                pass
            except Exception as e:
                current_app.logger.error(f"JWT验证错误: {str(e)}") 