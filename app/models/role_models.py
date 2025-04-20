from . import db
from datetime import datetime

class PatientInfo(db.Model):
    """患者信息模型"""
    __tablename__ = 'patient_info'
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 用户ID
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    # 出生日期
    date_of_birth = db.Column(db.Date, nullable=True)
    # 性别
    gender = db.Column(db.String(10), nullable=True)
    # 地址
    address = db.Column(db.String(200), nullable=True)
    # 紧急联系人
    emergency_contact = db.Column(db.String(100), nullable=True)
    # 紧急联系电话
    emergency_phone = db.Column(db.String(20), nullable=True)
    # 病史
    medical_history = db.Column(db.Text, nullable=True)
    # 过敏史
    allergies = db.Column(db.Text, nullable=True)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 更新时间
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'date_of_birth': self.date_of_birth.isoformat() if self.date_of_birth else None,
            'gender': self.gender,
            'address': self.address,
            'emergency_contact': self.emergency_contact,
            'emergency_phone': self.emergency_phone,
            'medical_history': self.medical_history,
            'allergies': self.allergies
        }


class DoctorInfo(db.Model):
    """医生信息模型"""
    __tablename__ = 'doctor_info'
    
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 用户ID
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    # 专业
    specialty = db.Column(db.String(100), nullable=True)
    # 执照号码
    license_number = db.Column(db.String(50), nullable=True)
    # 工作年限
    years_of_experience = db.Column(db.Integer, nullable=True)
    # 教育背景  
    education = db.Column(db.String(200), nullable=True)
    # 所属医院
    hospital = db.Column(db.String(100), nullable=True)
    # 部门
    department = db.Column(db.String(100), nullable=True)
    # 简介
    bio = db.Column(db.Text, nullable=True)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 更新时间
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'specialty': self.specialty,
            'license_number': self.license_number,
            'years_of_experience': self.years_of_experience,
            'education': self.education,
            'hospital': self.hospital,
            'department': self.department,
            'bio': self.bio
        }


class ResearcherInfo(db.Model):
    """研究人员信息模型"""
    __tablename__ = 'researcher_info'
    
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 用户ID
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    # 所属机构
    institution = db.Column(db.String(100), nullable=True)
    # 部门
    department = db.Column(db.String(100), nullable=True)
    # 研究领域
    research_area = db.Column(db.String(200), nullable=True)
    # 教育背景
    education = db.Column(db.String(200), nullable=True)
    # 发表文章
    publications = db.Column(db.Text, nullable=True)
    # 参与项目
    projects = db.Column(db.Text, nullable=True)
    # 简介
    bio = db.Column(db.Text, nullable=True)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 更新时间
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'institution': self.institution,
            'department': self.department,
            'research_area': self.research_area,
            'education': self.education,
            'publications': self.publications,
            'projects': self.projects,
            'bio': self.bio
        } 