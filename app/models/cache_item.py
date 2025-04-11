import json
from datetime import datetime, timedelta
from ..utils.redis_utils import get_redis, cache_key

class CacheItem:
    """
    用于管理Redis中缓存项的辅助类。
    """
    
    @staticmethod
    def set(key, value, expire=None):
        """
        在缓存中设置值。
        
        参数:
            key: 缓存键
            value: 要缓存的值（将被JSON序列化）
            expire: 过期时间（秒）
            
        返回:
            bool: 成功状态
        """
        redis_client = get_redis()
        serialized = json.dumps(value)
        if expire:
            return redis_client.setex(key, expire, serialized)
        else:
            return redis_client.set(key, serialized)
    
    @staticmethod
    def get(key):
        """
        从缓存中获取值。
        
        参数:
            key: 缓存键
            
        返回:
            缓存的值，如果未找到则为None
        """
        redis_client = get_redis()
        value = redis_client.get(key)
        if value:
            return json.loads(value)
        return None
    
    @staticmethod
    def delete(key):
        """
        从缓存中删除键。
        
        参数:
            key: 缓存键
            
        返回:
            int: 删除的键数量
        """
        redis_client = get_redis()
        return redis_client.delete(key)
    
    @staticmethod
    def ttl(key):
        """
        获取键的生存时间。
        
        参数:
            key: 缓存键
            
        返回:
            int: 生存时间（秒），-1表示无过期时间，-2表示键不存在
        """
        redis_client = get_redis()
        return redis_client.ttl(key) 