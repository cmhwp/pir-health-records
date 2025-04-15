from flask import Blueprint, request, jsonify, current_app, g, send_from_directory
from flask_login import login_user, logout_user, login_required, current_user
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo
from werkzeug.security import generate_password_hash
import re
import jwt
import os
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.utils import secure_filename
from ..utils.settings_utils import get_setting
from ..utils.log_utils import log_security, log_user
from sqlalchemy import or_
from ..models.system_settings import SystemSetting

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# 确保上传目录存在
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads', 'avatars')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 角色验证装饰器
def role_required(role):
    def decorator(f):
        @wraps(f)
        @api_login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_role(role):
                return jsonify({
                    'success': False,
                    'message': '权限不足，需要 {} 角色'.format(role)
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# 自定义装饰器，用于API接口的登录验证，不使用重定向
def api_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录'
            }), 401
        return f(*args, **kwargs)
    return decorated_function

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
    
    # 检查邮箱格式是否有效
    if not re.match(r"[^@]+@[^@]+\.[^@]+", data['email']):
        return jsonify({
            'success': False,
            'message': '邮箱格式无效'
        }), 400
        
    # 验证密码是否符合密码策略
    password = data.get('password')
    password_policy = get_setting('password_policy', {
        'min_length': 6,
        'require_uppercase': False,
        'require_lowercase': False,
        'require_numbers': False,
        'require_special': False
    })
    
    # 检查密码长度
    if len(password) < password_policy.get('min_length', 6):
        return jsonify({
            'success': False,
            'message': f'密码长度不能少于{password_policy.get("min_length", 6)}个字符'
        }), 400
    
    # 如果策略要求大写字母
    if password_policy.get('require_uppercase') and not any(c.isupper() for c in password):
        return jsonify({
            'success': False,
            'message': '密码必须包含至少一个大写字母'
        }), 400
    
    # 如果策略要求小写字母
    if password_policy.get('require_lowercase') and not any(c.islower() for c in password):
        return jsonify({
            'success': False,
            'message': '密码必须包含至少一个小写字母'
        }), 400
    
    # 如果策略要求数字
    if password_policy.get('require_numbers') and not any(c.isdigit() for c in password):
        return jsonify({
            'success': False,
            'message': '密码必须包含至少一个数字'
        }), 400
    
    # 如果策略要求特殊字符
    if password_policy.get('require_special'):
        import string
        special_chars = set(string.punctuation)
        if not any(c in special_chars for c in password):
            return jsonify({
                'success': False,
                'message': '密码必须包含至少一个特殊字符'
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
        
        # 记录用户注册日志
        log_user(
            message=f'新用户注册: {user.username}',
            details={
                'user_id': user.id,
                'username': user.username,
                'email': user.email,
                'role': str(user.role),
                'registration_time': datetime.now().isoformat()
            },
            user_id=user.id
        )
        
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
    
    # 检查数据是否完整
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (username, password)'
        }), 400
    
    # 查找用户
    user = User.query.filter(or_(
        User.username == data['username'],
        User.email == data['username']
    )).first()
    
    # 检查用户是否存在
    if not user:
        log_security(
            message="登录失败：用户不存在",
            details={
                'attempted_username': data['username'],
                'reason': '用户不存在',
                'login_time': datetime.now().isoformat()
            }
        )
        # 为了安全，不要暴露用户是否存在
        return jsonify({
            'success': False,
            'message': '用户名或密码错误'
        }), 401
    
    # 检查用户是否被禁用
    if not user.is_active:
        log_security(
            message="登录失败：账户已禁用",
            details={
                'user_id': user.id,
                'username': user.username,
                'reason': '账户已禁用',
                'login_time': datetime.now().isoformat()
            }
        )
        return jsonify({
            'success': False,
            'message': '账户已被禁用，请联系管理员'
        }), 403
    
    # 检查登录尝试次数
    from flask import session
    
    login_attempts_key = f'login_attempts_{user.id}'
    login_attempts = session.get(login_attempts_key, 0)
    max_attempts = get_setting('login_attempts', 5)
    
    if login_attempts >= max_attempts:
        log_security(
            message="登录失败：超过最大尝试次数",
            details={
                'user_id': user.id,
                'username': user.username,
                'reason': '超过最大尝试次数',
                'login_time': datetime.now().isoformat(),
                'attempts': login_attempts,
                'max_attempts': max_attempts
            }
        )
        return jsonify({
            'success': False,
            'message': f'超过最大尝试次数({max_attempts})，请15分钟后再试或重置密码'
        }), 429
    
    # 验证密码
    if not user.verify_password(data['password']):
        # 增加登录尝试计数
        session[login_attempts_key] = login_attempts + 1
        
        log_security(
            message="登录失败：密码错误",
            details={
                'user_id': user.id,
                'username': user.username,
                'reason': '密码错误',
                'login_time': datetime.now().isoformat(),
                'attempts': login_attempts + 1,
                'max_attempts': max_attempts
            }
        )
        
        return jsonify({
            'success': False,
            'message': '用户名或密码错误',
            'attempts_left': max_attempts - (login_attempts + 1)
        }), 401
    
    # 登录成功，重置登录尝试次数
    if login_attempts_key in session:
        session.pop(login_attempts_key)
    
    # 更新最后登录时间
    user.update_last_login()
    
    # 生成JWT令牌
    token = generate_jwt_token(user)
    
    # 记录成功登录
    log_security(
        message="用户登录成功",
        details={
            'user_id': user.id,
            'username': user.username,
            'role': str(user.role),
            'login_time': datetime.now().isoformat()
        }
    )
    
    # 设置会话持久化和超时
    session_timeout = get_setting('session_timeout', 30)  # 默认30分钟
    session.permanent = True
    
    return jsonify({
        'success': True,
        'message': '登录成功',
        'data': {
            'user': user.to_dict(),
            'token': token,
            'expires': (datetime.now() + timedelta(seconds=current_app.config['JWT_EXPIRATION_DELTA'])).timestamp()
        }
    })

