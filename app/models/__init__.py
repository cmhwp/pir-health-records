from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录才能访问此页面'

# 在此导入模型，使其在导入db时可用
from .user import User, Role
from .role_models import PatientInfo, DoctorInfo, ResearcherInfo
from .health_records import (
    RecordType, RecordVisibility, HealthRecord, 
    RecordFile, MedicationRecord, VitalSign, QueryHistory,
    SharePermission, SharedRecord
)
from .institution import Institution, CustomRecordType
from .notification import Notification, NotificationType
from .system_settings import SystemSetting
from .log import SystemLog, LogType
from .cache_item import CacheItem
from .prescription import Prescription, PrescriptionStatus, PrescriptionItem
from .researcher import ResearchProject, ProjectTeamMember, ProjectStatus
from .export import ExportTask, ExportStatus

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id)) 