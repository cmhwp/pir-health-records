from . import db
from datetime import datetime
import enum

class NotificationType(enum.Enum):
    SYSTEM = "system"              # 系统通知
    RECORD_SHARED = "record_shared"  # 记录共享通知
    RECORD_ACCESS = "record_access"  # 记录被访问通知
    HEALTH_ALERT = "health_alert"    # 健康提醒/警报
    MESSAGE = "message"            # 一般消息

class Notification(db.Model):
    """通知模型"""
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 通知接收者
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 发送者（如果适用）
    notification_type = db.Column(db.Enum(NotificationType), nullable=False)  # 通知类型
    title = db.Column(db.String(100), nullable=False)  # 通知标题
    message = db.Column(db.Text, nullable=False)  # 通知内容
    related_id = db.Column(db.String(100), nullable=True)  # 相关记录/对象ID
    is_read = db.Column(db.Boolean, default=False)  # 是否已读
    created_at = db.Column(db.DateTime, default=datetime.now)  # 创建时间
    expires_at = db.Column(db.DateTime, nullable=True)  # 过期时间
    
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