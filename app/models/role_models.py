from . import db
from datetime import datetime

class PatientInfo(db.Model):
    """患者信息模型"""
    __tablename__ = 'patient_info'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    address = db.Column(db.String(200), nullable=True)
    emergency_contact = db.Column(db.String(100), nullable=True)
    emergency_phone = db.Column(db.String(20), nullable=True)
    medical_history = db.Column(db.Text, nullable=True)
    allergies = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    specialty = db.Column(db.String(100), nullable=True)  # 专业
    license_number = db.Column(db.String(50), nullable=True)  # 执照号码
    years_of_experience = db.Column(db.Integer, nullable=True)  # 工作年限
    education = db.Column(db.String(200), nullable=True)  # 教育背景
    hospital = db.Column(db.String(100), nullable=True)  # 所属医院
    department = db.Column(db.String(100), nullable=True)  # 部门
    bio = db.Column(db.Text, nullable=True)  # 简介
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    institution = db.Column(db.String(100), nullable=True)  # 所属机构
    department = db.Column(db.String(100), nullable=True)  # 部门
    research_area = db.Column(db.String(200), nullable=True)  # 研究领域
    education = db.Column(db.String(200), nullable=True)  # 教育背景
    publications = db.Column(db.Text, nullable=True)  # 发表文章
    projects = db.Column(db.Text, nullable=True)  # 参与项目
    bio = db.Column(db.Text, nullable=True)  # 简介
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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