from flask import Blueprint, request, jsonify, current_app, g
from flask_login import login_user, logout_user, login_required, current_user
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo
from werkzeug.security import generate_password_hash
import re
import jwt
from datetime import datetime, timedelta
from functools import wraps

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# 角色验证装饰器
def role_required(role):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_role(role):
                return jsonify({
                    'success': False,
                    'message': '权限不足，需要 {} 角色'.format(role)
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# 注册
@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.json
    
    # 基础验证
    if not data or not data.get('username') or not data.get('email') or not data.get('password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (username, email, password)'
        }), 400
        
    # 检查用户名和邮箱是否已存在
    if User.query.filter_by(username=data['username']).first():
        return jsonify({
            'success': False,
            'message': '用户名已存在'
        }), 400
        
    if User.query.filter_by(email=data['email']).first():
        return jsonify({
            'success': False,
            'message': '邮箱已被注册'
        }), 400
        
    # 验证邮箱格式
    if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', data['email']):
        return jsonify({
            'success': False,
            'message': '邮箱格式不正确'
        }), 400
        
    # 验证密码长度
    if len(data['password']) < 6:
        return jsonify({
            'success': False,
            'message': '密码长度至少为6位'
        }), 400
    
    try:
        # 创建新用户
        user = User(
            username=data['username'],
            email=data['email'],
            password=data['password'],  # 使用setter方法加密
            full_name=data.get('full_name', ''),
            phone=data.get('phone', ''),
            is_active=True
        )
        
        # 设置角色
        role_str = data.get('role', 'patient').lower()
        if role_str == 'doctor':
            user.role = Role.DOCTOR
        elif role_str == 'researcher':
            user.role = Role.RESEARCHER
        elif role_str == 'admin':
            # 管理员注册需要特殊处理，首先检查是否有权限
            if not (current_user.is_authenticated and current_user.has_role(Role.ADMIN)):
                return jsonify({
                    'success': False,
                    'message': '没有权限注册管理员账户'
                }), 403
            user.role = Role.ADMIN
        else:
            user.role = Role.PATIENT
        
        db.session.add(user)
        db.session.flush()  # 获取用户ID
        
        # 根据角色创建相应的附加信息
        if user.role == Role.PATIENT:
            patient_info = PatientInfo(user_id=user.id)
            db.session.add(patient_info)
        elif user.role == Role.DOCTOR:
            doctor_info = DoctorInfo(user_id=user.id)
            db.session.add(doctor_info)
        elif user.role == Role.RESEARCHER:
            researcher_info = ResearcherInfo(user_id=user.id)
            db.session.add(researcher_info)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '注册成功',
            'data': user.to_dict()
        }), 201
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"注册失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'注册失败: {str(e)}'
        }), 500

# 登录
@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.json
    
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (username, password)'
        }), 400
    
    # 允许使用用户名或邮箱登录
    username = data.get('username')
    user = User.query.filter((User.username == username) | (User.email == username)).first()
    
    if not user or not user.verify_password(data.get('password')):
        return jsonify({
            'success': False,
            'message': '用户名或密码错误'
        }), 401
    
    # 检查账户是否激活
    if not user.is_active:
        return jsonify({
            'success': False,
            'message': '账户已被停用，请联系管理员'
        }), 403
    
    # 使用Flask-Login登录用户
    login_user(user)
    
    # 生成JWT令牌
    token_expiry = datetime.utcnow() + timedelta(days=1)
    token = jwt.encode(
        {
            'sub': user.id,
            'iat': datetime.utcnow(),
            'exp': token_expiry,
            'role': user.role.value
        },
        current_app.config['SECRET_KEY'],
        algorithm='HS256'
    )
    
    return jsonify({
        'success': True,
        'message': '登录成功',
        'data': {
            'user': user.to_dict(),
            'token': token,
            'token_expires': token_expiry.isoformat()
        }
    })

# 登出
@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({
        'success': True,
        'message': '已成功登出'
    })

# 获取当前用户信息
@auth_bp.route('/me', methods=['GET'])
@login_required
def get_current_user():
    return jsonify({
        'success': True,
        'data': current_user.to_dict()
    })

# 更新用户信息
@auth_bp.route('/me', methods=['PUT'])
@login_required
def update_user():
    data = request.json
    user = current_user
    
    # 更新基本信息
    if 'full_name' in data:
        user.full_name = data['full_name']
    if 'phone' in data:
        user.phone = data['phone']
    
    # 根据角色更新对应的详细信息
    if user.role == Role.PATIENT and 'patient_info' in data:
        patient_info = user.patient_info or PatientInfo(user_id=user.id)
        patient_data = data['patient_info']
        
        for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                     'medical_history', 'allergies']:
            if field in patient_data:
                setattr(patient_info, field, patient_data[field])
        
        if patient_info.id is None:
            db.session.add(patient_info)
    
    elif user.role == Role.DOCTOR and 'doctor_info' in data:
        doctor_info = user.doctor_info or DoctorInfo(user_id=user.id)
        doctor_data = data['doctor_info']
        
        for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                     'hospital', 'department', 'bio']:
            if field in doctor_data:
                setattr(doctor_info, field, doctor_data[field])
                
        if doctor_info.id is None:
            db.session.add(doctor_info)
    
    elif user.role == Role.RESEARCHER and 'researcher_info' in data:
        researcher_info = user.researcher_info or ResearcherInfo(user_id=user.id)
        researcher_data = data['researcher_info']
        
        for field in ['institution', 'department', 'research_area', 'education',
                     'publications', 'projects', 'bio']:
            if field in researcher_data:
                setattr(researcher_info, field, researcher_data[field])
                
        if researcher_info.id is None:
            db.session.add(researcher_info)
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'message': '用户信息已更新',
            'data': user.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新用户信息失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500

# 修改密码
@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    
    if not data or not data.get('current_password') or not data.get('new_password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (current_password, new_password)'
        }), 400
    
    if not current_user.verify_password(data['current_password']):
        return jsonify({
            'success': False,
            'message': '当前密码不正确'
        }), 400
    
    if len(data['new_password']) < 6:
        return jsonify({
            'success': False,
            'message': '新密码长度至少为6位'
        }), 400
    
    current_user.password = data['new_password']
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'message': '密码已成功修改'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"密码修改失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'密码修改失败: {str(e)}'
        }), 500 