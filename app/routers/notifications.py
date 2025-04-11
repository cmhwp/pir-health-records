from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from ..models import db, User, Role, Notification, NotificationType
from ..routers.auth import role_required
from datetime import datetime, timedelta
from sqlalchemy import desc

notifications_bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')

# 获取当前用户的通知列表
@notifications_bp.route('', methods=['GET'])
@login_required
def get_notifications():
    try:
        # 分页
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # 筛选条件
        filter_read = request.args.get('read')
        filter_type = request.args.get('type')
        
        # 基础查询
        query = Notification.query.filter_by(user_id=current_user.id)
        
        # 应用筛选条件
        if filter_read is not None:
            is_read = filter_read.lower() == 'true'
            query = query.filter_by(is_read=is_read)
            
        if filter_type:
            try:
                notification_type = NotificationType(filter_type)
                query = query.filter_by(notification_type=notification_type)
            except ValueError:
                pass
                
        # 获取总数
        total = query.count()
        
        # 分页并按时间降序排序
        notifications = query.order_by(desc(Notification.created_at)) \
                            .offset((page - 1) * per_page) \
                            .limit(per_page) \
                            .all()
                            
        # 统计未读通知数量
        unread_count = Notification.query.filter_by(
            user_id=current_user.id, 
            is_read=False
        ).count()
        
        # 转换为字典
        result = [n.to_dict() for n in notifications]
        
        # 获取相关用户信息
        for notification in result:
            if notification['sender_id']:
                sender = User.query.get(notification['sender_id'])
                if sender:
                    notification['sender'] = {
                        'id': sender.id,
                        'username': sender.username,
                        'full_name': sender.full_name,
                        'role': sender.role.value
                    }
        
        return jsonify({
            'success': True,
            'data': {
                'notifications': result,
                'total': total,
                'pages': (total + per_page - 1) // per_page,
                'current_page': page,
                'unread_count': unread_count
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"获取通知列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取通知列表失败: {str(e)}'
        }), 500

# 标记通知为已读
@notifications_bp.route('/<notification_id>/read', methods=['PUT'])
@login_required
def mark_notification_read(notification_id):
    try:
        notification = Notification.query.get(notification_id)
        if not notification:
            return jsonify({
                'success': False,
                'message': '通知不存在'
            }), 404
            
        # 验证权限
        if notification.user_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限操作此通知'
            }), 403
            
        # 标记为已读
        notification.is_read = True
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '通知已标记为已读'
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"标记通知为已读失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'标记通知为已读失败: {str(e)}'
        }), 500

# 标记所有通知为已读
@notifications_bp.route('/read-all', methods=['PUT'])
@login_required
def mark_all_notifications_read():
    try:
        Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).update({'is_read': True})
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '所有通知已标记为已读'
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"标记所有通知为已读失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'标记所有通知为已读失败: {str(e)}'
        }), 500

# 删除通知
@notifications_bp.route('/<notification_id>', methods=['DELETE'])
@login_required
def delete_notification(notification_id):
    try:
        notification = Notification.query.get(notification_id)
        if not notification:
            return jsonify({
                'success': False,
                'message': '通知不存在'
            }), 404
            
        # 验证权限
        if notification.user_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限删除此通知'
            }), 403
            
        # 删除通知
        db.session.delete(notification)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '通知已删除'
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除通知失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除通知失败: {str(e)}'
        }), 500

# 创建系统通知（仅管理员可用）
@notifications_bp.route('/system', methods=['POST'])
@login_required
@role_required(Role.ADMIN)
def create_system_notification():
    try:
        data = request.json
        if not data or not data.get('title') or not data.get('message'):
            return jsonify({
                'success': False,
                'message': '缺少必要参数'
            }), 400
            
        # 获取目标用户
        target_users = []
        if 'user_ids' in data and data['user_ids']:
            for user_id in data['user_ids']:
                user = User.query.get(user_id)
                if user:
                    target_users.append(user)
        else:
            # 如果未指定用户，则发送给所有用户
            target_users = User.query.all()
            
        # 获取过期时间
        expires_at = None
        if 'expires_days' in data and data['expires_days'] > 0:
            expires_at = datetime.now() + timedelta(days=data['expires_days'])
            
        # 创建通知
        created_count = 0
        for user in target_users:
            notification = Notification(
                user_id=user.id,
                sender_id=current_user.id,
                notification_type=NotificationType.SYSTEM,
                title=data['title'],
                message=data['message'],
                related_id=data.get('related_id'),
                expires_at=expires_at
            )
            db.session.add(notification)
            created_count += 1
            
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功创建 {created_count} 条系统通知',
            'data': {
                'created_count': created_count
            }
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建系统通知失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建系统通知失败: {str(e)}'
        }), 500

# 获取未读通知数量
@notifications_bp.route('/unread-count', methods=['GET'])
@login_required
def get_unread_count():
    try:
        unread_count = Notification.query.filter_by(
            user_id=current_user.id, 
            is_read=False
        ).count()
        
        return jsonify({
            'success': True,
            'data': {
                'unread_count': unread_count
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"获取未读通知数量失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取未读通知数量失败: {str(e)}'
        }), 500 