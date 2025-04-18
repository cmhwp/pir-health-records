from flask import current_app, g
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
import datetime
import json

mongo = PyMongo()

def get_mongo_db():
    """
    从Flask应用上下文中获取MongoDB连接。
    """
    if 'mongo_db' not in g:
        g.mongo_db = mongo.db
    return g.mongo_db

def init_mongo(app):
    """
    使用Flask应用初始化MongoDB。
    """
    mongo.init_app(app)
    
    # 创建索引
    with app.app_context():
        # 健康记录集合的索引
        mongo.db.health_records.create_index('patient_id')
        mongo.db.health_records.create_index('record_type')
        mongo.db.health_records.create_index('record_date')
        mongo.db.health_records.create_index('visibility')
        
        # 复合索引，用于按患者和日期范围查询
        mongo.db.health_records.create_index([
            ('patient_id', 1),
            ('record_date', -1)
        ])
        
        # 用于文本搜索的索引
        mongo.db.health_records.create_index([
            ('title', 'text'),
            ('description', 'text'),
            ('tags', 'text')
        ])
        
        # 查询历史的索引
        mongo.db.query_history.create_index('user_id')
        mongo.db.query_history.create_index('query_time')
        mongo.db.query_history.create_index('is_anonymous')
        
        # 复合索引，用于按用户和查询类型统计
        mongo.db.query_history.create_index([
            ('user_id', 1),
            ('query_type', 1)
        ])

class MongoJSONEncoder(json.JSONEncoder):
    """MongoDB数据的JSON编码器，处理特殊类型"""
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if hasattr(obj, '_id'):
            return str(obj._id)
        return super(MongoJSONEncoder, self).default(obj)

def format_mongo_doc(doc):
    """
    格式化MongoDB文档以便JSON响应（处理ObjectId和datetime对象）。
    递归处理嵌套的字典和列表。
    """
    if not doc:
        return doc
        
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == '_id' and isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, datetime.datetime):
                result[key] = value.isoformat()
            elif isinstance(value, datetime.date):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = format_mongo_doc(value)
            elif isinstance(value, list):
                result[key] = format_mongo_docs(value)
            else:
                result[key] = value
        return result
    elif isinstance(doc, ObjectId):
        return str(doc)
    elif isinstance(doc, datetime.datetime):
        return doc.isoformat()
    elif isinstance(doc, datetime.date):
        return doc.isoformat()
    else:
        return doc

def format_mongo_docs(docs):
    """
    格式化MongoDB文档列表以便JSON响应。
    可以处理文档列表或其他值的列表。
    """
    if not docs:
        return docs
        
    result = []
    for item in docs:
        result.append(format_mongo_doc(item))
    return result 