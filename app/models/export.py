from datetime import datetime
from enum import Enum
from . import db
from sqlalchemy.dialects.postgresql import JSON

class ExportStatus(Enum):
    """导出任务状态枚举"""
    PENDING = 'pending'        # 等待处理
    PROCESSING = 'processing'  # 处理中
    COMPLETED = 'completed'    # 已完成
    FAILED = 'failed'          # 失败
    
    def __str__(self):
        return self.value

class ExportTask(db.Model):
    """数据导出任务模型"""
    __tablename__ = 'export_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    export_id = db.Column(db.String(36), unique=True, index=True)  # 唯一标识符
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 导出用户
    export_type = db.Column(db.String(50), nullable=False)  # 导出类型
    format = db.Column(db.String(10), nullable=False, default='json')  # 导出格式
    status = db.Column(db.Enum(ExportStatus), default=ExportStatus.PENDING)  # 状态
    
    # 导出文件信息
    filename = db.Column(db.String(255))  # 导出文件名
    file_path = db.Column(db.String(255))  # 文件路径
    file_size = db.Column(db.Integer)  # 文件大小(bytes)
    record_count = db.Column(db.Integer)  # 记录数量
    
    # 导出选项
    options = db.Column(JSON, default={})  # 导出选项
    parameters = db.Column(JSON, default={})  # 请求参数
    
    # 时间信息
    created_at = db.Column(db.DateTime, default=datetime.now)  # 创建时间
    started_at = db.Column(db.DateTime)  # 开始处理时间
    completed_at = db.Column(db.DateTime)  # 完成时间
    
    # 错误信息
    error_message = db.Column(db.Text)  # 错误消息
    
    # 备注
    notes = db.Column(db.Text)  # 额外说明
    
    def __repr__(self):
        return f'<ExportTask {self.export_id}: {self.export_type} ({self.status})>'
    
    def to_dict(self):
        """转换为字典表示"""
        return {
            'id': self.export_id,  # 使用export_id作为前端显示的ID
            'dataType': self.export_type,
            'format': self.format,
            'status': str(self.status),
            'filename': self.filename,
            'fileSize': self.file_size,
            'recordCount': self.record_count,
            'options': self.options,
            'parameters': self.parameters,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'startedAt': self.started_at.isoformat() if self.started_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None,
            'exportedBy': None,  # 将由API填充用户信息
            'notes': self.notes,
            'errorMessage': self.error_message
        } 