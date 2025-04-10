from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='patient')  # 'patient', 'doctor', 'admin'
    email = db.Column(db.String(64), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    health_records = db.relationship('HealthRecord', foreign_keys='HealthRecord.patient_id', backref='patient')
    doctor_records = db.relationship('HealthRecord', foreign_keys='HealthRecord.doctor_id', backref='doctor')
    
    def __repr__(self):
        return f'<User {self.username}>'

class HealthRecord(db.Model):
    __tablename__ = 'health_records'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    record_type = db.Column(db.String(50), nullable=False)  # diagnosis, treatment, medication, etc.
    content_hash = db.Column(db.String(64), nullable=False)  # Hash of the content stored in MongoDB
    mongo_id = db.Column(db.String(64), nullable=False)  # MongoDB document ID
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f'<HealthRecord {self.id} - {self.record_type}>'

class PrivacyPolicy(db.Model):
    __tablename__ = 'privacy_policies'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    policy_type = db.Column(db.String(50), nullable=False)  # 'default', 'custom'
    access_level = db.Column(db.String(20), nullable=False)  # 'high', 'medium', 'low'
    allowed_users = db.Column(db.Text, nullable=True)  # JSON array of user IDs
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationship
    user = db.relationship('User', backref='privacy_policies')
    
    def __repr__(self):
        return f'<PrivacyPolicy {self.id} - {self.user_id}>'

class QueryLog(db.Model):
    __tablename__ = 'query_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    query_type = db.Column(db.String(50), nullable=False)
    query_params = db.Column(db.Text, nullable=True)  # Encrypted query parameters
    timestamp = db.Column(db.DateTime, default=datetime.now)
    success = db.Column(db.Boolean, default=True)
    
    # Relationship
    user = db.relationship('User', backref='query_logs')
    
    def __repr__(self):
        return f'<QueryLog {self.id} - {self.user_id}>' 