# 登出
@auth_bp.route('/logout', methods=['POST'])
@api_login_required  # 使用自定义装饰器替代login_required
def logout():
    # 记录登出日志
    log_security(
        message=f'用户登出: {current_user.username}',
        details={
            'user_id': current_user.id,
            'username': current_user.username,
            'logout_time': datetime.now().isoformat()
        },
        user_id=current_user.id
    )
    
    logout_user()
    return jsonify({
        'success': True,
        'message': '登出成功'
    })

# 获取当前用户信息
@auth_bp.route('/me', methods=['GET'])
def get_current_user():
    # 检查用户是否通过JWT或会话认证
    if current_user.is_authenticated:
        # 获取用户详情并添加格式化的最后登录时间
        user_data = current_user.to_dict()
        if current_user.last_login_at:
            user_data['last_login_formatted'] = current_user.last_login_at.strftime('%Y-%m-%d %H:%M:%S')
        else:
            user_data['last_login_formatted'] = '从未登录'  
        
        # 对于患者角色，添加统计数据
        if current_user.role == Role.PATIENT:
            from ..models import HealthRecord, Appointment, Prescription
            
            records_count = HealthRecord.query.filter_by(patient_id=current_user.id).count()
            appointments_count = Appointment.query.filter_by(patient_id=current_user.id).count()
            prescriptions_count = Prescription.query.filter_by(patient_id=current_user.id).count()
            
            user_data['statistics'] = {
                'records_count': records_count,
                'appointments_count': appointments_count,
                'prescriptions_count': prescriptions_count
            }
            
        return jsonify({
            'success': True,
            'data': user_data
        })
    else:
        return jsonify({
            'success': False,
            'message': '未授权，请先登录'
        }), 401

# 更新用户信息
@auth_bp.route('/me', methods=['PUT'])
@login_required
def update_user():
    data = request.json
    if not data:
        return jsonify({
            'success': False,
            'message': '未提供更新数据'
        }), 400
    
    user = current_user
    
    # 更新基本信息
    if 'full_name' in data:
        user.full_name = data['full_name']
    if 'phone' in data:
        user.phone = data['phone']
    if 'avatar' in data:
        user.avatar = data['avatar']
    
    # 根据角色更新对应的详细信息
    if user.role == Role.PATIENT and 'patient_info' in data:
        patient_info = user.patient_info or PatientInfo(user_id=user.id)
        patient_data = data['patient_info']
        
        # 处理基本字段
        for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                     'medical_history', 'allergies']:
            if field in patient_data:
                setattr(patient_info, field, patient_data[field])
        
        # 特殊处理日期字段
        if 'date_of_birth' in patient_data:
            try:
                date_value = patient_data['date_of_birth']
                if date_value:
                    patient_info.date_of_birth = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                else:
                    patient_info.date_of_birth = None
            except (ValueError, TypeError) as e:
                current_app.logger.error(f"解析出生日期失败: {str(e)}")
        
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
        
        # 记录操作
        log_user(
            message=f'用户{user.username}更新了个人资料',
            details={
                'user_id': user.id,
                'update_fields': list(data.keys()),
                'update_time': datetime.now().isoformat()
            },
            user_id=user.id
        )
        
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
    
    if not data or not data.get('old_password') or not data.get('new_password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (old_password, new_password)'
        }), 400
    
    # 验证当前密码
    if not current_user.verify_password(data.get('old_password')):
        # 记录密码更改失败日志
        log_security(
            message=f'密码更改失败: {current_user.username}',
            details={
                'user_id': current_user.id,
                'username': current_user.username,
                'reason': '当前密码验证失败',
                'time': datetime.now().isoformat()
            },
            user_id=current_user.id
        )
        
        return jsonify({
            'success': False,
            'message': '当前密码不正确'
        }), 400
    
    # 验证新密码长度
    if len(data.get('new_password')) < 6:
        return jsonify({
            'success': False,
            'message': '新密码长度至少为6位'
        }), 400
    
    # 更新密码
    current_user.password = data.get('new_password')
    
    # 记录密码更改成功日志
    log_security(
        message=f'密码更改成功: {current_user.username}',
        details={
            'user_id': current_user.id,
            'username': current_user.username,
            'time': datetime.now().isoformat()
        },
        user_id=current_user.id
    )
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': '密码更新成功'
    })

