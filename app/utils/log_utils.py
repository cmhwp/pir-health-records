"""
日志工具模块，包含日志类型定义和日志记录辅助函数
"""
from enum import Enum
from datetime import datetime
import json
from flask import request, current_app
from flask_login import current_user
from ..models import db
from ..models.log import SystemLog

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
    
    def __str__(self):
        return self.value

def log_activity(log_type, message, details=None, user_id=None, ip_address=None, user_agent=None):
    """
    记录系统活动的通用函数
    
    参数:
        log_type (LogType): 日志类型
        message (str): 日志消息
        details (dict): 详细信息，将被转换为JSON
        user_id (int): 用户ID，如果为None则使用当前登录用户
        ip_address (str): IP地址，如果为None则从请求中获取
        user_agent (str): 用户代理信息，如果为None则从请求中获取
    
    返回:
        SystemLog: 创建的日志对象
    """
    try:
        # 获取当前用户ID（如果未提供）
        if user_id is None and current_user and current_user.is_authenticated:
            user_id = current_user.id
            
        # 获取IP地址（如果未提供）
        if ip_address is None and request:
            ip_address = request.remote_addr
            
        # 获取用户代理（如果未提供）
        if user_agent is None and request and request.user_agent:
            user_agent = request.user_agent.string
        
        # 转换详细信息为JSON
        json_details = None
        if details:
            # 添加通用信息
            if isinstance(details, dict):
                if 'timestamp' not in details:
                    details['timestamp'] = datetime.now().isoformat()
                if ip_address and 'ip_address' not in details:
                    details['ip_address'] = ip_address
                if user_id and 'user_id' not in details:
                    details['user_id'] = user_id
                    
            json_details = json.dumps(details)
        
        # 创建日志记录
        log = SystemLog(
            user_id=user_id,
            log_type=log_type,
            message=message,
            details=json_details,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        db.session.add(log)
        db.session.commit()
        return log
    
    except Exception as e:
        current_app.logger.error(f"记录日志失败: {str(e)}")
        db.session.rollback()
        return None

def log_error(error_message, exception=None, details=None, user_id=None):
    """
    记录错误日志
    
    参数:
        error_message (str): 错误消息
        exception (Exception): 异常对象
        details (dict): 额外的详细信息
        user_id (int): 用户ID
    """
    error_details = details or {}
    
    if exception:
        error_details['exception'] = str(exception)
        error_details['exception_type'] = exception.__class__.__name__
    
    return log_activity(
        log_type=LogType.ERROR,
        message=error_message,
        details=error_details,
        user_id=user_id
    )

def log_security(message, details=None, user_id=None):
    """记录安全相关日志"""
    return log_activity(
        log_type=LogType.SECURITY,
        message=message,
        details=details,
        user_id=user_id
    )

def log_user(message, details=None, user_id=None):
    """记录用户操作日志"""
    return log_activity(
        log_type=LogType.USER,
        message=message,
        details=details,
        user_id=user_id
    )

def log_record(message, details=None, user_id=None):
    """记录健康记录操作日志"""
    return log_activity(
        log_type=LogType.RECORD,
        message=message,
        details=details,
        user_id=user_id
    )

def log_admin(message, details=None, user_id=None):
    """记录管理员操作日志"""
    return log_activity(
        log_type=LogType.ADMIN,
        message=message,
        details=details,
        user_id=user_id
    )

def log_pir(message, details=None, user_id=None):
    """记录PIR相关日志"""
    return log_activity(
        log_type=LogType.PIR,
        message=message,
        details=details,
        user_id=user_id
    )

def log_access(message, details=None, user_id=None):
    """记录访问控制日志"""
    return log_activity(
        log_type=LogType.ACCESS,
        message=message,
        details=details,
        user_id=user_id
    )

def log_export(message, details=None, user_id=None):
    """记录数据导出日志"""
    return log_activity(
        log_type=LogType.EXPORT,
        message=message,
        details=details,
        user_id=user_id
    )

def log_import(message, details=None, user_id=None):
    """记录数据导入日志"""
    return log_activity(
        log_type=LogType.IMPORT,
        message=message,
        details=details,
        user_id=user_id
    )

def log_audit(message, details=None, user_id=None):
    """记录审计日志"""
    return log_activity(
        log_type=LogType.AUDIT,
        message=message,
        details=details,
        user_id=user_id
    ) 