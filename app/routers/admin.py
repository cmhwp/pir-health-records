from flask import Blueprint, request, jsonify, current_app, send_from_directory
from flask_login import login_required, current_user
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo
from ..routers.auth import role_required, api_login_required
from sqlalchemy import or_, and_
import os
import json
import time
from datetime import datetime, timedelta
from sqlalchemy.sql import func, distinct, desc

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
    from ..models.log import SystemLog, LogType
    import json
    log = SystemLog(
        user_id=current_user.id,
        log_type=LogType.ADMIN,
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
        }),
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None
    )
    db.session.add(log)
    db.session.commit()
    
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
    from ..models.log import SystemLog, LogType
    import json
    log = SystemLog(
        user_id=current_user.id,
        log_type=LogType.ADMIN,
        message=f'管理员查看了用户详情: {user.username}',
        details=json.dumps({
            'viewed_user_id': user.id,
            'username': user.username,
            'role': str(user.role),
            'admin_username': current_user.username,
            'ip_address': request.remote_addr
        }),
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None
    )
    db.session.add(log)
    db.session.commit()
    
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
        
        # 记录用户创建日志
        from ..models.log import SystemLog, LogType
        import json
        log = SystemLog(
            user_id=current_user.id,
            log_type=LogType.ADMIN,
            message=f'管理员创建了新用户: {user.username}',
            details=json.dumps({
                'created_user_id': user.id,
                'username': user.username,
                'email': user.email,
                'role': str(user.role),
                'admin_username': current_user.username,
                'ip_address': request.remote_addr
            }),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )
        db.session.add(log)
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
        from ..models.log import SystemLog, LogType
        import json
        
        # 找出发生变化的字段
        changes = {}
        for key in old_data:
            if old_data[key] != new_data[key]:
                changes[key] = {
                    'old': old_data[key],
                    'new': new_data[key]
                }
        
        log = SystemLog(
            user_id=current_user.id,
            log_type=LogType.ADMIN,
            message=f'管理员更新了用户信息: {user.username}',
            details=json.dumps({
                'user_id': user.id,
                'username': user.username,
                'changes': changes,
                'admin_username': current_user.username,
                'ip_address': request.remote_addr
            }),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )
        db.session.add(log)
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
        # 保存用户信息以便日志记录
        deleted_user_info = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': str(user.role),
            'created_at': user.created_at.isoformat() if user.created_at else None
        }
        
        db.session.delete(user)
        db.session.commit()
        
        # 记录用户删除日志
        from ..models.log import SystemLog, LogType
        import json
        log = SystemLog(
            user_id=current_user.id,
            log_type=LogType.ADMIN,
            message=f'管理员删除了用户: {deleted_user_info["username"]}',
            details=json.dumps({
                'deleted_user_id': deleted_user_info['id'],
                'username': deleted_user_info['username'],
                'email': deleted_user_info['email'],
                'role': deleted_user_info['role'],
                'admin_username': current_user.username,
                'ip_address': request.remote_addr
            }),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )
        db.session.add(log)
        db.session.commit()
        
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
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': str(user.role.value),
            'activity_count': activity_count
        } for user, activity_count in active_users]
        
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

