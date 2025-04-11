from flask import current_app, g
from flask_pymongo import PyMongo
from bson.objectid import ObjectId

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

def format_mongo_doc(doc):
    """
    格式化MongoDB文档以便JSON响应（将ObjectId转换为字符串）。
    """
    if doc and '_id' in doc and isinstance(doc['_id'], ObjectId):
        doc['_id'] = str(doc['_id'])
    return doc

def format_mongo_docs(docs):
    """
    格式化MongoDB文档列表以便JSON响应。
    """
    return [format_mongo_doc(doc) for doc in docs] 