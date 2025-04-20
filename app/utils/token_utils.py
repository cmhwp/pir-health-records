import jwt
from datetime import datetime, timedelta
from flask import current_app
import logging

logger = logging.getLogger(__name__)

def generate_download_token(user_id, filename, expires_in=300):
    """
    生成用于下载文件的JWT令牌
    
    参数:
    user_id: 用户ID
    filename: 文件名
    expires_in: 过期时间（秒），默认5分钟
    
    返回:
    JWT令牌字符串
    """
    try:
        # 设置过期时间
        exp = datetime.now() + timedelta(seconds=expires_in)
        
        # 创建payload
        payload = {
            'sub': user_id,
            'filename': filename,
            'exp': exp.timestamp(),
            'iat': datetime.now().timestamp(),
            'type': 'download_token'
        }
        
        # 使用应用程序的密钥签名
        token = jwt.encode(
            payload,
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )
        
        return token
    except Exception as e:
        logger.error(f"生成下载令牌失败: {str(e)}")
        return None

def validate_download_token(token, filename):
    """
    验证下载令牌
    
    参数:
    token: JWT令牌
    filename: 请求下载的文件名
    
    返回:
    如果令牌有效，返回用户ID；否则返回None
    """
    try:
        # 解码JWT
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=['HS256'],
            leeway=current_app.config.get('JWT_LEEWAY', 60)  # 允许时间偏差
        )
        
        # 检查令牌类型
        if payload.get('type') != 'download_token':
            logger.warning(f"无效的令牌类型: {payload.get('type')}")
            return None
        
        # 检查文件名
        if payload.get('filename') != filename:
            logger.warning(f"文件名不匹配: 期望 {payload.get('filename')}, 实际 {filename}")
            return None
        
        # 如果一切正常，返回用户ID
        return payload.get('sub')
    except jwt.ExpiredSignatureError:
        logger.warning(f"令牌已过期")
        return None
    except Exception as e:
        logger.error(f"验证下载令牌失败: {str(e)}")
        return None 