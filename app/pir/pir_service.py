import numpy as np
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
import hashlib
import base64
import json
import uuid
from flask import current_app
from app.models.mongo import mongo_client
from app.models.redis import redis_client
from datetime import datetime

class PrivateInformationRetrieval:
    """
    健康记录的隐私信息检索(PIR)系统实现。
    该实现使用以下组合：
    1. 基于索引的PIR用于关键词搜索
    2. 计算型PIR协议，无需透露访问哪条记录即可检索
    """
    
    def __init__(self):
        """使用默认参数初始化PIR系统"""
        self.encryption_key = None
        self.database_dimension = (0, 0)  # 随着记录添加会更新
        self.initialized = False
    
    def initialize(self, secret_key=None):
        """使用加密密钥初始化PIR系统"""
        if secret_key is None:
            # 如果未提供密钥，则生成随机密钥
            secret_key = get_random_bytes(32)
        
        # 从密钥派生加密密钥
        self.encryption_key = hashlib.sha256(secret_key).digest()
        self.initialized = True
    
    def _encrypt_data(self, data):
        """使用AES加密数据"""
        if not self.initialized:
            self.initialize()
        
        # 如果数据不是字符串，转换为字符串
        if not isinstance(data, str):
            data = json.dumps(data)
        
        # 生成随机IV
        iv = get_random_bytes(16)
        
        # 创建密码对象并加密数据
        cipher = AES.new(self.encryption_key, AES.MODE_CBC, iv)
        ct_bytes = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
        
        # 将IV和密文编码为base64
        iv_b64 = base64.b64encode(iv).decode('utf-8')
        ct_b64 = base64.b64encode(ct_bytes).decode('utf-8')
        
        # 返回编码后的数据
        return f"{iv_b64}:{ct_b64}"
    
    def _decrypt_data(self, encrypted_data):
        """使用AES解密数据"""
        if not self.initialized:
            raise ValueError("PIR系统未初始化")
        
        # 分离IV和密文
        iv_b64, ct_b64 = encrypted_data.split(':')
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ct_b64)
        
        # 创建密码对象并解密数据
        cipher = AES.new(self.encryption_key, AES.MODE_CBC, iv)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        
        # 返回解密后的数据
        return pt.decode('utf-8')
    
    def _extract_keywords(self, content):
        """从健康记录内容中提取关键词用于索引"""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                # 如果不是有效的JSON，则视为纯文本
                pass
        
        # 如果内容是字典，提取值
        if isinstance(content, dict):
            keywords = []
            for key, value in content.items():
                # 递归从嵌套结构中提取
                if isinstance(value, (dict, list)):
                    sub_keywords = self._extract_keywords(value)
                    keywords.extend(sub_keywords)
                elif isinstance(value, str):
                    # 将字符串值分割为单词
                    words = value.lower().split()
                    keywords.extend(words)
            return keywords
        
        # 如果内容是列表，处理每个项目
        elif isinstance(content, list):
            keywords = []
            for item in content:
                sub_keywords = self._extract_keywords(item)
                keywords.extend(sub_keywords)
            return keywords
        
        # 对于纯文本内容，分割为单词
        elif isinstance(content, str):
            return content.lower().split()
        
        return []
    
    def add_record(self, record_id, content):
        """将健康记录添加到PIR系统"""
        if not self.initialized:
            self.initialize()
        
        # 从内容中提取关键词
        keywords = self._extract_keywords(content)
        
        # 加密内容
        encrypted_content = self._encrypt_data(content)
        
        # 将加密内容存储在MongoDB中
        mongo_id = mongo_client.store_health_record(record_id, encrypted_content)
        
        # 将关键词添加到PIR索引
        for keyword in set(keywords):  # 使用集合删除重复项
            hash_keyword = hashlib.sha256(keyword.encode()).hexdigest()
            mongo_client.add_to_pir_index(hash_keyword, record_id)
        
        return mongo_id
    
    def query(self, query_params):
        """
        根据提供的参数执行PIR查询
        query_params应该是一个包含以下内容的字典：
        - 'keywords': 要搜索的关键词列表
        - 'query_type': 'keyword'（按关键词搜索）或'record_id'（检索特定记录）
        """
        if not self.initialized:
            self.initialize()
        
        query_type = query_params.get('query_type', 'keyword')
        user_id = query_params.get('user_id')
        
        # 为此会话生成查询ID
        query_id = str(uuid.uuid4())
        
        if query_type == 'keyword':
            # 关键词搜索
            keywords = query_params.get('keywords', [])
            if not keywords:
                return {'error': '未提供关键词'}
            
            # 私密处理每个关键词
            record_ids = set()
            for keyword in keywords:
                hash_keyword = hashlib.sha256(keyword.encode()).hexdigest()
                
                # 这里是实际PIR协议应该发生的地方
                # 不是直接暴露我们在寻找哪个关键词，
                # 而是使用PIR协议私密地检索信息
                
                # 对于此实现，我们通过执行直接查找来模拟PIR
                # 在真实实现中，这将被适当的PIR协议替代
                matching_ids = mongo_client.search_pir_index(hash_keyword)
                record_ids.update(matching_ids)
            
            # 将查询状态存储在Redis中以便后续检索
            if user_id:
                query_state = {
                    'query_id': query_id,
                    'record_ids': list(record_ids),
                    'status': 'completed',
                    'timestamp': str(datetime.now())
                }
                redis_client.store_pir_query_state(user_id, query_id, query_state)
            
            # 返回记录ID，不透露哪个关键词匹配了哪条记录
            return {
                'query_id': query_id,
                'record_count': len(record_ids),
                'record_ids': list(record_ids)
            }
        
        elif query_type == 'record_id':
            # 直接记录检索
            record_id = query_params.get('record_id')
            if not record_id:
                return {'error': '未提供记录ID'}
            
            # 从MongoDB检索记录
            record = mongo_client.get_health_record(record_id)
            if not record:
                return {'error': '记录未找到'}
            
            # 解密内容
            try:
                encrypted_content = record.get('content')
                decrypted_content = self._decrypt_data(encrypted_content)
                content = json.loads(decrypted_content)
                
                # 存储查询状态
                if user_id:
                    query_state = {
                        'query_id': query_id,
                        'record_id': record_id,
                        'status': 'completed',
                        'timestamp': str(datetime.now())
                    }
                    redis_client.store_pir_query_state(user_id, query_id, query_state)
                
                return {
                    'query_id': query_id,
                    'record_id': record_id,
                    'content': content
                }
            except Exception as e:
                return {'error': f'解密记录失败: {str(e)}'}
        
        else:
            return {'error': '不支持的查询类型'}
    
    def get_query_status(self, user_id, query_id):
        """获取先前执行的查询的状态"""
        query_state = redis_client.get_pir_query_state(user_id, query_id)
        if query_state:
            return {
                'query_id': query_id,
                'status': query_state.get('status', 'unknown'),
                'timestamp': query_state.get('timestamp')
            }
        return {'error': '查询未找到'}

# 创建单例实例
pir_system = PrivateInformationRetrieval() 