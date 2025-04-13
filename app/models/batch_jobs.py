from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any, Union
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, ForeignKey, Enum as SQLAEnum
from sqlalchemy.orm import relationship

from . import db
from .user import User


class BatchStatus(str, Enum):
    """批量任务状态枚举"""
    PENDING = 'pending'           # 待处理
    PROCESSING = 'processing'     # 处理中
    COMPLETED = 'completed'       # 已完成
    FAILED = 'failed'             # 失败


class BatchType(str, Enum):
    """批量任务类型枚举"""
    PATIENT = 'patient'           # 患者记录
    MEDICATION = 'medication'     # 药物数据
    LAB = 'lab'                   # 实验室结果
    CUSTOM = 'custom'             # 自定义数据


class LogLevel(str, Enum):
    """日志级别枚举"""
    INFO = 'info'                 # 信息
    WARNING = 'warning'           # 警告
    ERROR = 'error'               # 错误
    SUCCESS = 'success'           # 成功


class BatchJob(db.Model):
    """批量任务模型"""
    __tablename__ = 'batch_jobs'
    
    id = Column(Integer, primary_key=True)
    job_id = Column(String(64), unique=True, nullable=False, index=True)  # 业务ID，如BATCH-001
    name = Column(String(255), nullable=False)
    type = Column(SQLAEnum(BatchType), nullable=False)
    status = Column(SQLAEnum(BatchStatus), nullable=False, default=BatchStatus.PENDING)
    progress = Column(Integer, nullable=False, default=0)  # 0-100
    
    file_name = Column(String(255))  # 原始文件名
    file_path = Column(String(512))  # 存储的文件路径
    file_size = Column(Integer)      # 文件大小(字节)
    file_type = Column(String(50))   # 文件类型(CSV, JSON, XML)
    
    records_count = Column(Integer)  # 总记录数
    processed_count = Column(Integer, default=0)  # 已处理记录数
    error_count = Column(Integer, default=0)      # 错误记录数
    
    options = Column(JSON)  # 处理选项，例如{"validateOnly": true, "skipDuplicates": true}
    
    # 创建者关系
    created_by_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_by = relationship('User', backref='batch_jobs')
    
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)  # 完成时间
    
    def __init__(self, name: str, job_type: Union[BatchType, str], created_by_id: int, 
                 file_name: Optional[str] = None, file_path: Optional[str] = None, 
                 file_size: Optional[int] = None, file_type: Optional[str] = None,
                 options: Optional[Dict[str, Any]] = None):
        """初始化批量任务"""
        # 生成唯一的任务ID
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        self.job_id = f"BATCH-{timestamp}"
        
        self.name = name
        # 支持传入枚举或字符串
        if isinstance(job_type, str):
            self.type = BatchType(job_type)
        else:
            self.type = job_type
            
        self.status = BatchStatus.PENDING
        self.progress = 0
        self.created_by_id = created_by_id
        
        # 文件信息
        self.file_name = file_name
        self.file_path = file_path
        self.file_size = file_size
        self.file_type = file_type
        
        # 处理选项
        self.options = options or {}

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典"""
        user = User.query.get(self.created_by_id)
        return {
            'id': self.job_id,
            'name': self.name,
            'type': self.type.value,
            'status': self.status.value,
            'progress': self.progress,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'records_count': self.records_count,
            'processed_count': self.processed_count,
            'error_count': self.error_count,
            'options': self.options,
            'createdBy': user.username if user else None,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None
        }

    def update_progress(self, progress: int, processed_count: Optional[int] = None, 
                        error_count: Optional[int] = None) -> None:
        """更新处理进度"""
        self.progress = min(100, max(0, progress))
        
        if processed_count is not None:
            self.processed_count = processed_count
            
        if error_count is not None:
            self.error_count = error_count
            
        self.updated_at = datetime.now()
        
        if self.progress >= 100:
            self.status = BatchStatus.COMPLETED
            self.completed_at = datetime.now()
            
        db.session.commit()

    def mark_failed(self, error_count: Optional[int] = None) -> None:
        """标记任务失败"""
        self.status = BatchStatus.FAILED
        
        if error_count is not None:
            self.error_count = error_count
            
        self.updated_at = datetime.now()
        db.session.commit()

    def mark_processing(self) -> None:
        """标记任务为处理中"""
        self.status = BatchStatus.PROCESSING
        self.updated_at = datetime.now()
        db.session.commit()


class BatchJobLog(db.Model):
    """批量任务日志模型"""
    __tablename__ = 'batch_job_logs'
    
    id = Column(Integer, primary_key=True)
    batch_job_id = Column(Integer, ForeignKey('batch_jobs.id'), nullable=False, index=True)
    batch_job = relationship('BatchJob', backref='logs')
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    level = Column(SQLAEnum(LogLevel), nullable=False, default=LogLevel.INFO)
    message = Column(Text, nullable=False)
    details = Column(JSON)  # 附加详细信息
    
    def __init__(self, batch_job_id: int, message: str, level: Union[LogLevel, str] = LogLevel.INFO,
                 details: Optional[Dict[str, Any]] = None):
        """初始化批量任务日志"""
        self.batch_job_id = batch_job_id
        self.message = message
        
        # 支持传入枚举或字符串
        if isinstance(level, str):
            self.level = LogLevel(level)
        else:
            self.level = level
            
        self.details = details
        
    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典"""
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'level': self.level.value,
            'message': self.message,
            'details': self.details
        }


