from flask import current_app
from pymongo import MongoClient
import json
from datetime import datetime

class MongoConnector:
    def __init__(self):
        self.client = None
        self.db = None
    
    def init_app(self, app):
        """使用Flask应用配置初始化MongoDB连接"""
        mongodb_uri = app.config.get('MONGO_URI')
        db_name = app.config.get('MONGO_DB_NAME')
        
        self.client = MongoClient(mongodb_uri)
        self.db = self.client[db_name]
        
        # 如果不存在，创建必要的集合和索引
        if 'health_records' not in self.db.list_collection_names():
            self.db.create_collection('health_records')
            self.db.health_records.create_index('record_id', unique=True)
        
        if 'pir_indexes' not in self.db.list_collection_names():
            self.db.create_collection('pir_indexes')
            self.db.pir_indexes.create_index('keyword')
    
    def store_health_record(self, record_id, content):
        """在MongoDB中存储加密的健康记录内容"""
        record_doc = {
            'record_id': record_id,
            'content': content,
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = self.db.health_records.insert_one(record_doc)
        return str(result.inserted_id)
    
    def get_health_record(self, record_id):
        """通过ID检索健康记录"""
        record = self.db.health_records.find_one({'record_id': record_id})
        return record
    
    def update_health_record(self, record_id, content):
        """更新现有的健康记录"""
        result = self.db.health_records.update_one(
            {'record_id': record_id},
            {
                '$set': {
                    'content': content,
                    'updated_at': datetime.now()
                }
            }
        )
        return result.modified_count > 0
    
    def add_to_pir_index(self, keyword, record_id, encrypted_location=None):
        """通过关键词将记录添加到PIR索引"""
        # Upsert - 如果关键词存在，将record_id添加到列表中，否则创建新文档
        result = self.db.pir_indexes.update_one(
            {'keyword': keyword},
            {
                '$addToSet': {'record_ids': record_id},
                '$set': {'updated_at': datetime.now()},
                '$setOnInsert': {
                    'created_at': datetime.now(),
                    'encrypted_location': encrypted_location
                }
            },
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None
    
    def search_pir_index(self, keyword):
        """搜索PIR索引中的关键词（这不是私密查询，仅用于测试）"""
        index_doc = self.db.pir_indexes.find_one({'keyword': keyword})
        if index_doc:
            return index_doc.get('record_ids', [])
        return []

# 创建单例实例
mongo_client = MongoConnector() 