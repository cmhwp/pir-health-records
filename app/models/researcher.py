from . import db
from datetime import datetime
from sqlalchemy.orm import relationship
from enum import Enum

class ProjectStatus(Enum):
    PLANNING = "计划中"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    PAUSED = "已暂停"   
    CANCELLED = "已取消"


class ResearchProject(db.Model):
    """研究项目模型"""
    __tablename__ = 'research_projects'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)  # 项目标题
    description = db.Column(db.Text, nullable=True)  # 项目描述
    status = db.Column(db.Enum(ProjectStatus), default=ProjectStatus.PLANNING)  # 项目状态
    start_date = db.Column(db.Date, nullable=False)  # 开始日期
    end_date = db.Column(db.Date, nullable=False)  # 结束日期
    participants = db.Column(db.Integer, default=0)  # 参与人数
    
    # 外键关联
    researcher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 创建者ID
    
    # 关系
    team_members = relationship("ProjectTeamMember", back_populates="project", cascade="all, delete-orphan")
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status.value if self.status else None,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'participants': self.participants,
            'researcher_id': self.researcher_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'team_members': [member.to_dict() for member in self.team_members] if self.team_members else []
        }
    
    @classmethod
    def get_projects_by_researcher(cls, researcher_id):
        """获取研究者的所有项目"""
        return cls.query.filter_by(researcher_id=researcher_id).order_by(cls.created_at.desc()).all()
    
    @classmethod
    def get_project_by_id(cls, project_id, researcher_id=None):
        """通过ID获取项目，可选择指定研究者ID进行权限验证"""
        if researcher_id:
            return cls.query.filter_by(id=project_id, researcher_id=researcher_id).first()
        return cls.query.filter_by(id=project_id).first()


class ProjectTeamMember(db.Model):
    """项目团队成员模型"""
    __tablename__ = 'project_team_members'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # 成员姓名
    role = db.Column(db.String(100), nullable=False)  # 成员角色
    
    # 外键关联
    project_id = db.Column(db.Integer, db.ForeignKey('research_projects.id'), nullable=False)
    
    # 关系
    project = relationship("ResearchProject", back_populates="team_members")
    
    # 时间戳
    added_at = db.Column(db.DateTime, default=datetime.now)
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role,
            'project_id': self.project_id,
            'added_at': self.added_at.isoformat() if self.added_at else None
        }
    
    @classmethod
    def get_members_by_project(cls, project_id):
        """获取项目的所有团队成员"""
        return cls.query.filter_by(project_id=project_id).all() 