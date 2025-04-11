from flask import Blueprint, jsonify, request, current_app
from ..models import db
from ..models.user import User
from ..models.product import Product
from ..models.log import Log
from ..models.cache_item import CacheItem
from ..utils.mongo_utils import get_mongo_db, format_mongo_docs, format_mongo_doc
from ..utils.redis_utils import get_redis, cache_key

db_examples_bp = Blueprint('db_examples', __name__, url_prefix='/api/db')

# MySQL (SQLAlchemy) 示例
@db_examples_bp.route('/mysql/users', methods=['GET'])
def get_users():
    users = User.query.all()
    return jsonify({
        'success': True,
        'data': [user.to_dict() for user in users]
    })

@db_examples_bp.route('/mysql/users', methods=['POST'])
def create_user():
    data = request.json
    user = User(
        username=data.get('username'),
        email=data.get('email'),
        password_hash=data.get('password')  # 在生产环境中，应该对密码进行哈希处理
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({
        'success': True,
        'data': user.to_dict()
    }), 201

@db_examples_bp.route('/mysql/products', methods=['GET'])
def get_products():
    products = Product.query.all()
    return jsonify({
        'success': True,
        'data': [product.to_dict() for product in products]
    })

@db_examples_bp.route('/mysql/products', methods=['POST'])
def create_product():
    data = request.json
    product = Product(
        name=data.get('name'),
        description=data.get('description'),
        price=data.get('price'),
        inventory=data.get('inventory', 0),
        category=data.get('category')
    )
    db.session.add(product)
    db.session.commit()
    return jsonify({
        'success': True,
        'data': product.to_dict()
    }), 201

# MongoDB 示例
@db_examples_bp.route('/mongodb/logs', methods=['GET'])
def get_logs():
    mongo_db = get_mongo_db()
    user_id = request.args.get('user_id')
    action = request.args.get('action')
    limit = int(request.args.get('limit', 100))
    skip = int(request.args.get('skip', 0))
    
    logs = Log.get_logs(mongo_db, limit=limit, skip=skip, user_id=user_id, action=action)
    return jsonify({
        'success': True,
        'count': len(logs),
        'data': format_mongo_docs(logs)
    })

@db_examples_bp.route('/mongodb/logs', methods=['POST'])
def create_log():
    mongo_db = get_mongo_db()
    data = request.json
    
    log_id = Log.create_log(
        mongo_db,
        action=data.get('action'),
        data=data.get('data'),
        user_id=data.get('user_id')
    )
    
    # 获取创建的日志
    log = mongo_db.logs.find_one({'_id': log_id})
    
    return jsonify({
        'success': True,
        'data': format_mongo_doc(log)
    }), 201

# Redis 示例
@db_examples_bp.route('/redis/cache', methods=['GET'])
def get_cache():
    key = request.args.get('key')
    if not key:
        return jsonify({
            'success': False,
            'error': '需要key参数'
        }), 400
    
    value = CacheItem.get(key)
    if value is None:
        return jsonify({
            'success': False,
            'error': '缓存中未找到该键'
        }), 404
    
    ttl = CacheItem.ttl(key)
    
    return jsonify({
        'success': True,
        'data': {
            'key': key,
            'value': value,
            'ttl': ttl
        }
    })

@db_examples_bp.route('/redis/cache', methods=['POST'])
def set_cache():
    data = request.json
    key = data.get('key')
    value = data.get('value')
    expire = data.get('expire')  # 秒为单位
    
    if not key or value is None:
        return jsonify({
            'success': False,
            'error': '需要key和value参数'
        }), 400
    
    success = CacheItem.set(key, value, expire)
    
    return jsonify({
        'success': success,
        'data': {
            'key': key,
            'value': value,
            'expire': expire
        }
    }), 201

@db_examples_bp.route('/redis/cache', methods=['DELETE'])
def delete_cache():
    key = request.args.get('key')
    if not key:
        return jsonify({
            'success': False,
            'error': '需要key参数'
        }), 400
    
    deleted = CacheItem.delete(key)
    
    return jsonify({
        'success': True,
        'deleted': deleted > 0
    }) 