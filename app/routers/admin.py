from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo
from ..routers.auth import role_required, api_login_required
from sqlalchemy import or_

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# 获取所有用户列表
@admin_bp.route('/users', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_users():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '')
    role_filter = request.args.get('role', '')
    
    query = User.query
    
    # 按角色过滤
    if role_filter:
        try:
            role_enum = next(r for r in Role if r.value == role_filter)
            query = query.filter(User.role == role_enum)
        except (StopIteration, ValueError):
            pass  # 无效角色，忽略过滤
    
    # 搜索
    if search:
        query = query.filter(or_(
            User.username.ilike(f'%{search}%'),
            User.email.ilike(f'%{search}%'),
            User.full_name.ilike(f'%{search}%')
        ))
    
    # 分页
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=per_page)
    
    return jsonify({
        'success': True,
        'data': {
            'users': [user.to_dict() for user in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        }
    })

# 获取单个用户详情
@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    
    return jsonify({
        'success': True,
        'data': user.to_dict()
    })

# 创建用户（管理员专用）
@admin_bp.route('/users', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def create_user():
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
    
    try:
        # 创建新用户
        user = User(
            username=data['username'],
            email=data['email'],
            password=data['password'],  # 使用setter方法加密
            full_name=data.get('full_name', ''),
            phone=data.get('phone', ''),
            is_active=data.get('is_active', True)
        )
        
        # 设置角色
        role_str = data.get('role', 'patient').lower()
        if role_str == 'doctor':
            user.role = Role.DOCTOR
        elif role_str == 'researcher':
            user.role = Role.RESEARCHER
        elif role_str == 'admin':
            user.role = Role.ADMIN
        else:
            user.role = Role.PATIENT
            
        db.session.add(user)
        db.session.flush()  # 获取用户ID
        
        # 根据角色创建相应的附加信息
        if user.role == Role.PATIENT and 'patient_info' in data:
            patient_info = PatientInfo(user_id=user.id)
            patient_data = data['patient_info']
            
            for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                         'medical_history', 'allergies']:
                if field in patient_data:
                    setattr(patient_info, field, patient_data[field])
                    
            db.session.add(patient_info)
        
        elif user.role == Role.DOCTOR and 'doctor_info' in data:
            doctor_info = DoctorInfo(user_id=user.id)
            doctor_data = data['doctor_info']
            
            for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                         'hospital', 'department', 'bio']:
                if field in doctor_data:
                    setattr(doctor_info, field, doctor_data[field])
                    
            db.session.add(doctor_info)
        
        elif user.role == Role.RESEARCHER and 'researcher_info' in data:
            researcher_info = ResearcherInfo(user_id=user.id)
            researcher_data = data['researcher_info']
            
            for field in ['institution', 'department', 'research_area', 'education',
                         'publications', 'projects', 'bio']:
                if field in researcher_data:
                    setattr(researcher_info, field, researcher_data[field])
                    
            db.session.add(researcher_info)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '用户创建成功',
            'data': user.to_dict()
        }), 201
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建用户失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建用户失败: {str(e)}'
        }), 500

# 更新用户信息（管理员专用）
@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.json
    
    # 更新基本信息
    if 'username' in data and data['username'] != user.username:
        if User.query.filter_by(username=data['username']).first():
            return jsonify({
                'success': False,
                'message': '用户名已存在'
            }), 400
        user.username = data['username']
        
    if 'email' in data and data['email'] != user.email:
        if User.query.filter_by(email=data['email']).first():
            return jsonify({
                'success': False,
                'message': '邮箱已被注册'
            }), 400
        user.email = data['email']
    
    if 'password' in data:
        user.password = data['password']
        
    if 'full_name' in data:
        user.full_name = data['full_name']
        
    if 'phone' in data:
        user.phone = data['phone']
        
    if 'is_active' in data:
        user.is_active = data['is_active']
    
    # 更新角色
    if 'role' in data:
        role_str = data['role'].lower()
        old_role = user.role
        
        if role_str == 'doctor':
            user.role = Role.DOCTOR
        elif role_str == 'researcher':
            user.role = Role.RESEARCHER
        elif role_str == 'admin':
            user.role = Role.ADMIN
        else:
            user.role = Role.PATIENT
        
        # 如果角色发生变化，创建相应的附加信息
        if old_role != user.role:
            if user.role == Role.PATIENT and not user.patient_info:
                patient_info = PatientInfo(user_id=user.id)
                db.session.add(patient_info)
                
            elif user.role == Role.DOCTOR and not user.doctor_info:
                doctor_info = DoctorInfo(user_id=user.id)
                db.session.add(doctor_info)
                
            elif user.role == Role.RESEARCHER and not user.researcher_info:
                researcher_info = ResearcherInfo(user_id=user.id)
                db.session.add(researcher_info)
    
    # 更新角色特定信息
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

# 删除用户
@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@api_login_required
@role_required(Role.ADMIN)
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # 防止删除自己
    if user.id == current_user.id:
        return jsonify({
            'success': False,
            'message': '不能删除当前登录的用户'
        }), 400
    
    try:
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'用户 {user.username} 已被删除'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除用户失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除用户失败: {str(e)}'
        }), 500

# 获取系统统计数据
@admin_bp.route('/stats', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_stats():
    try:
        total_users = User.query.count()
        patient_count = User.query.filter_by(role=Role.PATIENT).count()
        doctor_count = User.query.filter_by(role=Role.DOCTOR).count()
        researcher_count = User.query.filter_by(role=Role.RESEARCHER).count()
        admin_count = User.query.filter_by(role=Role.ADMIN).count()
        
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = User.query.filter_by(is_active=False).count()
        
        return jsonify({
            'success': True,
            'data': {
                'total_users': total_users,
                'role_distribution': {
                    'patients': patient_count,
                    'doctors': doctor_count,
                    'researchers': researcher_count,
                    'admins': admin_count
                },
                'active_users': active_users,
                'inactive_users': inactive_users
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取统计数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取统计数据失败: {str(e)}'
        }), 500 