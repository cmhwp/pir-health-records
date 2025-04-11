import redis
from flask import current_app, g
from flask_caching import Cache

cache = Cache()

def get_redis():
    """
    从Flask应用上下文中获取Redis连接。
    """
    if 'redis' not in g:
        g.redis = redis.from_url(current_app.config['REDIS_URL'])
    return g.redis

def init_redis(app):
    """
    使用Flask应用初始化Redis。
    """
    cache.init_app(app)
    
def close_redis(e=None):
    """
    关闭Redis连接。
    """
    redis_client = g.pop('redis', None)
    if redis_client is not None:
        redis_client.connection_pool.disconnect()

def cache_key(*args, **kwargs):
    """
    从参数生成缓存键。
    """
    key_parts = []
    # 添加位置参数
    for arg in args:
        key_parts.append(str(arg))
    
    # 按排序顺序添加关键字参数
    for key in sorted(kwargs.keys()):
        key_parts.append(f"{key}={kwargs[key]}")
    
    return ":".join(key_parts) 