# 上传头像
@auth_bp.route('/avatar', methods=['POST'])
@login_required
def upload_avatar():
    if 'avatar' not in request.files:
        return jsonify({
            'success': False,
            'message': '没有上传文件'
        }), 400
        
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({
            'success': False,
            'message': '没有选择文件'
        }), 400
        
    if not allowed_file(file.filename):
        return jsonify({
            'success': False,
            'message': '不支持的文件类型，只支持: ' + ', '.join(ALLOWED_EXTENSIONS)
        }), 400
        
    try:
        # 生成安全的文件名
        filename = secure_filename(file.filename)
        # 添加用户ID和时间戳，确保文件名唯一
        filename = f"{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        # 保存文件
        file.save(filepath)
        
        # 更新用户头像
        current_user.avatar = filename
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '头像上传成功',
            'data': {
                'avatar': filename
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"头像上传失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'头像上传失败: {str(e)}'
        }), 500

# 获取头像
@auth_bp.route('/avatar/<filename>', methods=['GET'])
def get_avatar(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename)
    except Exception as e:
        current_app.logger.error(f"获取头像失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': '头像不存在'
        }), 404

# 获取公开系统设置
@auth_bp.route('/public-settings', methods=['GET'])
def get_public_settings():
    """
    获取公开的系统设置信息，供未登录用户或注册页面使用
    返回密码策略、注册规则等公开设置
    """
    try:
        # 获取公开设置
        public_settings = {}
        
        # 密码策略设置
        password_policy = get_setting('password_policy', {
            'min_length': 6,
            'require_uppercase': False,
            'require_lowercase': False,
            'require_numbers': False,
            'require_special': False
        })
        public_settings['password_policy'] = password_policy
        
        # 是否需要邮箱验证
        require_email_confirmation = get_setting('require_email_confirmation', True)
        public_settings['require_email_confirmation'] = require_email_confirmation
        
        # 是否允许注册
        registration_enabled = get_setting('registration_enabled', True)
        public_settings['registration_enabled'] = registration_enabled
        
        # 获取其他标记为公开的设置
        try:
            public_db_settings = SystemSetting.query.filter_by(is_public=True).all()
            for setting in public_db_settings:
                try:
                    if setting.value_type == 'json':
                        import json
                        public_settings[setting.key] = json.loads(setting.value)
                    elif setting.value_type == 'int':
                        public_settings[setting.key] = int(setting.value)
                    elif setting.value_type == 'float':
                        public_settings[setting.key] = float(setting.value)
                    elif setting.value_type == 'bool':
                        public_settings[setting.key] = setting.value.lower() in ('true', 'yes', '1')
                    else:
                        public_settings[setting.key] = setting.value
                except:
                    public_settings[setting.key] = setting.value
        except Exception as e:
            current_app.logger.warning(f"获取公开数据库设置失败: {str(e)}")
        
        # 添加系统版本等信息
        public_settings['system_version'] = current_app.config.get('SYSTEM_VERSION', '1.0.0')
        
        # 获取可用角色列表
        available_roles = [
            {'value': 'patient', 'label': '患者', 'description': '需要使用医疗服务的用户'},
            {'value': 'doctor', 'label': '医生', 'description': '提供医疗服务的专业医护人员'}
        ]
        
        # 研究人员角色通常需要特殊审批，根据设置决定是否允许直接注册
        if get_setting('allow_researcher_registration', False):
            available_roles.append({
                'value': 'researcher', 
                'label': '研究人员', 
                'description': '进行医学研究的专业人员'
            })
            
        public_settings['available_roles'] = available_roles
        
        return jsonify({
            'success': True,
            'data': public_settings
        })
        
    except Exception as e:
        current_app.logger.error(f"获取公开系统设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取公开系统设置失败'
        }), 500

def generate_jwt_token(user):
    """生成JWT令牌"""
    current_time = datetime.now()
    payload = {
        'sub': user.id,
        'username': user.username,
        'role': user.role.value,
        'iat': current_time.timestamp(),  # 签发时间
        'exp': (current_time + timedelta(seconds=current_app.config['JWT_EXPIRATION_DELTA'])).timestamp()  # 过期时间
    }
    token = jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')
    return token 