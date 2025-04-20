from . import db
from datetime import datetime
import enum

class NotificationType(enum.Enum):
    MESSAGE = "message"                    # 消息通知
    SYSTEM = "system"                      # 系统通知
    RECORD = "record"                      # 健康记录通知
    SHARE = "share"                        # 记录共享通知
    PRESCRIPTION = "prescription"          # 处方通知
    PRESCRIPTION_REQUEST = "prescription_request"  # 处方申请通知

class Notification(db.Model):
    """通知模型"""
    __tablename__ = 'notifications'

    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 通知接收者
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # 发送者（如果适用）
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # 通知类型
    notification_type = db.Column(db.Enum(NotificationType), nullable=False)
    # 通知标题
    title = db.Column(db.String(100), nullable=False)
    # 通知内容
    message = db.Column(db.Text, nullable=False)
    # 相关记录/对象ID
    related_id = db.Column(db.String(100), nullable=True)
    # 是否已读
    is_read = db.Column(db.Boolean, default=False)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 过期时间
    expires_at = db.Column(db.DateTime, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'sender_id': self.sender_id,
            'notification_type': self.notification_type.value,
            'title': self.title,
            'message': self.message,
            'related_id': self.related_id,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }
    
    def is_valid(self):
        """检查通知是否有效（未过期）"""
        if not self.expires_at:
            return True
        return datetime.now() < self.expires_at
    
    def mark_as_read(self):
        """标记通知为已读"""
        self.is_read = True
        db.session.commit() 