from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, desc, or_, and_, case, cast, Float
from datetime import datetime, timedelta
from bson.objectid import ObjectId
import json
import csv
import io
import random
import numpy as np
from collections import defaultdict
import base64
import hashlib

from ..models import db, User, Role, HealthRecord, RecordType, RecordVisibility
from ..models.role_models import ResearcherInfo
from ..models.health_records import mongo_health_record_to_dict, get_mongo_health_record
from ..models.researcher import ResearchProject, ProjectTeamMember, ProjectStatus
from ..models.institution import CustomRecordType
from ..routers.auth import role_required
from ..utils.mongo_utils import mongo, get_mongo_db
from ..utils.log_utils import log_record, log_research, log_pir
from ..utils.pir_utils import PIRQuery, prepare_pir_database, parse_encrypted_query_id, find_similar_records
from ..utils.experiment_utils import (
    PIRProtocolType, 
    PIRPerformanceMetric, 
    generate_mock_health_data, 
    configure_pir_protocol,
    execute_pir_query_experiment,
    analyze_experiment_results
)

# 创建研究人员路由蓝图
researcher_bp = Blueprint('researcher', __name__, url_prefix='/api/researcher')

# 研究人员控制台
@researcher_bp.route('/dashboard', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_researcher_dashboard():
    try:
        # 获取研究者信息
        researcher_info = current_user.researcher_info if hasattr(current_user, 'researcher_info') else None
        
        # 获取研究者可访问的记录数量
        total_accessible_records = HealthRecord.query.filter(
            HealthRecord.visibility == RecordVisibility.RESEARCHER
        ).count()
        
        # 获取最近查询历史
        recent_queries = db.session.query(
            func.count().label('query_count')
        ).filter(
            HealthRecord.doctor_id == current_user.id,
            HealthRecord.created_at >= datetime.now() - timedelta(days=30)
        ).scalar() or 0
        
        # 获取研究员的研究项目
        projects = ResearchProject.get_projects_by_researcher(current_user.id)
        recent_projects = [project.to_dict() for project in projects[:3]]  # 最近3个项目
        
        # 统计进行中的项目数量
        active_projects_count = sum(1 for p in projects if p.status == ProjectStatus.IN_PROGRESS)
        
        # 返回研究员控制台数据
        return jsonify({
            'success': True,
            'message': '研究员控制台数据',
            'data': {
                'researcher': {
                    'id': current_user.id,
                    'name': current_user.full_name,
                    'institution': researcher_info.institution if researcher_info else None,
                    'department': researcher_info.department if researcher_info else None,
                    'research_area': researcher_info.research_area if researcher_info else None
                },
                'statistics': {
                    'accessible_records': total_accessible_records,
                    'recent_queries': recent_queries,
                    'total_projects': len(projects),
                    'active_projects': active_projects_count
                },
                'recent_projects': recent_projects
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取研究员控制台数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取研究员控制台数据失败: {str(e)}'
        }), 500

# 获取所有项目状态
@researcher_bp.route('/project-statuses', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_project_statuses():
    try:
        # 返回所有可用的项目状态
        statuses = [
            {'key': status.name, 'value': status.value}
            for status in ProjectStatus
        ]
        
        return jsonify({
            'success': True,
            'message': '获取项目状态列表成功',
            'data': statuses
        })
        
    except Exception as e:
        current_app.logger.error(f"获取项目状态列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取项目状态列表失败: {str(e)}'
        }), 500

# 获取可访问的健康记录列表
@researcher_bp.route('/records', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_accessible_records():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 查询研究员可访问的记录（已标记为研究员可见的记录）
        query = HealthRecord.query.filter(
            HealthRecord.visibility == RecordVisibility.RESEARCHER
        )
        
        # 按记录类型筛选
        record_type = request.args.get('record_type')
        if record_type:
            # 直接使用字符串类型进行筛选
            query = query.filter(HealthRecord.record_type == record_type)
        
        # 按关键词搜索
        keyword = request.args.get('keyword')
        if keyword:
            query = query.filter(or_(
                HealthRecord.title.ilike(f'%{keyword}%'),
                HealthRecord.description.ilike(f'%{keyword}%')
            ))
            
        # 分页
        pagination = query.order_by(desc(HealthRecord.created_at)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        records = pagination.items
        
        # 获取MongoDB中的详细记录
        mongo_ids = [str(record.mongo_id) for record in records]
        mongo_records = {}
        
        if mongo_ids:
            for mongo_id in mongo_ids:
                try:
                    mongo_record = get_mongo_health_record(mongo_id)
                    if mongo_record:
                        mongo_records[mongo_id] = mongo_health_record_to_dict(mongo_record)
                except Exception as e:
                    current_app.logger.error(f"获取MongoDB记录失败: {str(e)}")
        
        # 获取记录类型名称映射
        record_types_dict = {}
        try:
            all_record_types = CustomRecordType.query.all()
            record_types_dict = {rt.code: rt.name for rt in all_record_types}
        except Exception as rt_err:
            current_app.logger.error(f"获取记录类型信息失败: {str(rt_err)}")
        
        # 格式化记录
        formatted_records = []
        for record in records:
            mongo_id = str(record.mongo_id)
            mongo_data = mongo_records.get(mongo_id, {})
            
            # 获取记录类型名称
            record_type_name = record_types_dict.get(record.record_type, record.record_type)
            
            # 现在record_type已经是字符串，不再是枚举
            record_type_value = record.record_type
            
            # 检查MongoDB数据中是否有record_type字段
            mongo_record_type = mongo_data.get('record_type')
            if mongo_record_type:
                record_type_value = mongo_record_type
            
            formatted_record = {
                'id': record.id,
                'mongo_id': mongo_id,
                'title': record.title,
                'record_type': record_type_value,
                'record_type_name': record_type_name,
                'patient_id': record.patient_id,
                'doctor_id': record.doctor_id,
                'record_date': mongo_data.get('record_date').isoformat() if mongo_data.get('record_date') and hasattr(mongo_data.get('record_date'), 'isoformat') else mongo_data.get('record_date'),   
                'created_at': record.created_at.isoformat(),
                'updated_at': record.updated_at.isoformat() if record.updated_at else None,
                'visibility': record.visibility.value,
                'is_encrypted': record.is_encrypted,
                'pir_protected': mongo_data.get('pir_protected', False),
                'description': mongo_data.get('description'),
                'doctor_name': mongo_data.get('doctor_name'),
                'patient_name': mongo_data.get('patient_name', '匿名患者')
            }
            
            formatted_records.append(formatted_record)
        
        # 记录研究员查询记录列表日志
        log_research(
            message=f'研究员{current_user.full_name}查询了健康记录列表',
            details={
                'researcher_id': current_user.id,
                'query_params': {
                    'page': page,
                    'per_page': per_page,
                    'record_type': record_type,
                    'keyword': keyword
                },
                'record_count': len(formatted_records),
                'query_time': datetime.now().isoformat()
            }
        )
        
        return jsonify({
            'success': True,
            'message': '获取可访问的健康记录列表成功',
            'data': {
                'records': formatted_records,
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取可访问的健康记录列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取可访问的健康记录列表失败: {str(e)}'
        }), 500

# 获取特定健康记录详情
@researcher_bp.route('/records/<record_id>', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_record_details(record_id):
    try:
        # 从MySQL获取基本记录信息
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查记录是否对研究员可见
        if record.visibility != RecordVisibility.RESEARCHER:
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 从MongoDB获取详细记录
        mongo_id = str(record.mongo_id)
        mongo_record = get_mongo_health_record(mongo_id)
        
        if not mongo_record:
            return jsonify({
                'success': False,
                'message': '记录详情不存在'
            }), 404
        
        # 转换MongoDB记录为字典
        record_data = mongo_health_record_to_dict(mongo_record)
        
        # 获取患者和医生信息
        patient = User.query.get(record.patient_id)
        doctor = User.query.get(record.doctor_id)
        
        # 移除敏感的患者身份信息，仅保留匿名化的数据
        if 'patient_name' in record_data:
            record_data['patient_name'] = '匿名患者'
        
        # 添加额外的记录元数据
        record_data.update({
            'id': record.id,
            'mongo_id': mongo_id,
            'patient_id': record.patient_id,
            'doctor_id': record.doctor_id,
            'doctor_name': doctor.full_name if doctor else "未知医生",
            'created_at': record.created_at.isoformat(),
            'updated_at': record.updated_at.isoformat() if record.updated_at else None,
            'visibility': record.visibility.value,
        })
        
        # 保留MongoDB中的原始record_type值，如果存在的话
        if 'record_type' in mongo_record and mongo_record['record_type']:
            record_data['record_type'] = mongo_record['record_type']
        else:
            # 直接使用record_type字符串
            record_data['record_type'] = record.record_type
        
        # 记录研究员查看记录日志
        log_research(
            message=f'研究员{current_user.full_name}查看了健康记录详情',
            details={
                'researcher_id': current_user.id,
                'record_id': record_id,
                'mongo_id': mongo_id,
                'access_time': datetime.now().isoformat()
            }
        )
        
        return jsonify({
            'success': True,
            'message': '获取记录详情成功',
            'data': record_data
        })
        
    except Exception as e:
        current_app.logger.error(f"获取记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录详情失败: {str(e)}'
        }), 500

# 导出匿名化的健康记录数据（CSV格式）
@researcher_bp.route('/export/records', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def export_anonymized_records():
    try:
        # 查询研究员可访问的记录（已标记为研究员可见的记录）
        records = HealthRecord.query.filter(
            HealthRecord.visibility == RecordVisibility.RESEARCHER
        ).order_by(desc(HealthRecord.created_at)).all()
        
        # 准备CSV输出
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入表头
        writer.writerow([
            '记录ID', '类型', '创建日期', '诊断', '医生部门', '患者性别', 
            '患者年龄组', '主要症状', '治疗方法', '药物'
        ])
        
        # 获取记录类型名称映射
        record_types_dict = {}
        try:
            all_record_types = CustomRecordType.query.all()
            record_types_dict = {rt.code: rt.name for rt in all_record_types}
        except Exception as rt_err:
            current_app.logger.error(f"获取记录类型信息失败: {str(rt_err)}")
        
        # 收集匿名化数据
        for record in records:
            try:
                mongo_id = str(record.mongo_id)
                mongo_record = get_mongo_health_record(mongo_id)
                
                if not mongo_record:
                    continue
                    
                # 转换MongoDB记录为字典
                record_data = mongo_health_record_to_dict(mongo_record)
                
                # 获取患者信息（用于年龄组和性别）
                patient = User.query.get(record.patient_id)
                patient_info = patient.patient_info if patient and hasattr(patient, 'patient_info') else None
                
                # 计算年龄组（如果有出生日期）
                age_group = '未知'
                if patient_info and patient_info.date_of_birth:
                    today = datetime.now()
                    birth_date = patient_info.date_of_birth
                    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
                    
                    if age < 18:
                        age_group = '0-18'
                    elif age < 30:
                        age_group = '19-30'
                    elif age < 45:
                        age_group = '31-45'
                    elif age < 60:
                        age_group = '46-60'
                    else:
                        age_group = '60+'
                
                # 获取性别（匿名化）
                gender = patient_info.gender if patient_info else '未知'
                
                # 获取记录类型显示名称
                record_type_name = record_types_dict.get(record.record_type, record.record_type)
                
                # 写入行数据
                writer.writerow([
                    record.id,
                    record_type_name,
                    record.created_at.strftime('%Y-%m-%d'),
                    record_data.get('diagnosis', ''),
                    record_data.get('department', ''),
                    gender,
                    age_group,
                    record_data.get('symptoms', ''),
                    record_data.get('treatment', ''),
                    record_data.get('medications', '')
                ])
                
            except Exception as e:
                current_app.logger.error(f"导出记录 {record.id} 失败: {str(e)}")
                continue
        
        # 记录研究员导出数据日志
        log_research(
            message=f'研究员{current_user.full_name}导出了匿名化健康记录数据',
            details={
                'researcher_id': current_user.id,
                'record_count': len(records),
                'export_time': datetime.now().isoformat()
            }
        )
        
        # 获取CSV内容并返回
        csv_content = output.getvalue()
        output.close()
        
        response = current_app.response_class(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=anonymized_health_records.csv'}
        )
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"导出匿名化健康记录数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导出匿名化健康记录数据失败: {str(e)}'
        }), 500

# 获取统计数据 - 按记录类型
@researcher_bp.route('/statistics/record-types', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_record_type_statistics():
    try:
        # 查询每种记录类型的数量
        query_result = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.visibility == RecordVisibility.RESEARCHER
        ).group_by(
            HealthRecord.record_type
        ).all()
        
        # 获取记录类型信息以显示名称
        record_types_dict = {}
        try:
            all_record_types = CustomRecordType.query.all()
            record_types_dict = {rt.code: rt.name for rt in all_record_types}
        except Exception as rt_err:
            current_app.logger.error(f"获取记录类型信息失败: {str(rt_err)}")
        
        # 格式化结果
        stats = []
        for record_type, count in query_result:
            # 获取记录类型名称（如果有）
            type_name = record_types_dict.get(record_type, record_type)
            stats.append({
                'record_type': record_type,
                'record_type_name': type_name,
                'count': count
            })
        
        return jsonify({
            'success': True,
            'message': '获取记录类型统计成功',
            'data': stats
        })
        
    except Exception as e:
        current_app.logger.error(f"获取记录类型统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录类型统计失败: {str(e)}'
        }), 500

# 获取统计数据 - 按时间分布
@researcher_bp.route('/statistics/time-distribution', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_time_distribution():
    try:
        # 获取参数
        interval = request.args.get('interval', 'month')
        limit = min(request.args.get('limit', 12, type=int), 24)  # 限制最大值
        
        # 根据间隔设置日期格式和时间范围
        if interval == 'day':
            # MySQL 使用 DATE() 提取日期部分
            time_expr = func.DATE(HealthRecord.created_at)
            time_range = datetime.now() - timedelta(days=limit)
        elif interval == 'week':
            # MySQL 使用 YEARWEEK() 获取年-周
            time_expr = func.concat(
                func.year(HealthRecord.created_at), 
                '-', 
                func.lpad(func.week(HealthRecord.created_at), 2, '0')
            )
            time_range = datetime.now() - timedelta(weeks=limit)
        else:  # 默认按月
            # MySQL 使用 DATE_FORMAT() 格式化日期
            time_expr = func.date_format(HealthRecord.created_at, '%Y-%m')
            time_range = datetime.now() - timedelta(days=30 * limit)
        
        # 查询每个时间间隔的记录数量
        query_result = db.session.query(
            time_expr.label('time_period'),
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.visibility == RecordVisibility.RESEARCHER,
            HealthRecord.created_at >= time_range
        ).group_by(
            'time_period'
        ).order_by(
            'time_period'
        ).all()
        
        # 格式化结果
        stats = [
            {'time_period': time_period, 'count': count}
            for time_period, count in query_result
        ]
        
        return jsonify({
            'success': True,
            'message': '获取时间分布统计成功',
            'data': {
                'interval': interval,
                'stats': stats
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取时间分布统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取时间分布统计失败: {str(e)}'
        }), 500
        
# 研究项目相关 API
@researcher_bp.route('/studies', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_researcher_studies():
    try:
        # 从数据库获取当前研究员的所有项目
        projects = ResearchProject.get_projects_by_researcher(current_user.id)
        
        # 转换为字典列表
        projects_list = [project.to_dict() for project in projects]
        
        return jsonify({
            'success': True,
            'message': '获取研究项目列表成功',
            'data': projects_list
        })
    except Exception as e:
        current_app.logger.error(f"获取研究项目列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取研究项目列表失败: {str(e)}'
        }), 500

# 创建研究项目
@researcher_bp.route('/studies', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def create_study():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供研究项目数据'
            }), 400
            
        # 验证必填字段
        required_fields = ['title', 'description', 'start_date', 'end_date']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'message': f'缺少必填字段: {field}'
                }), 400
        
        # 处理项目状态
        status = ProjectStatus.PLANNING  # 默认为计划中
        if 'status' in data and data['status']:
            try:
                # 尝试从枚举值获取状态
                status = ProjectStatus(data['status'])
            except ValueError:
                # 如果无效，则尝试从值名称获取状态
                try:
                    status = next((s for s in ProjectStatus if s.value == data['status']), ProjectStatus.PLANNING)
                except:
                    status = ProjectStatus.PLANNING
        
        # 创建新项目
        new_project = ResearchProject(
            title=data.get('title'),
            description=data.get('description'),
            status=status,
            start_date=datetime.strptime(data.get('start_date'), '%Y-%m-%d').date(),
            end_date=datetime.strptime(data.get('end_date'), '%Y-%m-%d').date(),
            participants=data.get('participants', 0),
            researcher_id=current_user.id
        )
        
        # 保存到数据库
        db.session.add(new_project)
        db.session.commit()
        
        # 记录创建研究项目日志
        log_research(
            message=f'研究员{current_user.full_name}创建了研究项目: {data.get("title")}',
            details={
                'researcher_id': current_user.id,
                'study_id': new_project.id,
                'title': new_project.title,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        project_dict = new_project.to_dict()
        # 将枚举转换为字符串
        project_dict['status'] = new_project.status.value if new_project.status else None
        
        return jsonify({
            'success': True,
            'message': '研究项目创建成功',
            'data': project_dict
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建研究项目失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建研究项目失败: {str(e)}'
        }), 500

# 更新研究项目
@researcher_bp.route('/studies/<int:study_id>', methods=['PUT'])
@login_required
@role_required(Role.RESEARCHER)
def update_study(study_id):
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供更新数据'
            }), 400
        
        # 获取指定ID的项目，并验证所有权
        project = ResearchProject.get_project_by_id(study_id, current_user.id)
        if not project:
            return jsonify({
                'success': False,
                'message': '项目不存在或无权限'
            }), 404
        
        # 更新字段
        updated_fields = []
        for field in ['title', 'description', 'participants']:
            if field in data and data[field] is not None:
                setattr(project, field, data[field])
                updated_fields.append(field)
        
        # 单独处理状态字段
        if 'status' in data and data['status'] is not None:
            try:
                # 尝试从枚举值获取状态
                status = ProjectStatus(data['status'])
                project.status = status
                updated_fields.append('status')
            except ValueError:
                # 如果无效，则尝试从值名称获取状态
                try:
                    status = next((s for s in ProjectStatus if s.value == data['status']), None)
                    if status:
                        project.status = status
                        updated_fields.append('status')
                except:
                    pass
        
        # 单独处理日期字段
        if 'start_date' in data and data['start_date']:
            project.start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
            updated_fields.append('start_date')
            
        if 'end_date' in data and data['end_date']:
            project.end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
            updated_fields.append('end_date')
        
        # 保存到数据库
        db.session.commit()
        
        # 记录更新研究项目日志
        log_research(
            message=f'研究员{current_user.full_name}更新了研究项目: {project.title}',
            details={
                'researcher_id': current_user.id,
                'study_id': study_id,
                'updated_fields': updated_fields,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        project_dict = project.to_dict()
        # 将枚举转换为字符串
        project_dict['status'] = project.status.value if project.status else None
        
        return jsonify({
            'success': True,
            'message': '研究项目更新成功',
            'data': project_dict
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新研究项目失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新研究项目失败: {str(e)}'
        }), 500

# 删除研究项目
@researcher_bp.route('/studies/<int:study_id>', methods=['DELETE'])
@login_required
@role_required(Role.RESEARCHER)
def delete_study(study_id):
    try:
        # 获取指定ID的项目，并验证所有权
        project = ResearchProject.get_project_by_id(study_id, current_user.id)
        if not project:
            return jsonify({
                'success': False,
                'message': '项目不存在或无权限'
            }), 404
        
        # 保存项目标题用于日志
        project_title = project.title
        
        # 删除项目（团队成员会自动级联删除）
        db.session.delete(project)
        db.session.commit()
        
        # 记录删除研究项目日志
        log_research(
            message=f'研究员{current_user.full_name}删除了研究项目: {project_title}',
            details={
                'researcher_id': current_user.id,
                'study_id': study_id,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        return jsonify({
            'success': True,
            'message': '研究项目删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除研究项目失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除研究项目失败: {str(e)}'
        }), 500

# 获取研究项目详情
@researcher_bp.route('/studies/<int:study_id>', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_study_details(study_id):
    try:
        # 获取指定ID的项目
        project = ResearchProject.get_project_by_id(study_id)
        if not project:
            return jsonify({
                'success': False,
                'message': '项目不存在'
            }), 404
        
        # 检查权限（只有创建者或系统管理员可查看详情）
        if project.researcher_id != current_user.id and current_user.role != Role.ADMIN:
            return jsonify({
                'success': False,
                'message': '无权限查看此项目'
            }), 403
        
        # 获取项目详情
        project_dict = project.to_dict()
        # 将枚举转换为字符串
        project_dict['status'] = project.status.value if project.status else None
        
        # 在详情中添加进度信息（示例数据，实际应从数据库获取）
        project_dict['progress'] = {
            'milestone_1': '已完成',
            'milestone_2': '进行中',
            'milestone_3': '未开始'
        }
        
        # 记录查看研究项目日志
        log_research(
            message=f'研究员{current_user.full_name}查看了研究项目详情: {project.title}',
            details={
                'researcher_id': current_user.id,
                'study_id': study_id,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        return jsonify({
            'success': True,
            'message': '获取研究项目详情成功',
            'data': project_dict
        })
        
    except Exception as e:
        current_app.logger.error(f"获取研究项目详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取研究项目详情失败: {str(e)}'
        }), 500

# 添加研究项目团队成员
@researcher_bp.route('/studies/<int:study_id>/team-members', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def add_team_member(study_id):
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供团队成员数据'
            }), 400
            
        # 验证必填字段
        required_fields = ['name', 'role']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'message': f'缺少必填字段: {field}'
                }), 400
        
        # 获取指定ID的项目，并验证所有权
        project = ResearchProject.get_project_by_id(study_id, current_user.id)
        if not project:
            return jsonify({
                'success': False,
                'message': '项目不存在或无权限'
            }), 404
        
        # 创建新团队成员
        new_member = ProjectTeamMember(
            name=data.get('name'),
            role=data.get('role'),
            project_id=study_id
        )
        
        # 保存到数据库
        db.session.add(new_member)
        db.session.commit()
        
        # 记录添加团队成员日志
        log_research(
            message=f'研究员{current_user.full_name}向研究项目添加了团队成员: {data.get("name")}',
            details={
                'researcher_id': current_user.id,
                'study_id': study_id,
                'member_id': new_member.id,
                'member_name': new_member.name,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        return jsonify({
            'success': True,
            'message': '团队成员添加成功',
            'data': new_member.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"添加团队成员失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'添加团队成员失败: {str(e)}'
        }), 500

# 删除研究项目团队成员
@researcher_bp.route('/studies/<int:study_id>/team-members/<int:member_id>', methods=['DELETE'])
@login_required
@role_required(Role.RESEARCHER)
def delete_team_member(study_id, member_id):
    try:
        # 获取指定ID的项目，并验证所有权
        project = ResearchProject.get_project_by_id(study_id, current_user.id)
        if not project:
            return jsonify({
                'success': False,
                'message': '项目不存在或无权限'
            }), 404
        
        # 获取指定ID的团队成员
        member = ProjectTeamMember.query.get(member_id)
        if not member or member.project_id != study_id:
            return jsonify({
                'success': False,
                'message': '团队成员不存在或不属于此项目'
            }), 404
        
        # 保存成员姓名用于日志
        member_name = member.name
        
        # 删除团队成员
        db.session.delete(member)
        db.session.commit()
        
        # 记录删除团队成员日志
        log_research(
            message=f'研究员{current_user.full_name}从研究项目中删除了团队成员: {member_name}',
            details={
                'researcher_id': current_user.id,
                'study_id': study_id,
                'member_id': member_id,
                'member_name': member_name,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 返回成功结果
        return jsonify({
            'success': True,
            'message': '团队成员删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除团队成员失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除团队成员失败: {str(e)}'
        }), 500

# 批量PIR查询接口
@researcher_bp.route('/pir/batch-query', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def batch_pir_query():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供查询数据'
            }), 400
        
        # 获取查询参数
        encrypted_query_ids = data.get('encrypted_query_ids', [])
        pir_protocol = data.get('pir_protocol', 'SealPIR')  # 默认使用SealPIR协议
        
        if not encrypted_query_ids:
            return jsonify({
                'success': False,
                'message': '未提供加密查询ID'
            }), 400
        
        # 记录查询日志，隐藏实际查询内容
        log_pir(
            message=f'研究员{current_user.full_name}执行了批量PIR查询',
            details={
                'researcher_id': current_user.id,
                'pir_protocol': pir_protocol,
                'query_count': len(encrypted_query_ids),
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 从数据库获取健康记录
        health_records = list(get_mongo_db().health_records.find({'visibility': 'researcher'}))
        
        # 如果没有记录，返回空结果
        if not health_records:
            return jsonify({
                'success': True,
                'message': '查询执行成功，但没有可用记录',
                'data': {
                    'results': [],
                    'protocol': pir_protocol,
                    'total_queries': len(encrypted_query_ids)
                }
            })
        
        # 准备PIR数据库
        pir_db, record_mapping = prepare_pir_database(health_records)
        
        # 获取数据库维度
        db_size = len(health_records)
        
        # 执行PIR查询
        results = []
        for encrypted_id in encrypted_query_ids:
            try:
                # 使用解析函数解析加密ID
                target_index = parse_encrypted_query_id(encrypted_id) % db_size
                
                # 确保索引在有效范围内
                if target_index < 0 or target_index >= db_size:
                    target_index = random.randint(0, db_size - 1)
                
                # 创建查询向量 - 确保长度匹配
                query_vector = PIRQuery.create_query_vector(db_size, target_index)
                
                # 获取记录向量的形状以确保维度匹配
                record_shape = pir_db.shape
                current_app.logger.info(f"PIR数据库形状: {record_shape}, 查询向量形状: {query_vector.shape}")
                
                # 执行查询
                result = PIRQuery.process_query(pir_db, query_vector)
                
                # 编码结果
                result_str = base64.b64encode(str(result).encode()).decode()
                
                # 获取匹配的健康记录信息
                matched_record = record_mapping.get(target_index, {})
                # 移除敏感字段并转换ID
                record_id = matched_record.get('_id', '')
                if record_id:
                    record_id = str(record_id)
                
                results.append({
                    'encrypted_result': result_str,
                    'protocol': pir_protocol,
                    'matched_record_id': record_id,
                    'metadata': {
                        'timestamp': datetime.now().isoformat(),
                        'query_id': random.randint(10000, 99999)
                    }
                })
            except Exception as query_err:
                current_app.logger.error(f"单个PIR查询失败: {str(query_err)}")
                # 继续处理下一个查询，不中断整个过程
                results.append({
                    'encrypted_result': None,
                    'protocol': pir_protocol,
                    'error': str(query_err),
                    'metadata': {
                        'timestamp': datetime.now().isoformat(),
                        'query_id': random.randint(10000, 99999)
                    }
                })
        
        # 记录研究日志
        log_research(
            message=f'研究员{current_user.full_name}成功执行批量PIR查询',
            details={
                'researcher_id': current_user.id,
                'pir_protocol': pir_protocol,
                'result_count': len(results),
                'timestamp': datetime.now().isoformat()
            }
        )
        
        return jsonify({
            'success': True,
            'message': '批量PIR查询执行成功',
            'data': {
                'results': results,
                'protocol': pir_protocol,
                'total_queries': len(encrypted_query_ids)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"批量PIR查询失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量PIR查询失败: {str(e)}'
        }), 500

# 健康数据聚合统计
@researcher_bp.route('/stats/aggregate', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def aggregate_health_stats():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供统计参数'
            }), 400
        
        # 获取统计维度
        dimension = data.get('dimension')
        if not dimension:
            return jsonify({
                'success': False,
                'message': '未指定统计维度'
            }), 400
        
        # 获取其他参数
        sub_dimension = data.get('sub_dimension')  # 可选子维度，如"年龄段"
        metric = data.get('metric', 'count')  # 度量指标，默认为数量统计
        min_count = data.get('min_count', 100)  # 最小统计基数，默认100，防止识别特定个体
        
        # 获取过滤条件
        filters = data.get('filters', {})
        
        # 记录查询日志
        log_research(
            message=f'研究员{current_user.full_name}请求健康数据聚合统计',
            details={
                'researcher_id': current_user.id,
                'dimension': dimension,
                'sub_dimension': sub_dimension,
                'metric': metric,
                'filters': filters,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 基于维度选择查询方式
        results = {}
        
        if dimension == 'disease':
            results = _aggregate_by_disease(sub_dimension, metric, filters, min_count)
        elif dimension == 'age_group':
            results = _aggregate_by_age_group(sub_dimension, metric, filters, min_count)
        elif dimension == 'gender':
            results = _aggregate_by_gender(sub_dimension, metric, filters, min_count)
        elif dimension == 'region':
            results = _aggregate_by_region(sub_dimension, metric, filters, min_count)
        elif dimension == 'medication':
            results = _aggregate_by_medication(sub_dimension, metric, filters, min_count)
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的统计维度: {dimension}'
            }), 400
        
        # 记录统计结果日志
        log_research(
            message=f'研究员{current_user.full_name}获取了健康数据聚合统计结果',
            details={
                'researcher_id': current_user.id,
                'dimension': dimension,
                'result_count': len(results['data']) if 'data' in results else 0,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        return jsonify({
            'success': True,
            'message': '聚合统计成功',
            'data': results
        })
        
    except Exception as e:
        current_app.logger.error(f"健康数据聚合统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'健康数据聚合统计失败: {str(e)}'
        }), 500

# 获取研究员的项目统计信息
@researcher_bp.route('/statistics/projects', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_project_statistics():
    try:
        # 获取当前研究员的所有项目
        projects = ResearchProject.get_projects_by_researcher(current_user.id)
        
        # 按状态分组统计
        status_counts = {}
        for status in ProjectStatus:
            status_counts[status.value] = 0
            
        for project in projects:
            if project.status:
                status_counts[project.status.value] += 1
        
        # 近期项目（最近3个月创建的）
        recent_date = datetime.now() - timedelta(days=90)
        recent_projects_count = sum(1 for p in projects if p.created_at and p.created_at >= recent_date)
        
        # 即将结束的项目（一个月内结束）
        today = datetime.now().date()
        ending_soon_count = sum(1 for p in projects if p.end_date and (p.end_date - today).days <= 30 and p.status == ProjectStatus.IN_PROGRESS)
        
        # 参与者总数
        total_participants = sum(p.participants or 0 for p in projects)
        
        # 按月份统计项目数量趋势
        month_stats = {}
        for project in projects:
            if project.created_at:
                month_key = project.created_at.strftime('%Y-%m')
                if month_key not in month_stats:
                    month_stats[month_key] = 0
                month_stats[month_key] += 1
        
        # 转换为列表格式
        month_trend = [
            {'month': month, 'count': count}
            for month, count in sorted(month_stats.items())
        ]
        
        # 返回统计结果
        return jsonify({
            'success': True,
            'message': '获取项目统计信息成功',
            'data': {
                'total_projects': len(projects),
                'status_distribution': status_counts,
                'recent_projects': recent_projects_count,
                'ending_soon': ending_soon_count,
                'total_participants': total_participants,
                'monthly_trend': month_trend
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取项目统计信息失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取项目统计信息失败: {str(e)}'
        }), 500

# 按疾病聚合统计辅助函数
def _aggregate_by_disease(sub_dimension, metric, filters, min_count):
    # 只分析对研究人员可见的记录
    base_query = db.session.query(
        HealthRecord
    ).filter(
        HealthRecord.visibility == RecordVisibility.RESEARCHER
    )
    
    # 应用过滤条件
    if 'record_type' in filters:
        record_type_value = filters['record_type']
        if record_type_value:
            # 直接使用字符串类型筛选
            base_query = base_query.filter(HealthRecord.record_type == record_type_value)
    
    if 'date_range' in filters:
        date_range = filters['date_range']
        if 'start' in date_range:
            start_date = datetime.fromisoformat(date_range['start'])
            base_query = base_query.filter(HealthRecord.created_at >= start_date)
        if 'end' in date_range:
            end_date = datetime.fromisoformat(date_range['end'])
            base_query = base_query.filter(HealthRecord.created_at <= end_date)
    
    # 获取记录ID列表
    records = base_query.all()
    
    # 如果记录太少，不进行统计以保护隐私
    if len(records) < min_count:
        return {
            'dimension': 'disease',
            'message': f'统计群体太小（{len(records)} < {min_count}人），为保护隐私不返回结果',
            'count': len(records),
            'data': []
        }
    
    # 从MongoDB获取诊断信息
    mongo_db = get_mongo_db()
    diseases_stats = defaultdict(list)
    
    for record in records:
        try:
            mongo_id = str(record.mongo_id)
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
            
            if mongo_record and 'diagnosis' in mongo_record:
                diagnosis = mongo_record['diagnosis']
                diseases_stats[diagnosis].append(record)
                
                # 如果有度量指标，收集相关数据
                if metric != 'count' and metric in mongo_record:
                    try:
                        metric_value = float(mongo_record[metric])
                        diseases_stats[diagnosis + '_values'].append(metric_value)
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            current_app.logger.error(f"处理记录 {record.id} 失败: {str(e)}")
    
    # 准备结果
    results = []
    for disease, disease_records in diseases_stats.items():
        # 跳过非记录列表的键（如度量指标值列表）
        if disease.endswith('_values'):
            continue
            
        # 如果疾病样本数小于最小统计基数，不包括在结果中
        if len(disease_records) < min_count:
            continue
        
        result = {
            'disease': disease,
            'count': len(disease_records)
        }
        
        # 如果需要统计额外指标
        if metric != 'count':
            values = diseases_stats.get(disease + '_values', [])
            if values:
                # 添加差分隐私噪声以保护隐私
                epsilon = 0.1  # 差分隐私参数
                noise_scale = 1.0 / epsilon
                mean_value = np.mean(values) + np.random.laplace(0, noise_scale / len(values))
                std_value = np.std(values) + np.random.laplace(0, noise_scale / len(values))
                
                result['metric'] = metric
                result['mean'] = round(float(mean_value), 2)
                result['std'] = round(float(std_value), 2)
                result['confidence_interval'] = [
                    round(float(mean_value - 1.96 * std_value / np.sqrt(len(values))), 2),
                    round(float(mean_value + 1.96 * std_value / np.sqrt(len(values))), 2)
                ]
        
        # 如果需要按子维度分组
        if sub_dimension:
            sub_groups = _group_by_sub_dimension(disease_records, sub_dimension, min_count)
            if sub_groups:
                result['sub_groups'] = sub_groups
        
        results.append(result)
    
    return {
        'dimension': 'disease',
        'total_records': len(records),
        'total_diseases': len(results),
        'data': results
    }

# 按年龄组聚合
def _aggregate_by_age_group(sub_dimension, metric, filters, min_count):
    # 基础实现，类似于按疾病聚合
    age_groups = [
        {'name': '0-18', 'min': 0, 'max': 18},
        {'name': '19-30', 'min': 19, 'max': 30},
        {'name': '31-45', 'min': 31, 'max': 45},
        {'name': '46-60', 'min': 46, 'max': 60},
        {'name': '60+', 'min': 61, 'max': 120}
    ]
    
    # 查询患者信息以获取年龄
    records_by_age = defaultdict(list)
    
    # 获取可见记录
    base_query = db.session.query(
        HealthRecord, User
    ).join(
        User, User.id == HealthRecord.patient_id
    ).filter(
        HealthRecord.visibility == RecordVisibility.RESEARCHER
    )
    
    # 应用过滤条件
    if 'record_type' in filters:
        record_type_value = filters['record_type']
        if record_type_value:
            # 直接使用字符串类型筛选
            base_query = base_query.filter(HealthRecord.record_type == record_type_value)
    
    # 执行查询
    query_results = base_query.all()
    
    # 如果记录太少，不进行统计
    if len(query_results) < min_count:
        return {
            'dimension': 'age_group',
            'message': f'统计群体太小（{len(query_results)} < {min_count}人），为保护隐私不返回结果',
            'count': len(query_results),
            'data': []
        }
    
    # 按年龄组分类
    for record, user in query_results:
        if hasattr(user, 'patient_info') and user.patient_info and user.patient_info.date_of_birth:
            # 计算年龄
            today = datetime.now()
            birth_date = user.patient_info.date_of_birth
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            
            # 确定年龄组
            for group in age_groups:
                if group['min'] <= age <= group['max']:
                    records_by_age[group['name']].append(record)
                    break
    
    # 准备结果
    results = []
    for age_group, group_records in records_by_age.items():
        # 如果年龄组样本数小于最小统计基数，不包括在结果中
        if len(group_records) < min_count:
            continue
        
        result = {
            'age_group': age_group,
            'count': len(group_records)
        }
        
        # 如果需要按子维度分组
        if sub_dimension:
            sub_groups = _group_by_sub_dimension(group_records, sub_dimension, min_count)
            if sub_groups:
                result['sub_groups'] = sub_groups
        
        results.append(result)
    
    return {
        'dimension': 'age_group',
        'total_records': len(query_results),
        'total_groups': len(results),
        'data': results
    }

# 按性别聚合
def _aggregate_by_gender(sub_dimension, metric, filters, min_count):
    # 查询患者性别信息
    base_query = db.session.query(
        HealthRecord, User
    ).join(
        User, User.id == HealthRecord.patient_id
    ).filter(
        HealthRecord.visibility == RecordVisibility.RESEARCHER
    )
    
    # 应用过滤条件
    if 'record_type' in filters:
        record_type_value = filters['record_type']
        if record_type_value:
            # 直接使用字符串类型筛选
            base_query = base_query.filter(HealthRecord.record_type == record_type_value)
    
    # 执行查询
    query_results = base_query.all()
    
    # 如果记录太少，不进行统计
    if len(query_results) < min_count:
        return {
            'dimension': 'gender',
            'message': f'统计群体太小（{len(query_results)} < {min_count}人），为保护隐私不返回结果',
            'count': len(query_results),
            'data': []
        }
    
    # 按性别分类
    records_by_gender = defaultdict(list)
    for record, user in query_results:
        gender = '未知'
        if hasattr(user, 'patient_info') and user.patient_info:
            gender = user.patient_info.gender or '未知'
        
        records_by_gender[gender].append(record)
    
    # 准备结果
    results = []
    for gender, gender_records in records_by_gender.items():
        # 如果性别组样本数小于最小统计基数，不包括在结果中
        if len(gender_records) < min_count:
            continue
        
        result = {
            'gender': gender,
            'count': len(gender_records)
        }
        
        # 如果需要按子维度分组
        if sub_dimension:
            sub_groups = _group_by_sub_dimension(gender_records, sub_dimension, min_count)
            if sub_groups:
                result['sub_groups'] = sub_groups
        
        results.append(result)
    
    return {
        'dimension': 'gender',
        'total_records': len(query_results),
        'total_groups': len(results),
        'data': results
    }

# 按地区聚合
def _aggregate_by_region(sub_dimension, metric, filters, min_count):
    # 查询患者地址信息
    base_query = db.session.query(
        HealthRecord, User
    ).join(
        User, User.id == HealthRecord.patient_id
    ).filter(
        HealthRecord.visibility == RecordVisibility.RESEARCHER
    )
    
    # 应用过滤条件
    if 'record_type' in filters:
        record_type_value = filters['record_type']
        if record_type_value:
            # 直接使用字符串类型筛选
            base_query = base_query.filter(HealthRecord.record_type == record_type_value)
    
    # 执行查询
    query_results = base_query.all()
    
    # 如果记录太少，不进行统计
    if len(query_results) < min_count:
        return {
            'dimension': 'region',
            'message': f'统计群体太小（{len(query_results)} < {min_count}人），为保护隐私不返回结果',
            'count': len(query_results),
            'data': []
        }
    
    # 按地区分类
    records_by_region = defaultdict(list)
    for record, user in query_results:
        region = '未知'
        if hasattr(user, 'patient_info') and user.patient_info and user.patient_info.address:
            # 简单地从地址中提取省份作为地区
            address = user.patient_info.address
            for province in ['北京', '上海', '广东', '江苏', '浙江', '四川', '湖北', '湖南', '河南', '河北']:
                if province in address:
                    region = province
                    break
        
        records_by_region[region].append(record)
    
    # 准备结果
    results = []
    for region, region_records in records_by_region.items():
        # 如果地区样本数小于最小统计基数，不包括在结果中
        if len(region_records) < min_count:
            continue
        
        result = {
            'region': region,
            'count': len(region_records)
        }
        
        # 如果需要按子维度分组
        if sub_dimension:
            sub_groups = _group_by_sub_dimension(region_records, sub_dimension, min_count)
            if sub_groups:
                result['sub_groups'] = sub_groups
        
        results.append(result)
    
    return {
        'dimension': 'region',
        'total_records': len(query_results),
        'total_groups': len(results),
        'data': results
    }

# 按药物聚合
def _aggregate_by_medication(sub_dimension, metric, filters, min_count):
    # 只分析对研究人员可见的记录
    base_query = db.session.query(
        HealthRecord
    ).filter(
        HealthRecord.visibility == RecordVisibility.RESEARCHER
    )
    
    # 应用过滤条件
    if 'record_type' in filters:
        record_type_value = filters['record_type']
        if record_type_value:
            # 直接使用字符串类型筛选
            base_query = base_query.filter(HealthRecord.record_type == record_type_value)
    
    # 获取记录ID列表
    records = base_query.all()
    
    # 如果记录太少，不进行统计
    if len(records) < min_count:
        return {
            'dimension': 'medication',
            'message': f'统计群体太小（{len(records)} < {min_count}人），为保护隐私不返回结果',
            'count': len(records),
            'data': []
        }
    
    # 从MongoDB获取药物信息
    mongo_db = get_mongo_db()
    meds_stats = defaultdict(list)
    
    for record in records:
        try:
            mongo_id = str(record.mongo_id)
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
            
            if mongo_record and 'medications' in mongo_record:
                medications = mongo_record['medications']
                # 处理可能的药物列表格式
                if isinstance(medications, list):
                    for med in medications:
                        if isinstance(med, dict) and 'name' in med:
                            meds_stats[med['name']].append(record)
                        elif isinstance(med, str):
                            meds_stats[med].append(record)
                elif isinstance(medications, str):
                    # 尝试拆分多个药物
                    for med in medications.split(','):
                        med = med.strip()
                        if med:
                            meds_stats[med].append(record)
        except Exception as e:
            current_app.logger.error(f"处理记录 {record.id} 失败: {str(e)}")
    
    # 准备结果
    results = []
    for medication, med_records in meds_stats.items():
        # 如果药物样本数小于最小统计基数，不包括在结果中
        if len(med_records) < min_count:
            continue
        
        result = {
            'medication': medication,
            'count': len(med_records)
        }
        
        # 如果需要按子维度分组
        if sub_dimension:
            sub_groups = _group_by_sub_dimension(med_records, sub_dimension, min_count)
            if sub_groups:
                result['sub_groups'] = sub_groups
        
        results.append(result)
    
    return {
        'dimension': 'medication',
        'total_records': len(records),
        'total_medications': len(results),
        'data': results
    }

# 辅助函数：按子维度分组
def _group_by_sub_dimension(records, sub_dimension, min_count):
    """
    将记录按子维度进行分组统计
    
    参数:
        records: 记录列表
        sub_dimension: 子维度名称
        min_count: 最小统计基数
        
    返回:
        子维度分组统计结果列表
    """
    if not records or not sub_dimension or len(records) < min_count:
        return []
    
    # 从MongoDB获取详细记录
    mongo_db = get_mongo_db()
    
    if sub_dimension == 'age_group':
        return _sub_group_by_age(records, mongo_db, min_count)
    elif sub_dimension == 'gender':
        return _sub_group_by_gender(records, mongo_db, min_count)
    elif sub_dimension == 'disease':
        return _sub_group_by_disease(records, mongo_db, min_count)
    elif sub_dimension == 'record_type':
        return _sub_group_by_record_type(records, min_count)
    elif sub_dimension == 'time_period':
        return _sub_group_by_time_period(records, min_count)
    elif sub_dimension == 'medication':
        return _sub_group_by_medication(records, mongo_db, min_count)
    elif sub_dimension == 'doctor_department':
        return _sub_group_by_doctor_department(records, mongo_db, min_count)
    else:
        return []

# 按年龄段子分组
def _sub_group_by_age(records, mongo_db, min_count):
    age_groups = [
        {'name': '0-18', 'min': 0, 'max': 18},
        {'name': '19-30', 'min': 19, 'max': 30},
        {'name': '31-45', 'min': 31, 'max': 45},
        {'name': '46-60', 'min': 46, 'max': 60},
        {'name': '60+', 'min': 61, 'max': 120}
    ]
    
    # 按年龄组分类
    records_by_age = defaultdict(list)
    
    for record in records:
        # 获取患者信息
        patient = User.query.get(record.patient_id)
        if not patient or not hasattr(patient, 'patient_info') or not patient.patient_info or not patient.patient_info.date_of_birth:
            continue
            
        # 计算年龄
        today = datetime.now()
        birth_date = patient.patient_info.date_of_birth
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        
        # 确定年龄组
        for group in age_groups:
            if group['min'] <= age <= group['max']:
                records_by_age[group['name']].append(record)
                break
    
    # 准备结果
    results = []
    for age_group, age_records in records_by_age.items():
        # 如果年龄组样本数小于最小统计基数，不包括在结果中
        if len(age_records) < min_count:
            continue
        
        results.append({
            'name': age_group,
            'count': len(age_records),
            'percentage': round(len(age_records) / len(records) * 100, 2)
        })
    
    return results

# 按性别子分组
def _sub_group_by_gender(records, mongo_db, min_count):
    # 按性别分类
    records_by_gender = defaultdict(list)
    
    for record in records:
        # 获取患者信息
        patient = User.query.get(record.patient_id)
        gender = '未知'
        if patient and hasattr(patient, 'patient_info') and patient.patient_info:
            gender = patient.patient_info.gender or '未知'
        
        records_by_gender[gender].append(record)
    
    # 准备结果
    results = []
    for gender, gender_records in records_by_gender.items():
        # 如果性别组样本数小于最小统计基数，不包括在结果中
        if len(gender_records) < min_count:
            continue
        
        results.append({
            'name': gender,
            'count': len(gender_records),
            'percentage': round(len(gender_records) / len(records) * 100, 2)
        })
    
    return results

# 按疾病子分组
def _sub_group_by_disease(records, mongo_db, min_count):
    # 按疾病分类
    records_by_disease = defaultdict(list)
    
    for record in records:
        try:
            mongo_id = str(record.mongo_id)
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
            
            if mongo_record and 'diagnosis' in mongo_record:
                diagnosis = mongo_record['diagnosis']
                records_by_disease[diagnosis].append(record)
        except Exception as e:
            current_app.logger.error(f"处理记录 {record.id} 子分组失败: {str(e)}")
    
    # 准备结果
    results = []
    for disease, disease_records in records_by_disease.items():
        # 如果疾病样本数小于最小统计基数，不包括在结果中
        if len(disease_records) < min_count:
            continue
        
        results.append({
            'name': disease,
            'count': len(disease_records),
            'percentage': round(len(disease_records) / len(records) * 100, 2)
        })
    
    return results

# 按记录类型子分组
def _sub_group_by_record_type(records, min_count):
    # 按记录类型分类
    records_by_type = defaultdict(list)
    
    # 获取记录类型名称映射
    record_types_dict = {}
    try:
        all_record_types = CustomRecordType.query.all()
        record_types_dict = {rt.code: rt.name for rt in all_record_types}
    except Exception as rt_err:
        current_app.logger.error(f"获取记录类型信息失败: {str(rt_err)}")
    
    for record in records:
        # 记录类型现在是字符串
        record_type = record.record_type
        # 获取显示名称
        type_name = record_types_dict.get(record_type, record_type)
        records_by_type[type_name].append(record)
    
    # 准备结果
    results = []
    for record_type, type_records in records_by_type.items():
        # 如果记录类型样本数小于最小统计基数，不包括在结果中
        if len(type_records) < min_count:
            continue
        
        results.append({
            'name': record_type,
            'count': len(type_records),
            'percentage': round(len(type_records) / len(records) * 100, 2)
        })
    
    return results

# 按时间段子分组
def _sub_group_by_time_period(records, min_count):
    # 获取时间范围
    if not records:
        return []
        
    # 所有记录按创建时间排序
    sorted_records = sorted(records, key=lambda r: r.created_at)
    earliest = sorted_records[0].created_at
    latest = sorted_records[-1].created_at
    
    # 计算合适的时间间隔（按月、按季度或按年）
    time_range = (latest - earliest).days
    
    time_periods = []
    
    if time_range <= 90:  # 少于3个月，按周分组
        # 按周分组
        period_start = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
        period_start = period_start - timedelta(days=period_start.weekday())  # 调整到周一
        
        while period_start <= latest:
            period_end = period_start + timedelta(days=6, hours=23, minutes=59, seconds=59)  # 周日结束
            period_name = f"{period_start.strftime('%Y-%m-%d')}至{period_end.strftime('%Y-%m-%d')}"
            time_periods.append({
                'name': period_name,
                'start': period_start,
                'end': period_end
            })
            period_start = period_start + timedelta(days=7)
    elif time_range <= 730:  # 少于2年，按月分组
        # 按月分组
        period_start = earliest.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        while period_start <= latest:
            # 计算月末
            if period_start.month == 12:
                period_end = period_start.replace(year=period_start.year+1, month=1, day=1) - timedelta(seconds=1)
            else:
                period_end = period_start.replace(month=period_start.month+1, day=1) - timedelta(seconds=1)
                
            period_name = period_start.strftime('%Y-%m')
            time_periods.append({
                'name': period_name,
                'start': period_start,
                'end': period_end
            })
            
            # 下一个月
            if period_start.month == 12:
                period_start = period_start.replace(year=period_start.year+1, month=1)
            else:
                period_start = period_start.replace(month=period_start.month+1)
    else:  # 超过2年，按年分组
        # 按年分组
        period_start = earliest.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        
        while period_start <= latest:
            period_end = period_start.replace(year=period_start.year+1) - timedelta(seconds=1)
            period_name = period_start.strftime('%Y年')
            time_periods.append({
                'name': period_name,
                'start': period_start,
                'end': period_end
            })
            period_start = period_start.replace(year=period_start.year+1)
    
    # 按时间段分类记录
    records_by_period = defaultdict(list)
    
    for record in records:
        for period in time_periods:
            if period['start'] <= record.created_at <= period['end']:
                records_by_period[period['name']].append(record)
                break
    
    # 准备结果
    results = []
    for period_name, period_records in records_by_period.items():
        # 如果时间段样本数小于最小统计基数，不包括在结果中
        if len(period_records) < min_count:
            continue
        
        results.append({
            'name': period_name,
            'count': len(period_records),
            'percentage': round(len(period_records) / len(records) * 100, 2)
        })
    
    # 按时间顺序排序
    return sorted(results, key=lambda x: x['name'])

# 按药物子分组
def _sub_group_by_medication(records, mongo_db, min_count):
    # 按药物分类
    records_by_medication = defaultdict(list)
    
    for record in records:
        try:
            mongo_id = str(record.mongo_id)
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
            
            if mongo_record and 'medications' in mongo_record:
                medications = mongo_record['medications']
                # 处理可能的药物列表格式
                if isinstance(medications, list):
                    for med in medications:
                        if isinstance(med, dict) and 'name' in med:
                            records_by_medication[med['name']].append(record)
                        elif isinstance(med, str):
                            records_by_medication[med].append(record)
                elif isinstance(medications, str):
                    # 尝试拆分多个药物
                    for med in medications.split(','):
                        med = med.strip()
                        if med:
                            records_by_medication[med].append(record)
        except Exception as e:
            current_app.logger.error(f"处理记录 {record.id} 药物子分组失败: {str(e)}")
    
    # 准备结果
    results = []
    for medication, med_records in records_by_medication.items():
        # 如果药物样本数小于最小统计基数，不包括在结果中
        if len(med_records) < min_count:
            continue
        
        results.append({
            'name': medication,
            'count': len(med_records),
            'percentage': round(len(med_records) / len(records) * 100, 2)
        })
    
    return results

# 按医生部门子分组
def _sub_group_by_doctor_department(records, mongo_db, min_count):
    # 按医生部门分类
    records_by_department = defaultdict(list)
    
    for record in records:
        try:
            # 获取医生信息
            doctor = User.query.get(record.doctor_id)
            department = '未知'
            
            if doctor and hasattr(doctor, 'doctor_info') and doctor.doctor_info:
                department = doctor.doctor_info.department or '未知'
            
            # 也可以从MongoDB获取
            if department == '未知':
                mongo_id = str(record.mongo_id)
                mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
                
                if mongo_record and 'department' in mongo_record:
                    department = mongo_record['department']
            
            records_by_department[department].append(record)
        except Exception as e:
            current_app.logger.error(f"处理记录 {record.id} 部门子分组失败: {str(e)}")
    
    # 准备结果
    results = []
    for department, dept_records in records_by_department.items():
        # 如果部门样本数小于最小统计基数，不包括在结果中
        if len(dept_records) < min_count:
            continue
        
        results.append({
            'name': department,
            'count': len(dept_records),
            'percentage': round(len(dept_records) / len(records) * 100, 2)
        })
    
    return results

# 获取PIR查询匹配的记录详情
@researcher_bp.route('/pir/record/<record_id>', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_pir_record_detail(record_id):
    try:
        # 记录访问日志
        log_research(
            message=f'研究员{current_user.full_name}请求PIR记录详情',
            details={
                'researcher_id': current_user.id,
                'record_id': record_id,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # 获取MongoDB记录
        mongo_db = get_mongo_db()
        record = None
        
        try:
            # 尝试将ID转换为ObjectId
            object_id = ObjectId(record_id)
            record = mongo_db.health_records.find_one({'_id': object_id, 'visibility': 'researcher'})
        except:
            # 如果ID格式不是ObjectId，则尝试使用字符串ID查询
            record = mongo_db.health_records.find_one({'_id': record_id, 'visibility': 'researcher'})
        
        if not record:
            return jsonify({
                'success': False,
                'message': '未找到记录或无权访问'
            }), 404
        
        # 转换ID为字符串
        record['_id'] = str(record['_id'])
        
        # 处理日期字段
        for date_field in ['record_date', 'created_at', 'updated_at']:
            if date_field in record and record[date_field]:
                record[date_field] = record[date_field].isoformat() if hasattr(record[date_field], 'isoformat') else record[date_field]
        
        # 处理嵌套对象中的日期
        if 'medication' in record and record['medication']:
            for date_field in ['start_date', 'end_date']:
                if date_field in record['medication'] and record['medication'][date_field]:
                    record['medication'][date_field] = record['medication'][date_field].isoformat() if hasattr(record['medication'][date_field], 'isoformat') else record['medication'][date_field]
        
        if 'vital_signs' in record and record['vital_signs']:
            for vs in record['vital_signs']:
                if 'measured_at' in vs and vs['measured_at']:
                    vs['measured_at'] = vs['measured_at'].isoformat() if hasattr(vs['measured_at'], 'isoformat') else vs['measured_at']
        
        # 匿名化处理：移除或模糊化患者标识信息
        anonymized_record = anonymize_record(record)
        
        return jsonify({
            'success': True,
            'message': '获取记录成功',
            'data': anonymized_record
        })
        
    except Exception as e:
        current_app.logger.error(f"获取PIR记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录详情失败: {str(e)}'
        }), 500

# 匿名化健康记录
def anonymize_record(record):
    """匿名化健康记录，移除或模糊化患者标识信息"""
    anonymized = record.copy()
    
    # 移除或模糊化患者ID
    if 'patient_id' in anonymized:
        # 替换为哈希值
        patient_id = str(anonymized['patient_id'])
        anonymized['patient_id'] = hashlib.sha256(patient_id.encode()).hexdigest()[:8]
    
    # 模糊化患者姓名
    if 'patient_name' in anonymized and anonymized['patient_name']:
        name = anonymized['patient_name']
        if len(name) > 0:
            # 只保留姓氏首字母加*号
            anonymized['patient_name'] = name[0] + '*' * (len(name) - 1)
    
    # 移除个人联系方式
    for field in ['contact', 'phone', 'email', 'address']:
        if field in anonymized:
            anonymized[field] = '[已隐藏]'
    
    # 处理demographic字段
    if 'demographic' in anonymized and anonymized['demographic']:
        if 'address' in anonymized['demographic']:
            # 只保留省市级别信息
            address_parts = anonymized['demographic']['address'].split()
            if len(address_parts) > 2:
                anonymized['demographic']['address'] = ' '.join(address_parts[:2]) + ' [已隐藏详细地址]'
        
        # 模糊年龄为年龄段
        if 'age' in anonymized['demographic']:
            age = anonymized['demographic']['age']
            if isinstance(age, int) or age.isdigit():
                age = int(age)
                age_group = (age // 10) * 10
                anonymized['demographic']['age_group'] = f"{age_group}-{age_group+9}"
                anonymized['demographic'].pop('age', None)
    
    return anonymized

# 解密PIR查询结果
@researcher_bp.route('/pir/decrypt', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def decrypt_pir_result():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供解密数据'
            }), 400
        
        # 获取加密结果和其他参数
        encrypted_result = data.get('encrypted_result')
        record_id = data.get('record_id')
        decrypt_key = data.get('decrypt_key', '')
        
        if not encrypted_result:
            return jsonify({
                'success': False,
                'message': '未提供加密结果'
            }), 400
        
        # 记录日志
        log_research(
            message=f'研究员{current_user.full_name}请求解密PIR查询结果',
            details={
                'researcher_id': current_user.id,
                'record_id': record_id,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        try:
            # 解码Base64
            decoded_bytes = base64.b64decode(encrypted_result)
            decoded_str = decoded_bytes.decode('utf-8')
            
            # 尝试解析为数组
            import re
            import numpy as np
            
            # 清理字符串并提取数值
            if decoded_str.strip().startswith('[') and decoded_str.strip().endswith(']'):
                # 删除方括号
                clean_str = decoded_str.replace('[', '').replace(']', '')
                # 使用正则表达式提取所有数字
                numbers = re.findall(r'\d+', clean_str)
                array_data = [int(num) for num in numbers]
                
                # 获取记录数据以增强分析结果
                record_data = None
                record_features = None
                if record_id:
                    try:
                        # 获取记录详情
                        mongo_db = get_mongo_db()
                        record = mongo_db.health_records.find_one({'_id': ObjectId(record_id)})
                        if record:
                            # 提取有用信息
                            record_data = {
                                'id': record.get('_id', ''),
                                'title': record.get('title', ''),
                                'description': record.get('description', ''),
                                'record_type': record.get('record_type', ''),
                                'record_date': record.get('record_date').isoformat() if record.get('record_date') and hasattr(record.get('record_date'), 'isoformat') else record.get('record_date'),
                                'doctor_name': record.get('doctor_name', ''),
                                'institution': record.get('institution')
                            }
                            
                            # 提取记录特征数据
                            if record.get('record_type') == 'medication' and record.get('medication'):
                                record_features = {
                                    'medication_name': record.get('medication').get('medication_name'),
                                    'dosage': record.get('medication').get('dosage')
                                }
                            elif record.get('record_type') == 'vital_signs' and record.get('vital_signs'):
                                record_features = {
                                    'vital_signs': [
                                        {'type': vs.get('type'), 'value': vs.get('value'), 'unit': vs.get('unit')} 
                                        for vs in record.get('vital_signs')
                                    ]
                                }
                    except Exception as e:
                        current_app.logger.error(f"获取记录详情失败: {str(e)}")
                
                # 分析数组数据
                analysis_result = {
                    'data_type': 'array',
                    'length': len(array_data),
                    'mean': float(np.mean(array_data)) if len(array_data) > 0 else 0,
                    'median': float(np.median(array_data)) if len(array_data) > 0 else 0,
                    'max': max(array_data) if len(array_data) > 0 else 0,
                    'min': min(array_data) if len(array_data) > 0 else 0,
                    'std_dev': float(np.std(array_data)) if len(array_data) > 0 else 0,
                    'non_zero_count': len([x for x in array_data if x != 0]),
                    'data': array_data
                }
                
                # 添加特征分析信息
                analysis_result['feature_analysis'] = {
                    'vector_dimension': len(array_data),
                    'statistical_properties': {
                        'mean': float(np.mean(array_data)) if len(array_data) > 0 else 0,
                        'median': float(np.median(array_data)) if len(array_data) > 0 else 0,
                        'std_dev': float(np.std(array_data)) if len(array_data) > 0 else 0,
                        'max': max(array_data) if len(array_data) > 0 else 0,
                        'min': min(array_data) if len(array_data) > 0 else 0
                    },
                    'pattern_analysis': {
                        'zero_ratio': len([x for x in array_data if x == 0]) / len(array_data) if len(array_data) > 0 else 0,
                        'positive_ratio': len([x for x in array_data if x > 0]) / len(array_data) if len(array_data) > 0 else 0,
                        'negative_ratio': len([x for x in array_data if x < 0]) / len(array_data) if len(array_data) > 0 else 0,
                        'entropy': 4.5  # 示例值
                    }
                }
                
                # 添加相似记录信息
                analysis_result['similar_records'] = []
                
                # 如果有记录ID，尝试查找相似记录
                if record_id:
                    try:
                        # 查找与当前记录向量相似的健康记录
                        similar_records = find_similar_records(
                            vector=array_data, 
                            current_record_id=record_id,
                            max_results=5,
                            similarity_threshold=0.6
                        )
                        
                        if similar_records:
                            analysis_result['similar_records'] = similar_records
                        else:
                            analysis_result['similar_records'] = []
                            current_app.logger.info(f"未找到与记录ID {record_id} 相似的记录")
                    except Exception as e:
                        current_app.logger.error(f"获取相似记录失败: {str(e)}")
                        analysis_result['similar_records'] = []
                
                # 添加数据洞察
                record_type_value = record_data.get('record_type') if record_data else None
                analysis_result['pattern_recognition'] = generate_health_pattern_insight(array_data, record_type_value)
                
                # 添加记录数据
                if record_data:
                    analysis_result['record_data'] = record_data
                if record_features:
                    analysis_result['record_features'] = record_features
                
                return jsonify({
                    'success': True,
                    'message': '解密成功',
                    'data': {
                        'decrypted_result': decoded_str,
                        'analysis': analysis_result
                    }
                })
            else:
                # 如果不是数组，尝试解析为JSON
                try:
                    import json
                    json_data = json.loads(decoded_str)
                    return jsonify({
                        'success': True,
                        'message': '解密成功',
                        'data': {
                            'decrypted_result': decoded_str,
                            'analysis': {
                                'data_type': 'json',
                                'structure': type(json_data).__name__,
                                'data': json_data
                            }
                        }
                    })
                except:
                    # 既不是数组也不是JSON，返回原始解码字符串
                    return jsonify({
                        'success': True,
                        'message': '解密成功，但无法解析为结构化数据',
                        'data': {
                            'decrypted_result': decoded_str,
                            'analysis': {
                                'data_type': 'string',
                                'length': len(decoded_str)
                            }
                        }
                    })
        except Exception as decrypt_err:
            return jsonify({
                'success': False,
                'message': f'解密数据失败: {str(decrypt_err)}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"解密PIR查询结果失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'解密PIR查询结果失败: {str(e)}'
        }), 500

def generate_health_pattern_insight(vector, record_type=None, features=None):
    """
    基于特征向量生成健康数据的洞察
    
    Args:
        vector: 特征向量
        record_type: 记录类型
        features: 特征名称映射
        
    Returns:
        洞察文本的列表
    """
    insights = []
    
    if not vector or len(vector) == 0:
        return ["没有足够的数据进行分析"]
    
    # 计算基本统计数据
    import numpy as np
    
    mean = np.mean(vector)
    median = np.median(vector)
    std_dev = np.std(vector)
    max_val = np.max(vector)
    max_index = np.argmax(vector)
    min_val = np.min(vector)
    zeros = len([x for x in vector if x == 0])
    zero_ratio = zeros / len(vector)
    
    # 添加基本洞察
    insights.append(f"特征向量包含 {len(vector)} 个维度，其中 {zeros} 个为零值 ({zero_ratio*100:.1f}%)。")
    
    if max_val > mean + 2 * std_dev:
        insights.append(f"第 {max_index+1} 个特征值显著高于平均水平，这可能表示一个重要特征。")
    
    # 根据记录类型添加特定的洞察
    if record_type:
        if record_type.lower() in ['examination', 'vital_signs']:
            insights.append("该检查结果显示部分指标偏离正常范围，建议进一步咨询医生。")
        elif record_type.lower() in ['medical_history', 'diagnosis']:
            insights.append("该病历数据与同类病例有较高的相似度，可参考相似病例的治疗方案。")
        elif record_type.lower() == 'medication':
            insights.append("该药物处方数据表明治疗方案正在按预期进行。")
        elif record_type.lower() == 'general_checkup':
            insights.append("该体检数据表明大部分指标在正常范围内，请关注标记为异常的项目。")
    
    # 添加通用洞察
    if zero_ratio > 0.7:
        insights.append("数据中存在大量零值，表明特征分布相对稀疏。")
    elif zero_ratio < 0.3:
        insights.append("数据中的非零值比例较高，表明信息密度较大。")
    
    if std_dev / abs(mean) > 2:
        insights.append("数据波动性较大，各特征间差异明显。")
    else:
        insights.append("数据相对稳定，各特征值分布较为均匀。")
    
    return insights

@researcher_bp.route('/pir/decrypt-key/<record_id>', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_pir_decrypt_key(record_id):
    """获取PIR记录解密密钥"""
    try:
        # 查询记录
        record = HealthRecord.query.filter_by(mongo_id=record_id).first()
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
            
        # 检查可见性
        if record.visibility != RecordVisibility.RESEARCHER:
            return jsonify({
                'success': False,
                'message': '无权访问此记录'
            }), 403
            
        # 生成解密密钥 (这里示例使用记录ID的哈希值)
        import hashlib
        decrypt_key = hashlib.sha256(f"{record_id}_{record.id}".encode()).hexdigest()[:16]
        
        # 记录解密密钥获取操作
        log_research(
            message=f'研究员{current_user.full_name}获取了记录解密密钥',
            details={
                'researcher_id': current_user.id,
                'record_id': record_id,
                'request_time': datetime.now().isoformat()
            }
        )
        
        return jsonify({
            'success': True,
            'message': '获取解密密钥成功',
            'data': {
                'decrypt_key': decrypt_key
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取解密密钥失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取解密密钥失败: {str(e)}'
        }), 500

# 研究人员PIR实验：生成模拟健康数据
@researcher_bp.route('/experiment/generate-mock-data', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def generate_mock_data():
    try:
        # 获取请求参数
        data = request.get_json()
        count = data.get('count', 100)
        structured = data.get('structured', True)
        record_types = data.get('record_types')
        
        # 验证参数
        if count > 1000:
            return jsonify({
                'success': False,
                'message': '生成数据量过大，请将数量控制在1000条以内'
            }), 400
            
        # 生成模拟数据
        mock_data = generate_mock_health_data(
            count=count, 
            structured=structured,
            record_types=record_types
        )
        
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}生成了{count}条模拟健康数据",
            details={
                'researcher_id': current_user.id,
                'data_count': count,
                'structured': structured,
                'record_types': record_types
            }
        )
        
        # 将生成的数据存储到MongoDB临时集合中
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 创建实验记录
        experiment_id = str(ObjectId())
        
        # 记录实验元数据
        experiment_meta = {
            '_id': ObjectId(experiment_id),
            'researcher_id': current_user.id,
            'researcher_name': current_user.full_name,
            'experiment_type': 'mock_data_generation',
            'created_at': datetime.now(),
            'parameters': {
                'count': count,
                'structured': structured,
                'record_types': record_types
            },
            'data_count': len(mock_data)
        }
        
        # 存储元数据和模拟数据
        experiment_collection.insert_one(experiment_meta)
        
        # 为模拟数据创建单独的集合
        data_collection_name = f'experiment_data_{experiment_id}'
        mongo_db[data_collection_name].insert_many(mock_data)
        
        # 更新实验元数据，添加数据集合名称
        experiment_collection.update_one(
            {'_id': ObjectId(experiment_id)},
            {'$set': {'data_collection': data_collection_name}}
        )
        
        return jsonify({
            'success': True,
            'message': '成功生成模拟健康数据',
            'data': {
                'experiment_id': experiment_id,
                'data_count': len(mock_data),
                'sample': mock_data[:3] if len(mock_data) > 0 else []  # 返回3条样例数据
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"生成模拟健康数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'生成模拟健康数据失败: {str(e)}'
        }), 500

# 研究人员PIR实验：配置PIR协议参数
@researcher_bp.route('/experiment/configure-protocol', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def configure_protocol():
    try:
        # 获取请求参数
        data = request.get_json()
        protocol_type = data.get('protocol_type', PIRProtocolType.BASIC)
        custom_params = data.get('params', {})
        experiment_id = data.get('experiment_id')
        
        # 验证实验ID
        if not experiment_id:
            return jsonify({
                'success': False,
                'message': '缺少实验ID参数'
            }), 400
            
        # 验证协议类型
        valid_protocols = [
            PIRProtocolType.BASIC,
            PIRProtocolType.HOMOMORPHIC, 
            PIRProtocolType.HYBRID,
            PIRProtocolType.ONION
        ]
        
        if protocol_type not in valid_protocols:
            return jsonify({
                'success': False,
                'message': f'无效的协议类型: {protocol_type}'
            }), 400
            
        # 配置协议参数
        protocol_config = configure_pir_protocol(protocol_type, custom_params)
        
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查找实验记录
        experiment = experiment_collection.find_one({'_id': ObjectId(experiment_id)})
        if not experiment:
            return jsonify({
                'success': False,
                'message': f'未找到实验记录: {experiment_id}'
            }), 404
            
        # 更新实验记录，添加协议配置
        experiment_collection.update_one(
            {'_id': ObjectId(experiment_id)},
            {'$set': {
                'protocol_config': protocol_config,
                'updated_at': datetime.now()
            }}
        )
        
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}配置了PIR实验协议",
            details={
                'researcher_id': current_user.id,
                'experiment_id': experiment_id,
                'protocol_type': protocol_type,
                'protocol_config': protocol_config
            }
        )
        
        return jsonify({
            'success': True,
            'message': 'PIR协议配置成功',
            'data': {
                'experiment_id': experiment_id,
                'protocol_type': protocol_type,
                'protocol_config': protocol_config
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"配置PIR协议参数失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'配置PIR协议参数失败: {str(e)}'
        }), 500

# 研究人员PIR实验：执行隐私查询测试
@researcher_bp.route('/experiment/execute-query', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def execute_query_experiment():
    try:
        # 获取请求参数
        data = request.get_json()
        experiment_id = data.get('experiment_id')
        query_count = min(data.get('query_count', 10), 50)  # 限制最大查询次数
        
        # 验证实验ID
        if not experiment_id:
            return jsonify({
                'success': False,
                'message': '缺少实验ID参数'
            }), 400
            
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查找实验记录
        experiment = experiment_collection.find_one({'_id': ObjectId(experiment_id)})
        if not experiment:
            return jsonify({
                'success': False,
                'message': f'未找到实验记录: {experiment_id}'
            }), 404
            
        # 检查是否已配置协议
        protocol_config = experiment.get('protocol_config')
        if not protocol_config:
            return jsonify({
                'success': False,
                'message': '请先配置PIR协议参数'
            }), 400
            
        # 获取实验数据
        data_collection_name = experiment.get('data_collection')
        if not data_collection_name:
            return jsonify({
                'success': False,
                'message': '实验数据集不存在'
            }), 404
            
        # 从数据集合获取数据
        experiment_data = list(mongo_db[data_collection_name].find())
        if not experiment_data:
            return jsonify({
                'success': False,
                'message': '实验数据集为空'
            }), 404
            
        # 生成随机查询目标索引
        max_index = len(experiment_data) - 1
        if max_index < 0:
            return jsonify({
                'success': False,
                'message': '实验数据集为空'
            }), 404
            
        target_indices = random.sample(range(max_index + 1), min(query_count, max_index + 1))
        
        # 执行PIR查询实验
        experiment_results = execute_pir_query_experiment(
            data=experiment_data,
            target_indices=target_indices,
            protocol_config=protocol_config
        )
        
        # 更新实验记录，添加查询结果
        experiment_collection.update_one(
            {'_id': ObjectId(experiment_id)},
            {'$set': {
                'query_results': experiment_results,
                'query_time': datetime.now()
            }}
        )
        
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}执行了PIR实验查询",
            details={
                'researcher_id': current_user.id,
                'experiment_id': experiment_id,
                'query_count': len(target_indices),
                'protocol_type': protocol_config.get('protocol_type'),
                'metrics': experiment_results.get('metrics')
            }
        )
        
        return jsonify({
            'success': True,
            'message': 'PIR查询实验执行成功',
            'data': {
                'experiment_id': experiment_id,
                'query_count': len(target_indices),
                'results': experiment_results
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"执行PIR查询实验失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'执行PIR查询实验失败: {str(e)}'
        }), 500

# 研究人员PIR实验：获取性能指标
@researcher_bp.route('/experiment/performance-metrics', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_performance_metrics():
    try:
        # 获取请求参数
        experiment_id = request.args.get('experiment_id')
        
        # 验证实验ID
        if not experiment_id:
            return jsonify({
                'success': False,
                'message': '缺少实验ID参数'
            }), 400
            
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查找实验记录
        experiment = experiment_collection.find_one({'_id': ObjectId(experiment_id)})
        if not experiment:
            return jsonify({
                'success': False,
                'message': f'未找到实验记录: {experiment_id}'
            }), 404
            
        # 检查是否已执行查询
        query_results = experiment.get('query_results')
        if not query_results:
            return jsonify({
                'success': False,
                'message': '请先执行PIR查询实验'
            }), 400
            
        # 提取性能指标
        metrics = query_results.get('metrics', {})
        protocol = query_results.get('protocol', {})
        
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}查看了PIR实验性能指标",
            details={
                'researcher_id': current_user.id,
                'experiment_id': experiment_id
            }
        )
        
        return jsonify({
            'success': True,
            'message': '获取性能指标成功',
            'data': {
                'experiment_id': experiment_id,
                'protocol': protocol,
                'metrics': metrics,
                'timestamp': experiment.get('query_time')
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取性能指标失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取性能指标失败: {str(e)}'
        }), 500

# 研究人员PIR实验：比较多个协议性能
@researcher_bp.route('/experiment/compare-protocols', methods=['POST'])
@login_required
@role_required(Role.RESEARCHER)
def compare_protocols():
    try:
        # 获取请求参数
        data = request.get_json()
        experiment_ids = data.get('experiment_ids', [])
        
        if not experiment_ids or len(experiment_ids) < 2:
            return jsonify({
                'success': False,
                'message': '需要至少两个实验ID进行比较'
            }), 400
            
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 获取实验记录
        experiments = []
        for exp_id in experiment_ids:
            experiment = experiment_collection.find_one({'_id': ObjectId(exp_id)})
            if experiment and 'query_results' in experiment:
                experiments.append(experiment)
                
        if len(experiments) < 2:
            return jsonify({
                'success': False,
                'message': '未找到足够的有效实验记录进行比较'
            }), 404
            
        # 准备比较数据
        comparison_data = []
        for exp in experiments:
            protocol_config = exp.get('protocol_config', {})
            query_results = exp.get('query_results', {})
            metrics = query_results.get('metrics', {})
            
            comparison_data.append({
                'experiment_id': str(exp['_id']),
                'protocol_type': protocol_config.get('protocol_type', 'unknown'),
                'metrics': metrics,
                'timestamp': exp.get('query_time')
            })
            
        # 进行分析比较
        comparisons = []
        baseline = comparison_data[0]  # 使用第一个实验作为基准
        
        for i in range(1, len(comparison_data)):
            current = comparison_data[i]
            
            # 分析当前实验与基准实验的比较
            comparison_report = analyze_experiment_results(
                experiment_results={'metrics': current['metrics'], 'protocol': {'protocol_type': current['protocol_type']}},
                baseline_results={'metrics': baseline['metrics'], 'protocol': {'protocol_type': baseline['protocol_type']}}
            )
            
            comparisons.append({
                'baseline': {
                    'experiment_id': baseline['experiment_id'],
                    'protocol_type': baseline['protocol_type']
                },
                'current': {
                    'experiment_id': current['experiment_id'],
                    'protocol_type': current['protocol_type']
                },
                'report': comparison_report
            })
            
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}比较了{len(experiments)}个PIR协议的性能",
            details={
                'researcher_id': current_user.id,
                'experiment_ids': experiment_ids
            }
        )
        
        return jsonify({
            'success': True,
            'message': 'PIR协议性能比较成功',
            'data': {
                'protocols': comparison_data,
                'comparisons': comparisons
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"比较协议性能失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'比较协议性能失败: {str(e)}'
        }), 500

# 研究人员PIR实验：获取实验列表
@researcher_bp.route('/experiments', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_experiments():
    try:
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查询当前研究员的实验记录
        experiments = list(experiment_collection.find(
            {'researcher_id': current_user.id}
        ).sort('created_at', -1))
        
        # 格式化实验记录
        formatted_experiments = []
        for exp in experiments:
            protocol_config = exp.get('protocol_config', {})
            query_results = exp.get('query_results', {})
            
            formatted_exp = {
                'id': str(exp['_id']),
                'experiment_type': exp.get('experiment_type'),
                'created_at': exp.get('created_at').isoformat() if exp.get('created_at') else None,
                'updated_at': exp.get('updated_at').isoformat() if exp.get('updated_at') else None,
                'data_count': exp.get('data_count', 0),
                'protocol_type': protocol_config.get('protocol_type') if protocol_config else None,
                'has_results': 'query_results' in exp,
                'query_time': exp.get('query_time').isoformat() if exp.get('query_time') else None
            }
            
            # 如果有查询结果，添加简要的性能指标
            if 'query_results' in exp and 'metrics' in query_results:
                metrics = query_results['metrics']
                formatted_exp['metrics_summary'] = {
                    'query_time': metrics.get(PIRPerformanceMetric.QUERY_TIME),
                    'privacy_level': metrics.get(PIRPerformanceMetric.PRIVACY_LEVEL)
                }
                
            formatted_experiments.append(formatted_exp)
        
        return jsonify({
            'success': True,
            'message': '获取实验列表成功',
            'data': {
                'experiments': formatted_experiments
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取实验列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取实验列表失败: {str(e)}'
        }), 500

# 研究人员PIR实验：获取实验详情
@researcher_bp.route('/experiments/<experiment_id>', methods=['GET'])
@login_required
@role_required(Role.RESEARCHER)
def get_experiment_details(experiment_id):
    try:
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查询实验记录
        experiment = experiment_collection.find_one({
            '_id': ObjectId(experiment_id),
            'researcher_id': current_user.id
        })
        
        if not experiment:
            return jsonify({
                'success': False,
                'message': f'未找到实验记录: {experiment_id}'
            }), 404
            
        # 格式化实验详情
        formatted_experiment = {
            'id': str(experiment['_id']),
            'experiment_type': experiment.get('experiment_type'),
            'created_at': experiment.get('created_at').isoformat() if experiment.get('created_at') else None,
            'updated_at': experiment.get('updated_at').isoformat() if experiment.get('updated_at') else None,
            'parameters': experiment.get('parameters', {}),
            'data_count': experiment.get('data_count', 0),
            'protocol_config': experiment.get('protocol_config'),
            'query_time': experiment.get('query_time').isoformat() if experiment.get('query_time') else None
        }
        
        # 获取查询结果
        if 'query_results' in experiment:
            query_results = experiment['query_results']
            formatted_experiment['results'] = {
                'metrics': query_results.get('metrics', {}),
                'sample_results': query_results.get('results', [])[:5]  # 仅返回前5个结果样例
            }
            
        # 获取数据样例
        if 'data_collection' in experiment:
            data_collection = mongo_db[experiment['data_collection']]
            data_samples = list(data_collection.find().limit(3))
            
            # 格式化样例数据
            formatted_samples = []
            for sample in data_samples:
                sample['_id'] = str(sample['_id'])
                formatted_samples.append(sample)
                
            formatted_experiment['data_samples'] = formatted_samples
            
        return jsonify({
            'success': True,
            'message': '获取实验详情成功',
            'data': formatted_experiment
        })
        
    except Exception as e:
        current_app.logger.error(f"获取实验详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取实验详情失败: {str(e)}'
        }), 500

# 研究人员PIR实验：删除实验
@researcher_bp.route('/experiments/<experiment_id>', methods=['DELETE'])
@login_required
@role_required(Role.RESEARCHER)
def delete_experiment(experiment_id):
    try:
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        experiment_collection = mongo_db.pir_experiments
        
        # 查询实验记录
        experiment = experiment_collection.find_one({
            '_id': ObjectId(experiment_id),
            'researcher_id': current_user.id
        })
        
        if not experiment:
            return jsonify({
                'success': False,
                'message': f'未找到实验记录: {experiment_id}'
            }), 404
            
        # 删除关联的数据集合
        if 'data_collection' in experiment:
            try:
                mongo_db[experiment['data_collection']].drop()
            except Exception as e:
                current_app.logger.error(f"删除实验数据集合失败: {str(e)}")
                
        # 删除实验记录
        experiment_collection.delete_one({'_id': ObjectId(experiment_id)})
        
        # 记录日志
        log_research(
            message=f"研究员{current_user.full_name}删除了PIR实验",
            details={
                'researcher_id': current_user.id,
                'experiment_id': experiment_id
            }
        )
        
        return jsonify({
            'success': True,
            'message': '删除实验成功',
            'data': {
                'experiment_id': experiment_id
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"删除实验失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除实验失败: {str(e)}'
        }), 500
