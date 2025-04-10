from flask import Blueprint, request, jsonify, session
from app.models.mysql import db, User, HealthRecord, QueryLog
from app.models.redis import redis_client
from app.pir.pir_service import pir_system
from app.utils.security import login_required, role_required
import json
import uuid
from datetime import datetime

query_bp = Blueprint('query', __name__)

@query_bp.route('/keyword', methods=['POST'])
@login_required
def keyword_query():
    """对健康记录执行基于关键词的隐私查询"""
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    data = request.get_json()
    
    if not data or not data.get('keywords'):
        return jsonify({'message': '未提供关键词'}), 400
    
    # 准备查询参数
    query_params = {
        'query_type': 'keyword',
        'keywords': data['keywords'],
        'user_id': current_user_id
    }
    
    # 添加用户角色以进行范围限定
    query_params['user_role'] = current_user.role
    
    # 如果医生在为特定患者查询，添加患者ID
    if current_user.role == 'doctor' and data.get('patient_id'):
        query_params['patient_id'] = data['patient_id']
    
    # 记录查询（为保护隐私，加密参数）
    query_log = QueryLog(
        user_id=current_user_id,
        query_type='keyword',
        query_params=json.dumps({"keywords_count": len(data['keywords'])}),  # 不记录实际关键词
        timestamp=datetime.now(),
        success=True
    )
    db.session.add(query_log)
    db.session.commit()
    
    # 使用PIR执行查询
    result = pir_system.query(query_params)
    
    if 'error' in result:
        # 用错误更新查询日志
        query_log.success = False
        db.session.commit()
        return jsonify({'message': result['error']}), 400
    
    # 如果是大型结果，将查询结果存储在Redis中以便稍后检索
    if 'record_ids' in result and len(result['record_ids']) > 10:
        query_id = result.get('query_id', str(uuid.uuid4()))
        redis_client.store_pir_query_state(current_user_id, query_id, {
            'record_ids': result['record_ids'],
            'timestamp': datetime.now().isoformat()
        })
        
        # 如果结果过多，只返回计数和查询ID
        if len(result['record_ids']) > 100:
            return jsonify({
                'message': '查询成功。结果集很大。',
                'query_id': query_id,
                'record_count': len(result['record_ids']),
                'retrieve_url': f'/api/query/results/{query_id}'
            }), 200
    
    # 获取结果的最小记录信息
    records_data = []
    if 'record_ids' in result:
        for record_id in result['record_ids'][:100]:  # 限制为前100条
            record = HealthRecord.query.get(record_id)
            if record:
                # 检查当前用户是否有权访问此记录
                has_access = False
                if current_user.role == 'admin':
                    has_access = True
                elif current_user_id == record.patient_id or current_user_id == record.doctor_id:
                    has_access = True
                
                if has_access:
                    records_data.append({
                        'id': record.id,
                        'patient_id': record.patient_id,
                        'doctor_id': record.doctor_id,
                        'record_type': record.record_type,
                        'created_at': record.created_at.isoformat()
                    })
    
    return jsonify({
        'query_id': result.get('query_id'),
        'record_count': result.get('record_count', 0),
        'records': records_data
    }), 200

@query_bp.route('/results/<query_id>', methods=['GET'])
@login_required
def get_query_results(query_id):
    """检索先前执行的查询的结果"""
    current_user_id = session.get('user_id')
    
    # 从Redis获取查询状态
    query_state = redis_client.get_pir_query_state(current_user_id, query_id)
    
    if not query_state:
        return jsonify({'message': '查询结果未找到或已过期'}), 404
    
    # 获取分页参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # 将per_page限制在合理的值范围内
    per_page = min(per_page, 100)
    
    # 从查询状态获取记录ID
    record_ids = query_state.get('record_ids', [])
    
    # 计算分页
    total_records = len(record_ids)
    total_pages = (total_records + per_page - 1) // per_page
    page = min(page, total_pages) if total_pages > 0 else 1
    
    # 获取当前页的记录
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, total_records)
    current_page_ids = record_ids[start_idx:end_idx]
    
    # 获取记录详情
    records_data = []
    for record_id in current_page_ids:
        record = HealthRecord.query.get(record_id)
        if record:
            records_data.append({
                'id': record.id,
                'patient_id': record.patient_id,
                'doctor_id': record.doctor_id,
                'record_type': record.record_type,
                'created_at': record.created_at.isoformat()
            })
    
    return jsonify({
        'query_id': query_id,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'total_records': total_records,
        'records': records_data
    }), 200

@query_bp.route('/status/<query_id>', methods=['GET'])
@login_required
def get_query_status(query_id):
    """获取先前执行的查询的状态"""
    current_user_id = session.get('user_id')
    
    status = pir_system.get_query_status(current_user_id, query_id)
    
    if 'error' in status:
        return jsonify({'message': status['error']}), 404
    
    return jsonify(status), 200

@query_bp.route('/pir', methods=['POST'])
@login_required
def direct_pir_query():
    """
    使用自定义参数执行直接PIR查询
    此端点适用于了解PIR协议的高级用户
    """
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 只允许管理员和医生使用直接PIR查询
    if current_user.role not in ['admin', 'doctor']:
        return jsonify({'message': '未授权访问'}), 403
    
    data = request.get_json()
    
    if not data:
        return jsonify({'message': '未提供查询参数'}), 400
    
    # 添加用户ID到查询参数
    data['user_id'] = current_user_id
    
    # 记录PIR查询（为保护隐私，记录最少信息）
    query_log = QueryLog(
        user_id=current_user_id,
        query_type='direct_pir',
        query_params=json.dumps({"query_type": data.get('query_type', 'unknown')}),
        timestamp=datetime.now(),
        success=True
    )
    db.session.add(query_log)
    db.session.commit()
    
    # 执行PIR查询
    result = pir_system.query(data)
    
    if 'error' in result:
        # 用错误更新查询日志
        query_log.success = False
        db.session.commit()
        return jsonify({'message': result['error']}), 400
    
    return jsonify(result), 200

@query_bp.route('/logs', methods=['GET'])
@login_required
@role_required(['admin'])
def get_query_logs():
    """获取查询日志（仅限管理员）"""
    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # 可选过滤器
    user_id = request.args.get('user_id', type=int)
    query_type = request.args.get('query_type')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # 构建查询
    query = QueryLog.query
    
    if user_id:
        query = query.filter_by(user_id=user_id)
    
    if query_type:
        query = query.filter_by(query_type=query_type)
    
    if start_date:
        query = query.filter(QueryLog.timestamp >= start_date)
    
    if end_date:
        query = query.filter(QueryLog.timestamp <= end_date)
    
    # 按时间戳降序排序
    query = query.order_by(QueryLog.timestamp.desc())
    
    # 分页结果
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    
    logs_data = [{
        'id': log.id,
        'user_id': log.user_id,
        'query_type': log.query_type,
        'query_params': json.loads(log.query_params) if log.query_params else None,
        'timestamp': log.timestamp.isoformat(),
        'success': log.success
    } for log in logs]
    
    return jsonify({
        'logs': logs_data,
        'page': page,
        'per_page': per_page,
        'total_pages': pagination.pages,
        'total_logs': pagination.total
    }), 200 