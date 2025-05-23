from datetime import datetime
from bson.objectid import ObjectId
from enum import Enum
from . import db

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
            'timestamp': datetime.now()
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

class LogType(Enum):
    """系统日志类型枚举"""
    SYSTEM = 'system'              # 系统级别日志（启动、关闭、配置变更等）
    SECURITY = 'security'          # 安全相关日志（登录、登出、密码修改等）
    USER = 'user'                  # 用户操作日志（注册、个人资料更新等）
    RECORD = 'record'              # 健康记录操作日志（创建、修改、删除记录等）
    ADMIN = 'admin'                # 管理员操作日志（用户管理、系统设置等）
    ERROR = 'error'                # 错误日志（系统错误、异常情况等）
    PIR = 'pir'                    # PIR相关日志（隐私信息检索操作）
    ACCESS = 'access'              # 访问控制日志（查看记录、文件下载等）
    EXPORT = 'export'              # 数据导出日志（导出、下载等）
    IMPORT = 'import'              # 数据导入日志（导入、上传等）
    AUDIT = 'audit'                # 审计日志（敏感操作、合规检查等）
    RESEARCH = 'research'          # 研究相关日志（研究员查询、导出等）
    
    def __str__(self):
        return self.value

class SystemLog(db.Model):
    """系统日志模型 (SQL版本)"""
    __tablename__ = 'system_logs'
    
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 日志类型
    log_type = db.Column(db.Enum(LogType), nullable=False, index=True)
    # 消息
    message = db.Column(db.String(255), nullable=False)
    # 详情
    details = db.Column(db.Text)
    
    # 关联用户(可选)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # IP地址和用户代理
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(255))
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    
    def __repr__(self):
        return f'<SystemLog {self.id}: {self.log_type}>'
    
    def to_dict(self):
        """转换为字典表示"""
        try:
            import json
            details_dict = json.loads(self.details) if self.details else {}
        except:
            details_dict = {'raw': self.details}
            
        return {
            'id': self.id,
            'log_type': str(self.log_type),
            'message': self.message,
            'details': details_dict,
            'user_id': self.user_id,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'created_at': self.created_at.isoformat() if self.created_at else None
        } 