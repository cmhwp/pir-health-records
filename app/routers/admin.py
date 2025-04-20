from flask import Blueprint, request, jsonify, current_app, send_from_directory, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo, Institution, CustomRecordType, ExportTask, ExportStatus
from ..routers.auth import role_required, api_login_required
from sqlalchemy import or_, and_
import os
import json
import time
import uuid
from datetime import datetime, timedelta, date
from sqlalchemy.sql import func, distinct, desc
from ..utils.log_utils import log_admin, add_system_log, log_export
from ..models.log import LogType

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# 处理日期时间字段函数
def process_datetime_fields(data_list):
    """
    递归处理列表或字典中的所有datetime和date对象，转换为ISO格式字符串
    
    参数:
        data_list: 要处理的数据列表或字典
        
    返回:
        处理后的数据
    """
    if isinstance(data_list, list):
        return [process_datetime_fields(item) for item in data_list]
    elif isinstance(data_list, dict):
        result = {}
        for key, value in data_list.items():
            if isinstance(value, (datetime, date)):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = process_datetime_fields(value)
            elif isinstance(value, list):
                result[key] = process_datetime_fields(value)
            else:
                result[key] = value
        return result
    else:
        # 如果既不是列表也不是字典，直接返回
        return data_list

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
    
    # 准备用户数据，添加格式化的最后登录时间
    users_data = []
    for user in pagination.items:
        user_dict = user.to_dict()
        if user.last_login_at:
            user_dict['last_login_formatted'] = user.last_login_at.strftime('%Y-%m-%d %H:%M:%S')
        else:
            user_dict['last_login_formatted'] = '从未登录'
        users_data.append(user_dict)
    
    # 记录查询用户列表的日志
    log_admin(
        message=f'管理员查询用户列表',
        details=json.dumps({
            'page': page,
            'per_page': per_page,
            'search': search,
            'role_filter': role_filter,
            'result_count': len(users_data),
            'total_count': pagination.total,
            'admin_username': current_user.username,
            'ip_address': request.remote_addr
        })
    )
    
    return jsonify({
        'success': True,
        'data': {
            'users': users_data,
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
    
    # 获取用户详情并添加格式化的最后登录时间
    user_data = user.to_dict()
    if user.last_login_at:
        user_data['last_login_formatted'] = user.last_login_at.strftime('%Y-%m-%d %H:%M:%S')
    else:
        user_data['last_login_formatted'] = '从未登录'
    
    # 记录查询单个用户详情的日志
    log_admin(
        message=f'管理员查看了用户详情: {user.username}',
        details=json.dumps({
            'viewed_user_id': user.id,
            'username': user.username,
            'role': str(user.role),
            'admin_username': current_user.username,
            'ip_address': request.remote_addr
        })
    )
    
    return jsonify({
        'success': True,
        'data': user_data
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
        if user.role == Role.PATIENT:
            patient_info = PatientInfo(user_id=user.id)
            if 'patient_info' in data:
                patient_data = data['patient_info']
                
                for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                             'medical_history', 'allergies']:
                    if field in patient_data:
                        setattr(patient_info, field, patient_data[field])
            
            db.session.add(patient_info)
            current_app.logger.info(f"为用户 {user.username} 创建了患者信息记录")
        
        elif user.role == Role.DOCTOR:
            doctor_info = DoctorInfo(user_id=user.id)
            if 'doctor_info' in data:
                doctor_data = data['doctor_info']
                
                for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                             'hospital', 'department', 'bio']:
                    if field in doctor_data:
                        setattr(doctor_info, field, doctor_data[field])
            
            db.session.add(doctor_info)
            current_app.logger.info(f"为用户 {user.username} 创建了医生信息记录")
        
        elif user.role == Role.RESEARCHER:
            researcher_info = ResearcherInfo(user_id=user.id)
            if 'researcher_info' in data:
                researcher_data = data['researcher_info']
                
                for field in ['institution', 'department', 'research_area', 'education',
                             'publications', 'projects', 'bio']:
                    if field in researcher_data:
                        setattr(researcher_info, field, researcher_data[field])
            
            db.session.add(researcher_info)
            current_app.logger.info(f"为用户 {user.username} 创建了研究人员信息记录")
        
        db.session.commit()
        
        # 记录用户创建日志
        log_admin(
            message=f'管理员创建了新用户: {user.username}',
            details={
                'created_user_id': user.id,
                'username': user.username,
                'email': user.email,
                'role': str(user.role),
                'admin_username': current_user.username,
                'creation_time': datetime.now().isoformat()
            }
        )
        
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
    
    # 更新前先保存原始数据以便日志记录
    old_data = {
        'username': user.username,
        'email': user.email,
        'full_name': user.full_name,
        'phone': user.phone,
        'is_active': user.is_active,
        'role': str(user.role)
    }
    
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
        new_role = None
        
        if role_str == 'doctor':
            new_role = Role.DOCTOR
        elif role_str == 'researcher':
            new_role = Role.RESEARCHER
        elif role_str == 'admin':
            new_role = Role.ADMIN
        else:
            new_role = Role.PATIENT
        
        # 如果角色发生变化，处理相关角色信息表
        if old_role != new_role:
            current_app.logger.info(f"用户 {user.username} 角色从 {old_role.value} 更改为 {new_role.value}")
            
            # 记录角色相关数据的处理
            data_handling_info = {}
            
            # 检查是否要保留原有角色数据
            transfer_data = data.get('transfer_role_data', False)
            
            # 专门处理从PATIENT到DOCTOR的角色转换，可复用患者病史等信息
            if transfer_data and old_role == Role.PATIENT and new_role == Role.DOCTOR and user.patient_info:
                # 如果是从患者转为医生且选择了迁移数据，可以保留一些基本信息
                patient_data = {
                    'gender': user.patient_info.gender,
                    'date_of_birth': user.patient_info.date_of_birth,
                    'address': user.patient_info.address,
                    'emergency_contact': user.patient_info.emergency_contact,
                    'emergency_phone': user.patient_info.emergency_phone,
                }
                data_handling_info['patient_to_doctor'] = {
                    'action': 'transferred_basic_info',
                    'transferred_fields': list(patient_data.keys())
                }
                
                # 创建或更新医生信息，同时转移一些基本个人信息
                doctor_info = user.doctor_info or DoctorInfo(user_id=user.id)
                # 可以转移的基本信息
                if 'doctor_info' not in data:
                    data['doctor_info'] = {}
                
                # 确保不覆盖已提供的字段
                doctor_data = data['doctor_info']
                current_app.logger.info(f"转移患者基本信息到医生信息")
            
            # 存储/删除旧角色数据
            if old_role == Role.PATIENT:
                if user.patient_info:
                    # 是否保留患者数据
                    keep_data = data.get('keep_patient_data', False)
                    if not keep_data:
                        data_handling_info['patient_info'] = {
                            'action': 'deleted',
                            'data_summary': user.patient_info.to_dict()
                        }
                        db.session.delete(user.patient_info)
                        current_app.logger.info(f"已删除用户 {user.username} 的患者信息记录")
                    else:
                        data_handling_info['patient_info'] = {'action': 'preserved'}
                        current_app.logger.info(f"已保留用户 {user.username} 的患者信息记录")
            
            elif old_role == Role.DOCTOR:
                if user.doctor_info:
                    keep_data = data.get('keep_doctor_data', False)
                    if not keep_data:
                        data_handling_info['doctor_info'] = {
                            'action': 'deleted',
                            'data_summary': user.doctor_info.to_dict()
                        }
                        db.session.delete(user.doctor_info)
                        current_app.logger.info(f"已删除用户 {user.username} 的医生信息记录")
                    else:
                        data_handling_info['doctor_info'] = {'action': 'preserved'}
                        current_app.logger.info(f"已保留用户 {user.username} 的医生信息记录")
            
            elif old_role == Role.RESEARCHER:
                if user.researcher_info:
                    keep_data = data.get('keep_researcher_data', False)
                    if not keep_data:
                        data_handling_info['researcher_info'] = {
                            'action': 'deleted',
                            'data_summary': user.researcher_info.to_dict()
                        }
                        db.session.delete(user.researcher_info)
                        current_app.logger.info(f"已删除用户 {user.username} 的研究人员信息记录")
                    else:
                        data_handling_info['researcher_info'] = {'action': 'preserved'}
                        current_app.logger.info(f"已保留用户 {user.username} 的研究人员信息记录")
            
            # 设置新角色
            user.role = new_role
            
            # 确保新角色有对应的信息记录
            if new_role == Role.PATIENT and not user.patient_info:
                patient_info = PatientInfo(user_id=user.id)
                db.session.add(patient_info)
                current_app.logger.info(f"为用户 {user.username} 创建了患者信息记录")
                data_handling_info['new_patient_info'] = {'action': 'created'}
                
                # 如果提供了患者信息，立即更新
                if 'patient_info' in data:
                    patient_data = data['patient_info']
                    for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                                'medical_history', 'allergies']:
                        if field in patient_data:
                            setattr(patient_info, field, patient_data[field])
                    
                    # 处理日期字段
                    if 'date_of_birth' in patient_data:
                        try:
                            date_value = patient_data['date_of_birth']
                            if date_value:
                                patient_info.date_of_birth = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                            else:
                                patient_info.date_of_birth = None
                        except (ValueError, TypeError) as e:
                            current_app.logger.error(f"解析出生日期失败: {str(e)}")
                
            elif new_role == Role.DOCTOR and not user.doctor_info:
                doctor_info = DoctorInfo(user_id=user.id)
                db.session.add(doctor_info)
                current_app.logger.info(f"为用户 {user.username} 创建了医生信息记录")
                data_handling_info['new_doctor_info'] = {'action': 'created'}
                
                # 如果提供了医生信息，立即更新
                if 'doctor_info' in data:
                    doctor_data = data['doctor_info']
                    for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                                'hospital', 'department', 'bio']:
                        if field in doctor_data:
                            setattr(doctor_info, field, doctor_data[field])
                
            elif new_role == Role.RESEARCHER and not user.researcher_info:
                researcher_info = ResearcherInfo(user_id=user.id)
                db.session.add(researcher_info)
                current_app.logger.info(f"为用户 {user.username} 创建了研究人员信息记录")
                data_handling_info['new_researcher_info'] = {'action': 'created'}
                
                # 如果提供了研究人员信息，立即更新
                if 'researcher_info' in data:
                    researcher_data = data['researcher_info']
                    for field in ['institution', 'department', 'research_area', 'education',
                                'publications', 'projects', 'bio']:
                        if field in researcher_data:
                            setattr(researcher_info, field, researcher_data[field])
            
            # 记录角色变更
            log_admin(
                message=f'管理员更改了用户 {user.username} 的角色: 从 {old_role.value} 变更为 {new_role.value}',
                details={
                    'user_id': user.id,
                    'username': user.username,
                    'old_role': old_role.value,
                    'new_role': new_role.value,
                    'data_handling': data_handling_info,
                    'admin_username': current_user.username,
                    'time': datetime.now().isoformat()
                }
            )
            
            # 提示信息
            role_change_message = f"用户角色已从 {old_role.value} 更改为 {new_role.value}，已创建相应的角色信息记录。"
            
            # 如果选择保留原角色数据，添加提示
            if data.get(f'keep_{old_role.value.lower()}_data', False):
                role_change_message += f" 已保留原 {old_role.value} 角色的数据。"
            else:
                role_change_message += f" 原 {old_role.value} 角色的数据已被清除。"
                
            # 如果进行了数据迁移，添加提示
            if transfer_data and old_role == Role.PATIENT and new_role == Role.DOCTOR:
                role_change_message += " 部分基本个人信息已从患者资料迁移至医生资料。"
        else:
            role_change_message = ""
    else:
        role_change_message = ""
    
    # 更新角色特定信息（对于未发生角色变更的情况或保留了原角色数据的情况）
    if user.role == Role.PATIENT and 'patient_info' in data:
        patient_info = user.patient_info or PatientInfo(user_id=user.id)
        patient_data = data['patient_info']
        
        for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                     'medical_history', 'allergies']:
            if field in patient_data:
                setattr(patient_info, field, patient_data[field])
        
        # 处理日期字段
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
        
        # 准备新数据用于比较
        new_data = {
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'phone': user.phone,
            'is_active': user.is_active,
            'role': str(user.role)
        }
        
        # 记录用户更新日志
        log_admin(
            message=f'管理员更新了用户信息: {user.username}',
            details=json.dumps({
                'user_id': user.id,
                'username': user.username,
                'changes': {key: {'old': old_data[key], 'new': new_data[key]} for key in old_data if old_data[key] != new_data[key]},
                'admin_username': current_user.username,
                'ip_address': request.remote_addr
            })
        )
        
        success_message = '用户信息已更新'
        if role_change_message:
            success_message += f" {role_change_message}"
            
        return jsonify({
            'success': True,
            'message': success_message,
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
        # 保存用户信息以便日志记录
        deleted_user_info = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': str(user.role),
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_login': user.last_login_at.isoformat() if user.last_login_at else None
        }
        
        db.session.delete(user)
        db.session.commit()
        
        # 记录用户删除日志
        log_admin(
            message=f'管理员删除了用户: {deleted_user_info["username"]}',
            details=json.dumps({
                'deleted_user_info': deleted_user_info,
                'admin_username': current_user.username,
                'deletion_time': datetime.now().isoformat(),
                'reason': request.args.get('reason', '管理员删除')
            })
        )
        
        return jsonify({
            'success': True,
            'message': f'用户 {deleted_user_info["username"]} 已被删除'
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

# 获取系统日志
@admin_bp.route('/logs', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_logs():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        log_type = request.args.get('type', '')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        from ..models.log import SystemLog
        query = SystemLog.query
        
        # 按日志类型筛选
        if log_type:
            query = query.filter(SystemLog.log_type == log_type)
            
        # 按日期范围筛选
        if start_date:
            try:
                start_datetime = datetime.fromisoformat(start_date)
                query = query.filter(SystemLog.created_at >= start_datetime)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_datetime = datetime.fromisoformat(end_date)
                query = query.filter(SystemLog.created_at <= end_datetime)
            except ValueError:
                pass
        
        # 分页
        pagination = query.order_by(SystemLog.created_at.desc()).paginate(page=page, per_page=per_page)
        
        return jsonify({
            'success': True,
            'data': {
                'logs': [log.to_dict() for log in pagination.items],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统日志失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统日志失败: {str(e)}'
        }), 500

# 获取用户活动统计
@admin_bp.route('/users/activity', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_user_activity():
    try:
        days = request.args.get('days', 30, type=int)
        user_id = request.args.get('user_id', type=int)
        
        from ..models.user import User
        from ..models import QueryHistory
        
        start_date = datetime.now() - timedelta(days=days)
        
        # 构建查询
        query = db.session.query(
            func.date(QueryHistory.query_time).label('date'),
            func.count().label('count')
        ).filter(QueryHistory.query_time >= start_date)
        
        # 如果指定了用户ID，则只查询该用户的活动
        if user_id:
            user = User.query.get_or_404(user_id)
            query = query.filter(QueryHistory.user_id == user_id)
            
        # 按日期分组
        query = query.group_by(func.date(QueryHistory.query_time)).order_by('date')
        
        # 执行查询
        activity_data = [{'date': str(row.date), 'count': row.count} for row in query.all()]
        
        # 获取用户类型分布
        role_distribution = db.session.query(
            User.role, 
            func.count().label('count')
        ).group_by(User.role).all()
        
        role_data = {str(role.value): count for role, count in role_distribution}
        
        # 获取最活跃的用户
        active_users = db.session.query(
            User.id, 
            User.username,
            User.email,
            User.role,
            func.count(QueryHistory.id).label('activity_count')
        ).join(QueryHistory, User.id == QueryHistory.user_id)\
         .filter(QueryHistory.query_time >= start_date)\
         .group_by(User.id)\
         .order_by(desc('activity_count'))\
         .limit(10).all()
        
        active_users_data = [{
            'id': row[0],  # User.id
            'username': row[1],  # User.username
            'email': row[2],  # User.email
            'role': str(row[3].value),  # User.role.value
            'activity_count': row[4]  # 活动计数
        } for row in active_users]
        
        return jsonify({
            'success': True,
            'data': {
                'daily_activity': activity_data,
                'role_distribution': role_data,
                'most_active_users': active_users_data,
                'period_days': days
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取用户活动统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取用户活动统计失败: {str(e)}'
        }), 500

# 导出系统数据
@admin_bp.route('/export/data', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def export_system_data():
    try:
        data = request.json
        if not data or 'export_type' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数 (export_type)'
            }), 400
            
        export_type = data.get('export_type')
        export_format = data.get('format', 'json')  # 默认为JSON格式
        options = data.get('options', {})
        
        # 检查是否需要匿名化数据 - 适应前端传递的不同格式
        if isinstance(options, list):
            anonymize_data = 'anonymize' in options
        elif isinstance(options, dict):
            anonymize_data = options.get('anonymize', False)
        else:
            anonymize_data = False
            
        current_app.logger.debug(f"数据导出选项: {options}, 是否匿名化: {anonymize_data}")
        
        # 创建导出目录
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads/exports')
        os.makedirs(export_dir, exist_ok=True)
        
        # 生成安全令牌并添加到文件名
        import secrets
        token = secrets.token_hex(8)  # 16个随机字符
        
        # 生成导出文件名
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        export_id = f"EXP-{uuid.uuid4().hex[:8].upper()}"  # 生成唯一导出ID
        
        # 文件扩展名
        file_extension = export_format.lower()
        if file_extension == 'excel':
            file_extension = 'xlsx'  # 使用正确的Excel文件扩展名
        filename = f"system_export_{export_type}_{timestamp}_{token}.{file_extension}"
        filepath = os.path.join(export_dir, filename)
        
        # 创建导出任务记录
        export_task = ExportTask(
            export_id=export_id,
            user_id=current_user.id,
            export_type=export_type,
            format=export_format,
            status=ExportStatus.PROCESSING,  # 设为正在处理状态
            started_at=datetime.now(),
            filename=filename,
            file_path=filepath,
            options=options,  # 直接存储前端传递的options对象
            parameters=data
        )
        
        db.session.add(export_task)
        db.session.flush()  # 获取ID但不提交事务
        
        # 导出信息以便日志记录
        export_info = {
            'export_id': export_id,
            'export_type': export_type,
            'format': export_format,
            'filename': filename,
            'timestamp': datetime.now().isoformat(),
            'parameters': data,
            'token': token,
            'options': options
        }
        
        try:
            # 根据类型导出不同的数据
            if export_type == 'users':
                # 导出用户数据
                users = User.query.all()
                export_data = [user.to_dict() for user in users]
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for user_data in export_data:
                        # 匿名化用户邮箱
                        if 'email' in user_data:
                            parts = user_data['email'].split('@')
                            if len(parts) > 1:
                                domain = parts[1]
                                user_data['email'] = f"user_{user_data['id']}@{domain}"
                        
                        # 匿名化电话号码
                        if 'phone' in user_data and user_data['phone']:
                            user_data['phone'] = f"****{user_data['phone'][-4:]}" if len(user_data['phone']) >= 4 else "********"
                        
                        # 匿名化全名
                        if 'full_name' in user_data and user_data['full_name']:
                            user_data['full_name'] = f"用户_{user_data['id']}"
                
                export_info['record_count'] = len(export_data)
                export_task.record_count = len(export_data)
                
            elif export_type == 'health_records':
                # 导出健康记录数据
                patient_id = data.get('patient_id')
                limit = data.get('limit', 1000)
                
                from ..utils.mongo_utils import get_mongo_db
                
                mongo_db = get_mongo_db()
                query = {}
                
                if patient_id:
                    query['patient_id'] = patient_id
                    export_info['patient_id'] = patient_id
                    
                cursor = mongo_db.health_records.find(query).limit(limit)
                export_data = [record for record in cursor]
                
                # 处理ObjectId格式和确保数据结构一致性
                for record in export_data:
                    record['_id'] = str(record['_id'])
                    
                    # 确保嵌套对象为可序列化对象
                    if 'files' in record and record['files']:
                        # 确保文件条目是简单对象
                        simplified_files = []
                        for file in record['files']:
                            if isinstance(file, dict):
                                # 只保留关键信息
                                simplified_file = {
                                    'filename': file.get('filename', ''),
                                    'file_size': file.get('file_size', 0),
                                    'file_type': file.get('file_type', ''),
                                    'uploaded_at': str(file.get('uploaded_at', ''))
                                }
                                simplified_files.append(simplified_file)
                        record['files'] = simplified_files
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for record in export_data:
                        # 删除或混淆敏感字段
                        if 'patient_name' in record:
                            record['patient_name'] = f"患者_{record.get('patient_id', 'unknown')}"
                        
                        # 删除详细地址
                        if 'address' in record:
                            record['address'] = "****"
                        
                        # 处理其他敏感信息
                        sensitive_fields = ['phone', 'contact_info', 'emergency_contact']
                        for field in sensitive_fields:
                            if field in record and record[field]:
                                record[field] = "********"
                
                export_info['record_count'] = len(export_data)
                export_info['limit'] = limit
                export_task.record_count = len(export_data)
                    
            elif export_type == 'system_logs':
                # 导出系统日志
                from ..models.log import SystemLog
                
                start_date = data.get('start_date')
                end_date = data.get('end_date')
                
                query = SystemLog.query
                
                if start_date:
                    try:
                        start_datetime = datetime.fromisoformat(start_date)
                        query = query.filter(SystemLog.created_at >= start_datetime)
                        export_info['start_date'] = start_date
                    except ValueError:
                        pass
                        
                if end_date:
                    try:
                        end_datetime = datetime.fromisoformat(end_date)
                        query = query.filter(SystemLog.created_at <= end_datetime)
                        export_info['end_date'] = end_date
                    except ValueError:
                        pass
                
                logs = query.order_by(SystemLog.created_at.desc()).all()
                export_data = [log.to_dict() for log in logs]
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for log_data in export_data:
                        # 匿名化IP地址
                        if 'ip_address' in log_data and log_data['ip_address']:
                            ip_parts = log_data['ip_address'].split('.')
                            if len(ip_parts) == 4:
                                log_data['ip_address'] = f"{ip_parts[0]}.{ip_parts[1]}.*.*"
                        
                        # 匿名化详细信息
                        if 'details' in log_data and isinstance(log_data['details'], dict):
                            # 删除敏感详情
                            if 'user_agent' in log_data['details']:
                                log_data['details']['user_agent'] = "[已匿名化]"
                            if 'ip_address' in log_data['details']:
                                ip_parts = log_data['details']['ip_address'].split('.')
                                if len(ip_parts) == 4:
                                    log_data['details']['ip_address'] = f"{ip_parts[0]}.{ip_parts[1]}.*.*"
                
                export_info['record_count'] = len(export_data)
                export_task.record_count = len(export_data)
                
            elif export_type == 'medications':
                # 导出药物数据
                from ..models.health_records import MedicationRecord
                
                # 获取所有处方信息
                medications = MedicationRecord.query.all()
                medication_data = []
                
                for medication in medications:
                    # 直接使用to_dict转换，不尝试获取关联项目
                    medication_dict = medication.to_dict()
                    
                    # 获取相关的健康记录信息
                    if medication.health_record:
                        medication_dict['health_record'] = {
                            'id': medication.health_record.id,
                            'title': medication.health_record.title,
                            'record_type': medication.health_record.record_type,
                            'record_date': medication.health_record.record_date.isoformat() if medication.health_record.record_date else None,
                            'mongo_id': medication.health_record.mongo_id
                        }
                    
                    medication_data.append(medication_dict)
                
                export_data = medication_data
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for prescription in export_data:
                        # 匿名化患者和医生信息
                        if 'patient_name' in prescription:
                            prescription['patient_name'] = f"患者_{prescription.get('patient_id', 'unknown')}"
                        if 'doctor_name' in prescription:
                            prescription['doctor_name'] = f"医生_{prescription.get('doctor_id', 'unknown')}"
                        # 如果包含健康记录，也匿名化其中的信息
                        if 'health_record' in prescription and isinstance(prescription['health_record'], dict):
                            if 'patient_name' in prescription['health_record']:
                                prescription['health_record']['patient_name'] = f"患者_{prescription['health_record'].get('patient_id', 'unknown')}"
                
                export_info['record_count'] = len(export_data)
                export_task.record_count = len(export_data)
                
            elif export_type == 'vitals':
                # 导出生命体征数据
                from ..models.health_records import VitalSign
                
                vitals = VitalSign.query.all()
                export_data = [vital.to_dict() for vital in vitals]
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for vital in export_data:
                        # 仅保留记录ID和测量值，移除患者识别信息
                        if 'patient_id' in vital:
                            vital['patient_id'] = str(vital['patient_id'])  # 转为字符串但不匿名化，因为这是外键
                        if 'patient_name' in vital:
                            vital['patient_name'] = f"患者_{vital.get('patient_id', 'unknown')}"
                
                export_info['record_count'] = len(export_data)
                export_task.record_count = len(export_data)
                
            elif export_type == 'labs':
                # 导出实验室项目数据
                # 这里需要根据实际的实验室数据模型调整
                # 假设有一个LabResult模型
                try:
                    from ..models.researcher import ResearchProject
                    research_projects = ResearchProject.query.all()
                    export_data = [project.to_dict() for project in research_projects]
                except ImportError:
                    # 如果模型不存在，可以从MongoDB获取
                    from ..utils.mongo_utils import get_mongo_db
                    mongo_db = get_mongo_db()
                    export_data = list(mongo_db.lab_results.find())
                    for record in export_data:
                        record['_id'] = str(record['_id'])
                
                # 处理日期时间对象
                export_data = process_datetime_fields(export_data)
                
                # 如果需要匿名化，处理敏感字段
                if anonymize_data:
                    for result in export_data:
                        # 匿名化患者信息
                        if 'patient_name' in result:
                            result['patient_name'] = f"患者_{result.get('patient_id', 'unknown')}"
                        if 'doctor_name' in result:
                            result['doctor_name'] = f"医生_{result.get('doctor_id', 'unknown')}"
                        # 安全处理 team_members，确保它是列表而不是对象
                        if 'team_members' in result and result['team_members'] is not None:
                            # 检查是否已经是字典列表
                            if isinstance(result['team_members'], list):
                                processed_members = []
                                for member in result['team_members']:
                                    # 如果成员是字典，直接使用
                                    if isinstance(member, dict):
                                        processed_members.append(member)
                                    # 如果成员有to_dict方法，调用它
                                    elif hasattr(member, 'to_dict') and callable(getattr(member, 'to_dict')):
                                        processed_members.append(member.to_dict())
                                    # 如果都不是，跳过此成员
                                    else:
                                        current_app.logger.warning(f"跳过不支持的成员类型: {type(member)}")
                                result['team_members'] = processed_members
                
                export_info['record_count'] = len(export_data)
                export_task.record_count = len(export_data)
                
            else:
                return jsonify({
                    'success': False,
                    'message': f'不支持的导出类型: {export_type}'
                }), 400
                
            # 根据格式导出数据
            if export_format == 'json':
                # 写入JSON文件
                class DateTimeEncoder(json.JSONEncoder):
                    def default(self, obj):
                        if isinstance(obj, (datetime, date)):
                            return obj.isoformat()
                        return super().default(obj)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
            elif export_format == 'csv':
                # 导出为CSV
                import csv
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    if export_data and len(export_data) > 0:
                        # 扁平化和预处理数据
                        processed_data = []
                        for item in export_data:
                            # 复制数据以避免修改原始数据
                            processed_item = {}
                            for key, value in item.items():
                                # 对于嵌套字典或列表，转换为JSON字符串
                                if isinstance(value, (dict, list)):
                                    processed_item[key] = json.dumps(value, ensure_ascii=False)
                                else:
                                    processed_item[key] = value
                            processed_data.append(processed_item)
                        
                        # 提取所有可能的字段
                        all_fields = set()
                        for item in processed_data:
                            all_fields.update(item.keys())
                        
                        # 按字母顺序排序字段
                        fieldnames = sorted(list(all_fields))
                        
                        writer = csv.DictWriter(f, fieldnames=fieldnames)   
                        writer.writeheader()
                        writer.writerows(processed_data)
            elif export_format == 'excel':
                # 导出为Excel
                import pandas as pd
                
                # 扁平化和预处理数据
                processed_data = []
                for item in export_data:
                    # 复制数据以避免修改原始数据
                    processed_item = {}
                    for key, value in item.items():
                        # 对于嵌套字典或列表，转换为JSON字符串
                        if isinstance(value, (dict, list)):
                            processed_item[key] = json.dumps(value, ensure_ascii=False)
                        else:
                            processed_item[key] = value
                    processed_data.append(processed_item)
                
                df = pd.DataFrame(processed_data)
                df.to_excel(filepath, index=False, engine='openpyxl')
            else:
                # 默认使用JSON格式
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)
                    
            # 更新文件大小
            file_size = os.path.getsize(filepath)
            export_task.file_size = file_size
            export_info['file_size'] = file_size
            
            # 更新导出任务状态为完成
            export_task.status = ExportStatus.COMPLETED
            export_task.completed_at = datetime.now()
            
            # 是否进行了匿名化操作
            if anonymize_data:
                export_task.notes = "数据已匿名化处理"
                export_info['anonymized'] = True
            
            # 记录数据导出日志
            log_export(
                message=f'管理员导出{export_type}数据',
                details=json.dumps({
                    'export_info': export_info,
                    'admin_username': current_user.username,
                    'file_size': file_size,
                    'ip_address': request.remote_addr
                }),
                user_id=current_user.id
            )
            
        except Exception as e:
            # 出现错误，更新导出任务状态为失败
            export_task.status = ExportStatus.FAILED
            export_task.error_message = str(e)
            export_task.completed_at = datetime.now()
            
            # 记录错误日志
            current_app.logger.error(f"导出{export_type}数据失败: {str(e)}")
            add_system_log(LogType.ERROR, f"导出{export_type}数据失败", details=str(e))
            
            db.session.commit()
            
            return jsonify({
                'success': False,
                'message': f'导出{export_type}数据失败: {str(e)}'
            }), 500
        
        # 提交事务，保存导出任务记录
        db.session.commit()
            
        # 返回下载链接
        download_url = f"/api/admin/export/download/{filename}"
        
        return jsonify({
            'success': True,
            'message': f'成功导出{export_type}数据',
            'data': {
                'export_id': export_id,
                'filename': filename,
                'download_url': download_url,
                'record_count': export_task.record_count,
                'file_size': export_task.file_size
            }
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"导出系统数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导出系统数据失败: {str(e)}'
        }), 500

# 下载导出的数据
@admin_bp.route('/export/download/<filename>', methods=['GET'])
def download_exported_data(filename):
    try:
        # 获取正确的导出目录
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads/exports')
        
        # 检查文件是否存在
        file_path = os.path.join(export_dir, filename)
        if not os.path.exists(file_path):
            current_app.logger.error(f"导出文件不存在: {file_path}")
            return jsonify({
                'success': False,
                'message': '文件不存在或已被删除'
            }), 404
        
        # 安全检查：确认文件名格式是否正确
        parts = filename.split('_')
        if len(parts) < 5 or not parts[0].startswith('system'):
            current_app.logger.warning(f"无效的导出文件名格式: {filename}")
            return jsonify({
                'success': False,
                'message': '无效的文件格式'
            }), 400
            
        # 检查来源IP地址并记录
        ip_address = request.remote_addr
        current_app.logger.info(f"文件下载请求: {filename}, IP: {ip_address}")
        
        # 记录下载行为（如果用户已登录）
        if current_user.is_authenticated:
            log_admin(
                message=f'管理员下载导出文件: {filename}',
                details=json.dumps({
                    'filename': filename,
                    'file_size': os.path.getsize(file_path),
                    'download_time': datetime.now().isoformat(),
                    'admin_username': current_user.username,
                    'ip_address': ip_address
                })
            )
        
        # 返回文件，使用send_file而不是send_from_directory以避免重定向
        from flask import send_file
        return send_file(
            file_path,
            mimetype='application/json',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"下载导出数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'下载导出数据失败: {str(e)}'
        }), 500

# 获取导出任务历史
@admin_bp.route('/export/history', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_export_history():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        export_type = request.args.get('type', '')
        status = request.args.get('status', '')
        
        # 构建查询
        query = ExportTask.query
        
        # 按导出类型过滤
        if export_type:
            query = query.filter(ExportTask.export_type == export_type)
        
        # 按状态过滤
        if status:
            try:
                status_enum = ExportStatus(status)
                query = query.filter(ExportTask.status == status_enum)
            except ValueError:
                pass  # 忽略无效状态
        
        # 分页查询
        pagination = query.order_by(ExportTask.created_at.desc()).paginate(page=page, per_page=per_page)
        
        # 准备结果集
        export_history = []
        for task in pagination.items:
            # 获取导出用户信息
            user = User.query.get(task.user_id)
            export_data = task.to_dict()
            
            # 添加用户信息
            if user:
                export_data['exportedBy'] = user.username
            else:
                export_data['exportedBy'] = f"用户ID {task.user_id}"
                
            # 格式化选项列表
            if isinstance(task.options, dict) and 'options' in task.options:
                export_data['options'] = task.options.get('options', [])
            
            export_history.append(export_data)
        
        return jsonify({
            'success': True,
            'data': {
                'exportHistory': export_history,
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取导出历史失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取导出历史失败: {str(e)}'
        }), 500

# 获取导出任务详情
@admin_bp.route('/export/<export_id>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_export_details(export_id):
    try:
        # 查找导出任务
        export_task = ExportTask.query.filter_by(export_id=export_id).first()
        
        if not export_task:
            return jsonify({
                'success': False,
                'message': '导出任务不存在'
            }), 404
        
        # 获取导出用户信息
        user = User.query.get(export_task.user_id)
        export_data = export_task.to_dict()
        
        # 添加用户信息
        if user:
            export_data['exportedBy'] = user.username
            export_data['exportedByFullName'] = user.full_name
            export_data['exportedByEmail'] = user.email
        else:
            export_data['exportedBy'] = f"用户ID {export_task.user_id}"
        
        # 检查导出文件是否存在
        if export_task.file_path and os.path.exists(export_task.file_path):
            export_data['fileExists'] = True
            export_data['fileSize'] = os.path.getsize(export_task.file_path)
            export_data['downloadUrl'] = f"/admin/export/download/{export_task.filename}"
        else:
            export_data['fileExists'] = False
        
        # 获取相关日志
        from ..models.log import SystemLog, LogType
        logs = SystemLog.query.filter(
            SystemLog.log_type == LogType.EXPORT,
            SystemLog.details.like(f'%{export_id}%')
        ).order_by(SystemLog.created_at.desc()).limit(5).all()
        
        export_data['logs'] = [log.to_dict() for log in logs]
        
        return jsonify({
            'success': True,
            'data': export_data
        })
    except Exception as e:
        current_app.logger.error(f"获取导出任务详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取导出任务详情失败: {str(e)}'
        }), 500

# 取消导出任务
@admin_bp.route('/export/<export_id>/cancel', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def cancel_export_task(export_id):
    try:
        # 查找导出任务
        export_task = ExportTask.query.filter_by(export_id=export_id).first()
        
        if not export_task:
            return jsonify({
                'success': False,
                'message': '导出任务不存在'
            }), 404
        
        # 只能取消处理中或等待中的任务
        if export_task.status not in [ExportStatus.PENDING, ExportStatus.PROCESSING]:
            return jsonify({
                'success': False,
                'message': f'无法取消{export_task.status.value}状态的任务'
            }), 400
        
        # 更新任务状态
        export_task.status = ExportStatus.FAILED
        export_task.error_message = '管理员手动取消任务'
        export_task.completed_at = datetime.now()
        
        # 记录取消操作
        log_admin(
            message=f'管理员取消了导出任务: {export_id}',
            details=json.dumps({
                'export_id': export_id,
                'export_type': export_task.export_type,
                'admin_username': current_user.username,
                'cancel_time': datetime.now().isoformat(),
                'previous_status': str(export_task.status)
            }),
            user_id=current_user.id
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '导出任务已取消',
            'data': {
                'export_id': export_id,
                'status': str(export_task.status)
            }
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"取消导出任务失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'取消导出任务失败: {str(e)}'
        }), 500

# 删除导出任务
@admin_bp.route('/export/<export_id>', methods=['DELETE'])
@api_login_required
@role_required(Role.ADMIN)
def delete_export_task(export_id):
    try:
        # 查找导出任务
        export_task = ExportTask.query.filter_by(export_id=export_id).first()
        
        if not export_task:
            return jsonify({
                'success': False,
                'message': '导出任务不存在'
            }), 404
        
        # 保存任务信息用于日志记录
        task_info = {
            'export_id': export_id,
            'export_type': export_task.export_type,
            'filename': export_task.filename,
            'status': str(export_task.status),
            'created_at': export_task.created_at.isoformat() if export_task.created_at else None
        }
        
        # 检查是否需要删除文件
        should_delete_file = request.args.get('delete_file', 'false').lower() == 'true'
        
        if should_delete_file and export_task.file_path and os.path.exists(export_task.file_path):
            try:
                os.remove(export_task.file_path)
                task_info['file_deleted'] = True
            except Exception as e:
                current_app.logger.error(f"删除导出文件失败: {str(e)}")
                task_info['file_deleted'] = False
                task_info['file_error'] = str(e)
        
        # 删除数据库记录
        db.session.delete(export_task)
        db.session.commit()
        
        # 记录删除操作
        log_admin(
            message=f'管理员删除了导出任务: {export_id}',
            details=json.dumps({
                'task_info': task_info,
                'admin_username': current_user.username,
                'deletion_time': datetime.now().isoformat(),
                'deleted_file': should_delete_file
            }),
            user_id=current_user.id
        )
        
        return jsonify({
            'success': True,
            'message': '导出任务已删除',
            'data': task_info
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除导出任务失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除导出任务失败: {str(e)}'
        }), 500

# 获取系统设置
@admin_bp.route('/settings', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_settings():
    try:
        # 从数据库或配置文件获取系统设置
        settings = {}
        
        # 尝试从数据库获取存储的配置
        from ..models.system_settings import SystemSetting
        stored_settings = SystemSetting.query.all()
        
        for setting in stored_settings:
            # 解析JSON值
            try:
                if setting.value_type == 'json':
                    settings[setting.key] = json.loads(setting.value)
                elif setting.value_type == 'int':
                    settings[setting.key] = int(setting.value)
                elif setting.value_type == 'float':
                    settings[setting.key] = float(setting.value)
                elif setting.value_type == 'bool':
                    settings[setting.key] = setting.value.lower() in ('true', 'yes', '1')
                else:
                    settings[setting.key] = setting.value
            except:
                settings[setting.key] = setting.value
        
        # 添加一些应用配置
        settings['pir_enabled'] = current_app.config.get('PIR_ENABLE_OBFUSCATION', False)
        settings['debug_mode'] = current_app.config.get('DEBUG', False)
        settings['upload_limit'] = current_app.config.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)
        
        # 分组设置
        grouped_settings = {
            'security': {
                'password_policy': settings.get('password_policy', {}),
                'login_attempts': settings.get('login_attempts', 5),
                'session_timeout': settings.get('session_timeout', 30),
                'require_email_confirmation': settings.get('require_email_confirmation', True)
            },
            'privacy': {
                'pir_enabled': settings.get('pir_enabled', False),
                'pir_batch_size': settings.get('pir_batch_size', 10),
                'default_record_visibility': settings.get('default_record_visibility', 'private')
            },
            'system': {
                'debug_mode': settings.get('debug_mode', False),
                'upload_limit': settings.get('upload_limit', 16 * 1024 * 1024),
                'max_export_size': settings.get('max_export_size', 1000),
                'system_version': current_app.config.get('SYSTEM_VERSION', '1.0.0')
            },
            'registration': {
                'registration_enabled': settings.get('registration_enabled', True),
                'require_email_confirmation': settings.get('require_email_confirmation', True),
                'allow_researcher_registration': settings.get('allow_researcher_registration', False)
            },
            'notifications': {
                'system_notifications': settings.get('system_notifications', True),
                'notification_types': settings.get('notification_types', [
                    'record_access', 'record_share', 'system_update'
                ])
            }
        }
        
        # 添加是否为公开设置的标记
        settings_visibility = {}
        for setting in stored_settings:
            settings_visibility[setting.key] = setting.is_public
        
        return jsonify({
            'success': True,
            'data': {
                'settings': grouped_settings,
                'raw_settings': settings,
                'settings_visibility': settings_visibility
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统设置失败: {str(e)}'
        }), 500

# 更新系统设置
@admin_bp.route('/settings', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_system_settings():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少设置数据'
            }), 400
            
        from ..models.system_settings import SystemSetting
        
        updated_settings = []
        errors = []
        
        # 记录设置变更内容以便日志记录
        setting_changes = {}
        
        # 检查是否有可见性设置的更改
        visibility_changes = False
        if 'visibility' in data:
            visibility_data = data.pop('visibility')
            if isinstance(visibility_data, dict):
                for key, is_public in visibility_data.items():
                    setting = SystemSetting.query.filter_by(key=key).first()
                    if setting and setting.is_public != is_public:
                        setting.is_public = is_public
                        visibility_changes = True
                        setting_changes[f'{key}_visibility'] = {
                            'old': not is_public,
                            'new': is_public
                        }
        
        # 遍历提交的设置并更新
        for key, value in data.items():
            try:
                # 确定值类型
                value_type = 'string'
                if isinstance(value, bool):
                    value_type = 'bool'
                    value = str(value).lower()
                elif isinstance(value, int):
                    value_type = 'int'
                    value = str(value)
                elif isinstance(value, float):
                    value_type = 'float'
                    value = str(value)
                elif isinstance(value, (dict, list)):
                    value_type = 'json'
                    value = json.dumps(value)
                
                # 查找现有设置或创建新设置
                setting = SystemSetting.query.filter_by(key=key).first()
                
                if setting:
                    # 记录变更信息
                    old_value = setting.value
                    if old_value != value:
                        setting_changes[key] = {
                            'old': old_value,
                            'new': value
                        }
                    
                    # 更新现有设置
                    setting.value = value
                    setting.value_type = value_type
                    setting.updated_at = datetime.now()
                    setting.updated_by = current_user.id
                else:
                    # 创建新设置
                    setting = SystemSetting(
                        key=key,
                        value=value,
                        value_type=value_type,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        is_public=False  # 默认新设置为非公开
                    )
                    db.session.add(setting)
                    
                    # 记录新增设置
                    setting_changes[key] = {
                        'old': None,
                        'new': value,
                        'is_new': True
                    }
                    
                updated_settings.append(key)
            
            except Exception as e:
                errors.append({
                    'key': key,
                    'error': str(e)
                })
        
        db.session.commit()
        
        # 刷新应用配置以应用更改
        from ..utils.settings_utils import apply_settings
        apply_settings()
        
        # 记录系统设置更新日志
        if setting_changes:
            log_admin(
                message=f'管理员更新了系统设置',
                details=json.dumps({
                    'changes': setting_changes,
                    'visibility_changes': visibility_changes,
                    'admin_username': current_user.username,
                    'ip_address': request.remote_addr,
                    'updated_at': datetime.now().isoformat()
                })
            )
        
        # 如果有错误，返回部分成功的响应
        if errors:
            return jsonify({
                'success': True,
                'message': f'部分设置更新成功，{len(errors)}个设置更新失败',
                'data': {
                    'updated': updated_settings,
                    'errors': errors
                }
            }), 207
        
        return jsonify({
            'success': True,
            'message': f'成功更新{len(updated_settings)}个系统设置',
            'data': {
                'updated': updated_settings
            }
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新系统设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新系统设置失败: {str(e)}'
        }), 500

# 获取管理员仪表盘数据
@admin_bp.route('/dashboard', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def admin_dashboard():
    try:
        # 获取各种统计数据
        from ..models.user import User
        from ..models import QueryHistory, HealthRecord, SharedRecord
        from ..models.system_settings import SystemSetting
        from ..models.log import SystemLog
        from ..utils.mongo_utils import mongo
        from datetime import datetime, timedelta
        import json
        
        # 系统概览数据
        system_overview = {
            'total_users': User.query.count(),
            'total_records': mongo.db.health_records.count_documents({}),
            'total_shared_records': SharedRecord.query.count(),
            'total_queries': QueryHistory.query.count()
        }
        
        # 用户类型分布
        user_distribution = db.session.query(
            User.role, 
            func.count().label('count')
        ).group_by(User.role).all()
        
        role_data = {str(role.value): count for role, count in user_distribution}
        
        # 获取最近的活动
        # 1. 最近的系统日志
        recent_logs = SystemLog.query.order_by(SystemLog.created_at.desc()).limit(5).all()
        
        # 2. 最近的用户注册
        recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
        
        # 3. 最近的查询历史
        recent_queries = QueryHistory.query.order_by(QueryHistory.query_time.desc()).limit(5).all()
        
        # 健康记录随时间变化
        now = datetime.now()
        timeline_data = []
        
        # 过去30天的每日健康记录数量
        for i in range(30, 0, -1):
            date = now - timedelta(days=i)
            date_str = date.strftime('%Y-%m-%d')
            
            # 计算该日的记录数量
            count = mongo.db.health_records.count_documents({
                'record_date': {
                    '$gte': datetime(date.year, date.month, date.day, 0, 0, 0),
                    '$lt': datetime(date.year, date.month, date.day, 23, 59, 59)
                }
            })
            
            timeline_data.append({
                'date': date_str,
                'count': count
            })
        
        # 获取最近错误日志计数
        error_count = SystemLog.query.filter_by(
            log_type='error'
        ).filter(
            SystemLog.created_at >= (now - timedelta(days=1))
        ).count()
            
        # 系统警报
        alerts = []
            
        if error_count > 10:
            alerts.append({
                'type': 'error',
                'message': f'过去24小时内发生了{error_count}个错误',
                'details': '请检查系统日志以获取详细信息'
            })
        
        # PIR使用情况
        pir_enabled = current_app.config.get('PIR_ENABLE_OBFUSCATION', False)
        pir_query_count = QueryHistory.query.filter_by(is_anonymous=True).count()
        
        return jsonify({
            'success': True,
            'data': {
                'system_overview': system_overview,
                'user_distribution': role_data,
                'recent_activity': {
                    'logs': [log.to_dict() for log in recent_logs],
                    'users': [user.to_dict() for user in recent_users],
                    'queries': [query.to_dict() for query in recent_queries]
                },
                'timeline_data': timeline_data,
                'system_status': {
                    'error_count_24h': error_count,
                    'pir_enabled': pir_enabled,
                    'pir_query_count': pir_query_count
                },
                'alerts': alerts
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取管理员仪表盘数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取管理员仪表盘数据失败: {str(e)}'
        }), 500
    
# 添加这些路由到合适的位置
@admin_bp.route('/institutions', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_institutions():
    """获取所有医疗机构列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '')
        
        # 构建查询
        query = Institution.query
        
        # 应用搜索过滤
        if search:
            query = query.filter(Institution.name.contains(search) | 
                                 Institution.code.contains(search) | 
                                 Institution.address.contains(search))
        
        # 获取分页结果
        pagination = query.order_by(Institution.id.desc()).paginate(page=page, per_page=per_page)
        institutions = pagination.items
        
        # 格式化响应
        result = {
            'institutions': [inst.to_dict() for inst in institutions],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'pages': pagination.pages,
        }
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        current_app.logger.error(f"获取医疗机构列表出错: {str(e)}")
        return jsonify({'success': False, 'message': '获取医疗机构列表失败'}), 500

@admin_bp.route('/institutions/<int:institution_id>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_institution(institution_id):
    """获取单个医疗机构详情"""
    try:
        institution = Institution.query.get(institution_id)
        if not institution:
            return jsonify({'success': False, 'message': '医疗机构不存在'}), 404
            
        return jsonify({'success': True, 'data': institution.to_dict()})
    except Exception as e:
        current_app.logger.error(f"获取医疗机构详情出错: {str(e)}")
        return jsonify({'success': False, 'message': '获取医疗机构详情失败'}), 500

@admin_bp.route('/institutions', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def create_institution():
    """创建新医疗机构"""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '请求数据无效'}), 400
            
        # 验证必填字段
        if not data.get('name'):
            return jsonify({'success': False, 'message': '机构名称不能为空'}), 400
            
        # 检查名称是否已存在
        existing = Institution.query.filter_by(name=data.get('name')).first()
        if existing:
            return jsonify({'success': False, 'message': '该机构名称已存在'}), 400
            
        # 创建新机构
        institution = Institution(
            name=data.get('name'),
            code=data.get('code'),
            address=data.get('address'),
            phone=data.get('phone'),
            email=data.get('email'),
            website=data.get('website'),
            description=data.get('description'),
            logo_url=data.get('logo_url'),
            is_active=data.get('is_active', True)
        )
        
        db.session.add(institution)
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员创建了新的医疗机构: {institution.name}"
        add_system_log(LogType.ADMIN, log_message, details=f"医疗机构ID: {institution.id}")
        
        return jsonify({'success': True, 'data': institution.to_dict(), 'message': '医疗机构创建成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建医疗机构出错: {str(e)}")
        return jsonify({'success': False, 'message': '创建医疗机构失败'}), 500

@admin_bp.route('/institutions/<int:institution_id>', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_institution(institution_id):
    """更新医疗机构信息"""
    try:
        institution = Institution.query.get(institution_id)
        if not institution:
            return jsonify({'success': False, 'message': '医疗机构不存在'}), 404
            
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '请求数据无效'}), 400
            
        # 检查名称是否已存在（如果要修改名称）
        if data.get('name') and data.get('name') != institution.name:
            existing = Institution.query.filter_by(name=data.get('name')).first()
            if existing:
                return jsonify({'success': False, 'message': '该机构名称已存在'}), 400
        
        # 更新字段
        if data.get('name'):
            institution.name = data.get('name')
        if 'code' in data:
            institution.code = data.get('code')
        if 'address' in data:
            institution.address = data.get('address')
        if 'phone' in data:
            institution.phone = data.get('phone')
        if 'email' in data:
            institution.email = data.get('email')
        if 'website' in data:
            institution.website = data.get('website')
        if 'description' in data:
            institution.description = data.get('description')
        if 'logo_url' in data:
            institution.logo_url = data.get('logo_url')
        if 'is_active' in data:
            institution.is_active = data.get('is_active')
            
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员更新了医疗机构信息: {institution.name}"
        add_system_log(LogType.ADMIN, log_message, details=f"医疗机构ID: {institution.id}")
        
        return jsonify({'success': True, 'data': institution.to_dict(), 'message': '医疗机构更新成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新医疗机构出错: {str(e)}")
        return jsonify({'success': False, 'message': '更新医疗机构失败'}), 500

@admin_bp.route('/institutions/<int:institution_id>', methods=['DELETE'])
@api_login_required
@role_required(Role.ADMIN)
def delete_institution(institution_id):
    """删除医疗机构"""
    try:
        institution = Institution.query.get(institution_id)
        if not institution:
            return jsonify({'success': False, 'message': '医疗机构不存在'}), 404
            
        # 记录操作信息用于日志
        institution_name = institution.name
        
        # 删除机构
        db.session.delete(institution)
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员删除了医疗机构: {institution_name}"
        add_system_log(LogType.ADMIN, log_message, details=f"医疗机构ID: {institution_id}")
        
        return jsonify({'success': True, 'message': '医疗机构删除成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除医疗机构出错: {str(e)}")
        return jsonify({'success': False, 'message': '删除医疗机构失败'}), 500

# 记录类型管理API
@admin_bp.route('/record-types', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_record_types():
    """获取所有记录类型列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '')
        
        # 构建查询
        query = CustomRecordType.query
        
        # 应用搜索过滤
        if search:
            query = query.filter(CustomRecordType.name.contains(search) | 
                                 CustomRecordType.code.contains(search) | 
                                 CustomRecordType.description.contains(search))
        
        # 获取分页结果
        pagination = query.order_by(CustomRecordType.id.desc()).paginate(page=page, per_page=per_page)
        record_types = pagination.items
        
        # 格式化响应
        result = {
            'record_types': [rt.to_dict() for rt in record_types],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'pages': pagination.pages,
        }
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        current_app.logger.error(f"获取记录类型列表出错: {str(e)}")
        return jsonify({'success': False, 'message': '获取记录类型列表失败'}), 500

@admin_bp.route('/record-types/<int:type_id>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_record_type(type_id):
    """获取单个记录类型详情"""
    try:
        record_type = CustomRecordType.query.get(type_id)
        if not record_type:
            return jsonify({'success': False, 'message': '记录类型不存在'}), 404
            
        return jsonify({'success': True, 'data': record_type.to_dict()})
    except Exception as e:
        current_app.logger.error(f"获取记录类型详情出错: {str(e)}")
        return jsonify({'success': False, 'message': '获取记录类型详情失败'}), 500

@admin_bp.route('/record-types', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def create_record_type():
    """创建新记录类型"""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '请求数据无效'}), 400
            
        # 验证必填字段
        if not data.get('name') or not data.get('code'):
            return jsonify({'success': False, 'message': '类型名称和代码不能为空'}), 400
            
        # 检查代码是否已存在
        existing = CustomRecordType.query.filter_by(code=data.get('code')).first()
        if existing:
            return jsonify({'success': False, 'message': '该类型代码已存在'}), 400
            
        # 创建新记录类型
        record_type = CustomRecordType(
            name=data.get('name'),
            code=data.get('code'),
            description=data.get('description'),
            color=data.get('color'),
            icon=data.get('icon'),
            is_active=data.get('is_active', True)
        )
        
        db.session.add(record_type)
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员创建了新的记录类型: {record_type.name}"
        add_system_log(LogType.ADMIN, log_message, details=f"记录类型ID: {record_type.id}")
        
        return jsonify({'success': True, 'data': record_type.to_dict(), 'message': '记录类型创建成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建记录类型出错: {str(e)}")
        return jsonify({'success': False, 'message': '创建记录类型失败'}), 500

@admin_bp.route('/record-types/<int:type_id>', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_record_type(type_id):
    """更新记录类型信息"""
    try:
        record_type = CustomRecordType.query.get(type_id)
        if not record_type:
            return jsonify({'success': False, 'message': '记录类型不存在'}), 404
            
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '请求数据无效'}), 400
            
        # 检查代码是否已存在（如果要修改代码）
        if data.get('code') and data.get('code') != record_type.code:
            existing = CustomRecordType.query.filter_by(code=data.get('code')).first()
            if existing:
                return jsonify({'success': False, 'message': '该类型代码已存在'}), 400
        
        # 更新字段
        if data.get('name'):
            record_type.name = data.get('name')
        if data.get('code'):
            record_type.code = data.get('code')
        if 'description' in data:
            record_type.description = data.get('description')
        if 'color' in data:
            record_type.color = data.get('color')
        if 'icon' in data:
            record_type.icon = data.get('icon')
        if 'is_active' in data:
            record_type.is_active = data.get('is_active')
            
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员更新了记录类型信息: {record_type.name}"
        add_system_log(LogType.ADMIN, log_message, details=f"记录类型ID: {record_type.id}")
        
        return jsonify({'success': True, 'data': record_type.to_dict(), 'message': '记录类型更新成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新记录类型出错: {str(e)}")
        return jsonify({'success': False, 'message': '更新记录类型失败'}), 500

@admin_bp.route('/record-types/<int:type_id>', methods=['DELETE'])
@api_login_required
@role_required(Role.ADMIN)
def delete_record_type(type_id):
    """删除记录类型"""
    try:
        record_type = CustomRecordType.query.get(type_id)
        if not record_type:
            return jsonify({'success': False, 'message': '记录类型不存在'}), 404
            
        # 记录操作信息用于日志
        type_name = record_type.name
        
        # 删除记录类型
        db.session.delete(record_type)
        db.session.commit()
        
        # 记录操作日志
        log_message = f"管理员删除了记录类型: {type_name}"
        add_system_log(LogType.ADMIN, log_message, details=f"记录类型ID: {type_id}")
        
        return jsonify({'success': True, 'message': '记录类型删除成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除记录类型出错: {str(e)}")
        return jsonify({'success': False, 'message': '删除记录类型失败'}), 500

# 获取支持的导出类型和格式
@admin_bp.route('/export/options', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_export_options():
    try:
        # 定义系统支持的导出类型
        export_types = [
            {
                'value': 'users',
                'label': '用户数据',
                'description': '导出系统中的所有用户信息',
                'formats': ['json', 'csv', 'excel'],
                'icon': 'user'
            },
            {
                'value': 'health_records',
                'label': '健康记录',
                'description': '导出患者健康记录数据',
                'formats': ['json', 'csv', 'excel'],
                'icon': 'file-text'
            },
            {
                'value': 'system_logs',
                'label': '系统日志',
                'description': '导出系统操作日志',
                'formats': ['json', 'csv'],
                'icon': 'history'
            },
            {
                'value': 'medications',
                'label': '药物数据',
                'description': '导出药物处方与用药记录',
                'formats': ['json', 'csv', 'excel'],
                'icon': 'medicine-box'
            },
            {
                'value': 'vitals',
                'label': '生命体征',
                'description': '导出生命体征数据',
                'formats': ['json', 'csv', 'excel'],
                'icon': 'experiment'
            },
            {
                'value': 'labs',
                'label': '实验室项目',
                'description': '导出实验室项目数据',
                'formats': ['json', 'csv', 'excel'],
                'icon': 'experiment'
            }
        ]
        
        # 支持的导出格式
        export_formats = [
            {
                'value': 'json',
                'label': 'JSON',
                'description': 'JSON格式，适合进一步处理和分析',
                'mime_type': 'application/json',
                'icon': 'code'
            },
            {
                'value': 'csv',
                'label': 'CSV',
                'description': 'CSV格式，可在Excel等电子表格软件中打开',
                'mime_type': 'text/csv',
                'icon': 'file-text'
            },
            {
                'value': 'excel',
                'label': 'Excel',
                'description': 'Excel格式，直接以电子表格形式打开',
                'mime_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'icon': 'file-excel'
            },
            {
                'value': 'xml',
                'label': 'XML',
                'description': 'XML格式，结构化数据格式',
                'mime_type': 'application/xml',
                'icon': 'code'
            }
        ]
        
        # 支持的选项
        export_options = [
            {
                'value': 'anonymize',
                'label': '匿名化数据',
                'description': '移除或模糊化个人敏感信息'
            }
        ]
        
        # 获取最近使用的导出类型
        recent_types = (ExportTask.query
            .with_entities(ExportTask.export_type, func.count(ExportTask.id).label('count'))
            .filter(ExportTask.user_id == current_user.id)
            .group_by(ExportTask.export_type)
            .order_by(desc('count'))
            .limit(3)
            .all())
            
        recent_export_types = [t[0] for t in recent_types]
        
        return jsonify({
            'success': True,
            'data': {
                'exportTypes': export_types,
                'exportFormats': export_formats,
                'exportOptions': export_options,
                'recentTypes': recent_export_types
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取导出选项失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取导出选项失败: {str(e)}'
        }), 500