class BatchJobError(db.Model):
    """批量任务错误模型"""
    __tablename__ = 'batch_job_errors'
    
    id = Column(Integer, primary_key=True)
    batch_job_id = Column(Integer, ForeignKey('batch_jobs.id'), nullable=False, index=True)
    batch_job = relationship('BatchJob', backref='errors')
    
    row = Column(Integer)  # 错误所在行号
    field = Column(String(255))  # 错误字段
    value = Column(Text)  # 错误值
    error = Column(Text, nullable=False)  # 错误消息
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    def __init__(self, batch_job_id: int, error: str, row: Optional[int] = None,
                 field: Optional[str] = None, value: Optional[str] = None):
        """初始化批量任务错误"""
        self.batch_job_id = batch_job_id
        self.error = error
        self.row = row
        self.field = field
        self.value = value
        
    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典"""
        return {
            'key': self.id,  # 前端表格需要key
            'row': self.row,
            'field': self.field,
            'value': self.value,
            'error': self.error,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


def add_batch_log(batch_job_id: int, message: str, level: Union[LogLevel, str] = LogLevel.INFO,
                  details: Optional[Dict[str, Any]] = None) -> BatchJobLog:
    """添加批量任务日志"""
    log = BatchJobLog(batch_job_id, message, level, details)
    db.session.add(log)
    db.session.commit()
    return log


def add_batch_error(batch_job_id: int, error: str, row: Optional[int] = None,
                    field: Optional[str] = None, value: Optional[str] = None) -> BatchJobError:
    """添加批量任务错误"""
    error_record = BatchJobError(batch_job_id, error, row, field, value)
    db.session.add(error_record)
    
    # 更新批量任务的错误计数
    batch_job = BatchJob.query.get(batch_job_id)
    if batch_job:
        batch_job.error_count = BatchJobError.query.filter_by(batch_job_id=batch_job_id).count() + 1
        db.session.commit()
        
    return error_record


def get_batch_job_by_job_id(job_id: str) -> Optional[BatchJob]:
    """根据业务ID获取批量任务"""
    return BatchJob.query.filter_by(job_id=job_id).first()


def get_batch_jobs_by_status(status: Union[BatchStatus, str]) -> List[BatchJob]:
    """根据状态获取批量任务列表"""
    if isinstance(status, str):
        status = BatchStatus(status)
    return BatchJob.query.filter_by(status=status).all() 