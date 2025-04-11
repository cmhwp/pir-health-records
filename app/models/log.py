from datetime import datetime
from bson.objectid import ObjectId

class Log:
    """
    MongoDB的日志模型。
    这是一个无模式的MongoDB模型，不是SQLAlchemy模型。
    """
    
    @staticmethod
    def create_log(mongo_db, action, data, user_id=None):
        """
        在MongoDB中创建新的日志条目。
        
        参数:
            mongo_db: MongoDB连接
            action: 执行的操作
            data: 与操作相关的数据
            user_id: 执行操作的用户ID
            
        返回:
            ObjectId: 创建的日志ID
        """
        log_data = {
            'action': action,
            'data': data,
            'user_id': user_id,
            'timestamp': datetime.utcnow()
        }
        result = mongo_db.logs.insert_one(log_data)
        return result.inserted_id
    
    @staticmethod
    def get_logs(mongo_db, limit=100, skip=0, user_id=None, action=None):
        """
        从MongoDB获取日志，可选择进行过滤。
        
        参数:
            mongo_db: MongoDB连接
            limit: 返回的最大日志数量
            skip: 要跳过的日志数量（用于分页）
            user_id: 按用户ID过滤日志
            action: 按操作过滤日志
            
        返回:
            list: 日志文档列表
        """
        query = {}
        if user_id:
            query['user_id'] = user_id
        if action:
            query['action'] = action
            
        logs = list(mongo_db.logs.find(query).sort('timestamp', -1).skip(skip).limit(limit))
        return logs 