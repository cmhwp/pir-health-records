import redis
from flask import current_app
import json
import pickle

class RedisConnector:
    def __init__(self):
        self.client = None
    
    def init_app(self, app):
        """使用Flask应用配置初始化Redis连接"""
        self.client = redis.Redis(
            host=app.config.get('REDIS_HOST', 'localhost'),
            port=app.config.get('REDIS_PORT', 6379),
            db=app.config.get('REDIS_DB', 0),
            password=app.config.get('REDIS_PASSWORD')
        )
    
    def store_pir_query_state(self, user_id, query_id, state_data, expiry=3600):
        """将PIR查询状态存储在Redis中，带有过期时间"""
        key = f"pir:query:{user_id}:{query_id}"
        state_serialized = pickle.dumps(state_data)
        self.client.set(key, state_serialized, ex=expiry)
    
    def get_pir_query_state(self, user_id, query_id):
        """从Redis检索PIR查询状态"""
        key = f"pir:query:{user_id}:{query_id}"
        state_serialized = self.client.get(key)
        if state_serialized:
            return pickle.loads(state_serialized)
        return None
    
    def delete_pir_query_state(self, user_id, query_id):
        """从Redis删除PIR查询状态"""
        key = f"pir:query:{user_id}:{query_id}"
        self.client.delete(key)
    
    def cache_user_data(self, user_id, data, expiry=1800):
        """在Redis中缓存用户数据"""
        key = f"user:{user_id}:data"
        self.client.set(key, json.dumps(data), ex=expiry)
    
    def get_cached_user_data(self, user_id):
        """从Redis获取缓存的用户数据"""
        key = f"user:{user_id}:data"
        data = self.client.get(key)
        if data:
            return json.loads(data)
        return None
    
    def store_session_data(self, session_id, data, expiry=86400):
        """在Redis中存储会话数据"""
        key = f"session:{session_id}"
        self.client.set(key, json.dumps(data), ex=expiry)
    
    def get_session_data(self, session_id):
        """从Redis获取会话数据"""
        key = f"session:{session_id}"
        data = self.client.get(key)
        if data:
            return json.loads(data)
        return None
    
    def delete_session(self, session_id):
        """从Redis删除会话数据"""
        key = f"session:{session_id}"
        self.client.delete(key)
    
    def rate_limit(self, ip_address, limit=100, period=60):
        """为IP地址实现速率限制"""
        key = f"ratelimit:{ip_address}"
        current = self.client.get(key)
        
        if current is None:
            self.client.set(key, 1, ex=period)
            return True
        
        if int(current) < limit:
            self.client.incr(key)
            return True
        
        return False

# 创建单例实例
redis_client = RedisConnector() 