# 批量管理记录
@admin_bp.route('/records/batch', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def batch_manage_records():
    try:
        data = request.json
        
        if not data or 'action' not in data or 'record_ids' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数 (action, record_ids)'
            }), 400
            
        action = data['action']
        record_ids = data['record_ids']
        
        if not record_ids or not isinstance(record_ids, list):
            return jsonify({
                'success': False,
                'message': 'record_ids必须是非空列表'
            }), 400
            
        # 根据不同的操作执行不同的逻辑
        if action == 'delete':
            # 标记删除记录
            from ..models.health_records import format_mongo_id
            from ..utils.mongo_utils import get_mongo_db
            
            mongo_db = get_mongo_db()
            deleted_count = 0
            deleted_records = []
            
            for record_id in record_ids:
                mongo_id = format_mongo_id(record_id)
                if not mongo_id:
                    continue
                    
                # 获取记录
                record = mongo_db.health_records.find_one({'_id': mongo_id})
                if not record:
                    continue
                
                # 记录删除信息以便日志记录
                deleted_records.append({
                    'record_id': str(record['_id']),
                    'patient_id': record.get('patient_id'),
                    'record_type': record.get('record_type'),
                    'record_date': str(record.get('record_date'))
                })
                    
                # 备份到删除集合
                record['deletion_time'] = datetime.now()
                record['deleted_by'] = current_user.id
                record['deletion_reason'] = data.get('reason', '管理员批量删除')
                
                mongo_db.health_records_deleted.insert_one(record)
                mongo_db.health_records.delete_one({'_id': mongo_id})
                
                deleted_count += 1
                
            # 记录批量删除操作的日志
            from ..models.log import SystemLog, LogType
            import json
            log = SystemLog(
                user_id=current_user.id,
                log_type=LogType.ADMIN,
                message=f'管理员批量删除健康记录',
                details=json.dumps({
                    'action': 'delete',
                    'deleted_count': deleted_count,
                    'record_ids': record_ids,
                    'deleted_records': deleted_records,
                    'reason': data.get('reason', '管理员批量删除'),
                    'admin_username': current_user.username,
                    'ip_address': request.remote_addr
                }),
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None
            )
            db.session.add(log)
            db.session.commit()
                
            return jsonify({
                'success': True,
                'message': f'成功删除{deleted_count}条记录',
                'data': {
                    'deleted_count': deleted_count
                }
            })
        
        elif action == 'visibility':
            # 修改记录可见性
            if 'visibility' not in data:
                return jsonify({
                    'success': False,
                    'message': '缺少visibility参数'
                }), 400
                
            visibility = data['visibility']
            
            # 验证可见性值
            from ..models import RecordVisibility
            try:
                RecordVisibility(visibility)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': f'无效的可见性值: {visibility}'
                }), 400
                
            # 执行批量更新
            from ..models.health_records import bulk_update_visibility
            
            updated_count = bulk_update_visibility(record_ids, visibility, current_user.id)
            
            # 记录批量更新可见性的日志
            from ..models.log import SystemLog, LogType
            import json
            log = SystemLog(
                user_id=current_user.id,
                log_type=LogType.ADMIN,
                message=f'管理员批量更新记录可见性',
                details=json.dumps({
                    'action': 'visibility',
                    'updated_count': updated_count,
                    'record_ids': record_ids,
                    'new_visibility': visibility,
                    'admin_username': current_user.username,
                    'ip_address': request.remote_addr
                }),
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None
            )
            db.session.add(log)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'批量更新可见性成功，共更新{updated_count}条记录',
                'data': {
                    'updated_count': updated_count
                }
            })
            
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的操作: {action}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"批量管理记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量管理记录失败: {str(e)}'
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
        
        # 创建导出目录
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exports')
        os.makedirs(export_dir, exist_ok=True)
        
        # 生成导出文件名
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"system_export_{export_type}_{timestamp}.json"
        filepath = os.path.join(export_dir, filename)
        
        # 导出信息以便日志记录
        export_info = {
            'export_type': export_type,
            'filename': filename,
            'timestamp': datetime.now().isoformat(),
            'parameters': data
        }
        
        # 根据类型导出不同的数据
        if export_type == 'users':
            # 导出用户数据
            users = User.query.all()
            export_data = [user.to_dict() for user in users]
            export_info['record_count'] = len(export_data)
            
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
            
            # 处理ObjectId格式
            for record in export_data:
                record['_id'] = str(record['_id'])
                
            export_info['record_count'] = len(export_data)
            export_info['limit'] = limit
            
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
            export_info['record_count'] = len(export_data)
            
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的导出类型: {export_type}'
            }), 400
            
        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)
            
        # 记录数据导出日志
        from ..models.log import SystemLog, LogType
        log = SystemLog(
            user_id=current_user.id,
            log_type=LogType.ADMIN,
            message=f'管理员导出{export_type}数据',
            details=json.dumps({
                'export_info': export_info,
                'admin_username': current_user.username,
                'file_size': os.path.getsize(filepath),
                'ip_address': request.remote_addr
            }),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )
        db.session.add(log)
        db.session.commit()
            
        # 返回下载链接
        download_url = f"/api/admin/export/download/{filename}"
        
        return jsonify({
            'success': True,
            'message': f'成功导出{export_type}数据',
            'data': {
                'filename': filename,
                'download_url': download_url,
                'record_count': len(export_data)
            }
        })
    except Exception as e:
        current_app.logger.error(f"导出系统数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导出系统数据失败: {str(e)}'
        }), 500

# 下载导出的数据
@admin_bp.route('/export/download/<filename>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def download_exported_data(filename):
    try:
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exports')
        
        file_path = os.path.join(export_dir, filename)
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': '文件不存在'
            }), 404
            
        # 记录下载行为
        from ..models.log import SystemLog, LogType
        log = SystemLog(
            user_id=current_user.id,
            log_type=LogType.ADMIN,
            message=f'管理员下载导出文件: {filename}',
            details=json.dumps({
                'filename': filename,
                'file_size': os.path.getsize(file_path),
                'download_time': datetime.now().isoformat(),
                'admin_username': current_user.username,
                'ip_address': request.remote_addr
            }),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )
        db.session.add(log)
        db.session.commit()
        
        return send_from_directory(export_dir, filename, as_attachment=True)
    except Exception as e:
        current_app.logger.error(f"下载导出数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'下载导出数据失败: {str(e)}'
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
                'max_export_size': settings.get('max_export_size', 1000)
            },
            'notifications': {
                'email_notifications': settings.get('email_notifications', True),
                'system_notifications': settings.get('system_notifications', True),
                'notification_types': settings.get('notification_types', [
                    'record_access', 'record_share', 'system_update'
                ])
            }
        }
        
        return jsonify({
            'success': True,
            'data': {
                'settings': grouped_settings,
                'raw_settings': settings
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
                        updated_by=current_user.id
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
        
        # 记录系统设置更新日志
        if setting_changes:
            from ..models.log import SystemLog, LogType
            log = SystemLog(
                user_id=current_user.id,
                log_type=LogType.ADMIN,
                message=f'管理员更新了系统设置',
                details=json.dumps({
                    'changes': setting_changes,
                    'admin_username': current_user.username,
                    'ip_address': request.remote_addr,
                    'updated_at': datetime.now().isoformat()
                }),
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None
            )
            db.session.add(log)
            db.session.commit()
        
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
