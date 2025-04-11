import numpy as np
import random
import math
from flask import current_app
from ..utils.mongo_utils import mongo
from datetime import datetime
import json
from bson import ObjectId
import hashlib
import base64
import os

class PIRQuery:
    """隐匿查询实现类"""
    
    @staticmethod
    def create_query_vector(db_size, target_index):
        """
        创建查询向量，一种基本的PIR实现
        
        Args:
            db_size: 数据库大小
            target_index: 目标数据索引
            
        Returns:
            查询向量
        """
        # 创建全0查询向量
        query_vector = np.zeros(db_size, dtype=int)
        # 目标位置设为1
        query_vector[target_index] = 1
        return query_vector
    
    @staticmethod
    def process_query(data, query_vector):
        """
        服务器处理查询向量并返回结果
        
        Args:
            data: 数据库中的所有数据
            query_vector: 查询向量
            
        Returns:
            查询结果
        """
        # 使用查询向量与数据内积获取结果
        result = np.dot(data, query_vector)
        return result
    
    @staticmethod
    def encode_health_record(record):
        """
        将健康记录编码为数值向量
        
        Args:
            record: 健康记录对象
            
        Returns:
            数值向量
        """
        # 将记录序列化为JSON字符串
        record_json = json.dumps(record, default=str)
        # 编码为字节
        record_bytes = record_json.encode('utf-8')
        # 计算哈希值创建固定长度指纹
        record_hash = hashlib.sha256(record_bytes).digest()
        # 转换为整数列表
        numeric_vector = [b for b in record_hash]
        return numeric_vector
    
    @staticmethod
    def obfuscate_query(query_params, patient_id):
        """
        混淆查询参数，增加噪声查询，隐藏用户真实查询意图
        
        Args:
            query_params: 原始查询参数
            patient_id: 患者ID
            
        Returns:
            混淆后的查询序列
        """
        # 创建查询拷贝
        true_query = query_params.copy()
        
        # 生成随机噪声查询的数量 (1-3个)
        num_noise_queries = random.randint(1, 3)
        
        # 可能的查询参数种类
        possible_params = {
            'record_type': ['medical_history', 'examination', 'medication', 'vital_signs', 'treatment', 'surgery', 'other'],
            'start_date': [(datetime.now().replace(year=datetime.now().year-i)).strftime('%Y-%m-%d') for i in range(1, 5)],
            'end_date': [(datetime.now().replace(month=i)).strftime('%Y-%m-%d') for i in range(1, 13) if i != datetime.now().month],
            'keyword': ['感冒', '发热', '检查', '治疗', '血压', '心率', '手术', '药物', '过敏', '住院']
        }
        
        # 生成噪声查询
        noise_queries = []
        for _ in range(num_noise_queries):
            noise_query = {}
            
            # 随机选择1-3个参数
            num_params = random.randint(1, 3)
            params_to_include = random.sample(list(possible_params.keys()), num_params)
            
            for param in params_to_include:
                noise_query[param] = random.choice(possible_params[param])
            
            # 添加到噪声查询列表
            noise_queries.append(noise_query)
        
        # 将真实查询和噪声查询混合
        all_queries = [true_query] + noise_queries
        random.shuffle(all_queries)  # 随机排序
        
        # 记录真实查询位置的哈希值 (仅患者可以解码)
        true_query_index = all_queries.index(true_query)
        index_seed = f"{patient_id}_{datetime.now().date().isoformat()}"
        index_hash = hashlib.sha256(index_seed.encode()).hexdigest()
        
        return {
            'queries': all_queries,
            'index_hash': index_hash,
            'true_index': PIRQuery.encrypt_index(true_query_index, index_hash)
        }
    
    @staticmethod
    def encrypt_index(index, key):
        """
        加密真实查询索引
        
        Args:
            index: 真实查询的索引
            key: 加密密钥
            
        Returns:
            加密后的索引
        """
        # 简单加密: 使用密钥的前8位作为种子，生成shuffle映射
        seed = int(key[:8], 16)
        random.seed(seed)
        
        # 生成10个随机数，将索引隐藏在其中
        random_numbers = [random.randint(100, 999) for _ in range(9)]
        
        # 将索引值转换为3位数
        encoded_index = index + 100
        
        # 插入到随机位置
        position = random.randint(0, 9)
        random_numbers.insert(position, encoded_index)
        
        # 再次使用密钥加密位置
        position_key = int(key[8:16], 16) % 100
        encrypted_position = (position + position_key) % 10
        
        return {
            'data': random_numbers,
            'key': encrypted_position
        }
    
    @staticmethod
    def decrypt_index(encrypted_data, key, patient_id):
        """
        解密真实查询索引
        
        Args:
            encrypted_data: 加密的索引数据
            key: 索引哈希
            patient_id: 患者ID
            
        Returns:
            真实查询的索引
        """
        # 验证密钥
        index_seed = f"{patient_id}_{datetime.now().date().isoformat()}"
        expected_hash = hashlib.sha256(index_seed.encode()).hexdigest()
        
        if key != expected_hash:
            raise ValueError("无效的密钥，无法解密索引")
        
        # 解密位置
        position_key = int(key[8:16], 16) % 100
        real_position = (encrypted_data['key'] - position_key) % 10
        if real_position < 0:
            real_position += 10
            
        # 获取真实索引
        encoded_index = encrypted_data['data'][real_position]
        true_index = encoded_index - 100
        
        return true_index

def prepare_pir_database(health_records):
    """
    将健康记录准备为PIR数据库格式
    
    Args:
        health_records: 健康记录列表
        
    Returns:
        PIR数据库，记录映射
    """
    # 创建记录到数值向量的映射
    record_vectors = []
    record_mapping = {}
    
    for idx, record in enumerate(health_records):
        vector = PIRQuery.encode_health_record(record)
        record_vectors.append(vector)
        record_mapping[idx] = record
    
    # 将向量列表转为numpy数组
    if record_vectors:
        max_length = max(len(v) for v in record_vectors)
        # 填充向量到相同长度
        padded_vectors = [v + [0] * (max_length - len(v)) for v in record_vectors]
        pir_database = np.array(padded_vectors)
    else:
        pir_database = np.array([])
    
    return pir_database, record_mapping

def store_health_record_mongodb(record_data, patient_id, file_info=None):
    """
    将健康记录存储到MongoDB
    
    Args:
        record_data: 记录数据
        patient_id: 患者ID
        file_info: 文件信息
        
    Returns:
        MongoDB中的记录ID
    """
    # 创建MongoDB记录
    mongo_record = {
        'patient_id': patient_id,
        'record_type': record_data.get('record_type'),
        'title': record_data.get('title'),
        'description': record_data.get('description'),
        'record_date': datetime.strptime(record_data.get('record_date', datetime.now().isoformat()), 
                                          '%Y-%m-%dT%H:%M:%S.%f') if 'record_date' in record_data else datetime.now(),
        'institution': record_data.get('institution'),
        'doctor_name': record_data.get('doctor_name'),
        'visibility': record_data.get('visibility', 'private'),
        'tags': record_data.get('tags'),
        'data': record_data.get('data'),
        'created_at': datetime.now(),
        'updated_at': datetime.now()
    }
    
    # 添加文件信息
    if file_info:
        mongo_record['files'] = file_info
    
    # 添加用药记录
    if record_data.get('record_type') == 'medication' and record_data.get('medication'):
        med_data = record_data.get('medication')
        mongo_record['medication'] = {
            'medication_name': med_data.get('medication_name', ''),
            'dosage': med_data.get('dosage'),
            'frequency': med_data.get('frequency'),
            'start_date': datetime.strptime(med_data.get('start_date'), '%Y-%m-%d').date() if med_data.get('start_date') else None,
            'end_date': datetime.strptime(med_data.get('end_date'), '%Y-%m-%d').date() if med_data.get('end_date') else None,
            'instructions': med_data.get('instructions'),
            'side_effects': med_data.get('side_effects')
        }
    
    # 添加生命体征
    if record_data.get('record_type') == 'vital_signs' and record_data.get('vital_signs'):
        mongo_record['vital_signs'] = []
        for vs_data in record_data.get('vital_signs', []):
            mongo_record['vital_signs'].append({
                'type': vs_data.get('type', ''),
                'value': float(vs_data.get('value', 0)),
                'unit': vs_data.get('unit'),
                'measured_at': datetime.strptime(vs_data.get('measured_at', datetime.now().isoformat()), 
                                               '%Y-%m-%dT%H:%M:%S.%f') if 'measured_at' in vs_data else datetime.now(),
                'notes': vs_data.get('notes')
            })
    
    # 插入记录
    result = mongo.db.health_records.insert_one(mongo_record)
    
    return str(result.inserted_id)

def query_health_records_mongodb(query_params, patient_id, is_anonymous=False):
    """
    从MongoDB查询健康记录
    
    Args:
        query_params: 查询参数
        patient_id: 患者ID
        is_anonymous: 是否匿名查询
        
    Returns:
        健康记录列表，查询元数据
    """
    # 基础查询条件
    query = {'patient_id': patient_id}
    
    # 添加查询条件
    if 'record_type' in query_params and query_params['record_type']:
        query['record_type'] = query_params['record_type']
    
    # 日期范围
    date_query = {}
    if 'start_date' in query_params and query_params['start_date']:
        date_query['$gte'] = datetime.strptime(query_params['start_date'], '%Y-%m-%d')
    if 'end_date' in query_params and query_params['end_date']:
        date_query['$lte'] = datetime.strptime(query_params['end_date'], '%Y-%m-%d')
    if date_query:
        query['record_date'] = date_query
    
    # 关键字搜索
    if 'keyword' in query_params and query_params['keyword']:
        keyword = query_params['keyword']
        query['$or'] = [
            {'title': {'$regex': keyword, '$options': 'i'}},
            {'description': {'$regex': keyword, '$options': 'i'}},
            {'tags': {'$regex': keyword, '$options': 'i'}}
        ]
    
    # 是否使用隐匿查询
    if is_anonymous:
        # 混淆查询
        obfuscated_query = PIRQuery.obfuscate_query(query_params, patient_id)
        
        # 执行所有混淆查询
        all_results = []
        real_results = None
        
        for i, q in enumerate(obfuscated_query['queries']):
            # 构建查询条件
            current_query = {'patient_id': patient_id}
            
            if 'record_type' in q and q['record_type']:
                current_query['record_type'] = q['record_type']
            
            date_query = {}
            if 'start_date' in q and q['start_date']:
                date_query['$gte'] = datetime.strptime(q['start_date'], '%Y-%m-%d')
            if 'end_date' in q and q['end_date']:
                date_query['$lte'] = datetime.strptime(q['end_date'], '%Y-%m-%d')
            if date_query:
                current_query['record_date'] = date_query
            
            if 'keyword' in q and q['keyword']:
                current_query['$or'] = [
                    {'title': {'$regex': q['keyword'], '$options': 'i'}},
                    {'description': {'$regex': q['keyword'], '$options': 'i'}},
                    {'tags': {'$regex': q['keyword'], '$options': 'i'}}
                ]
            
            # 执行查询
            results = list(mongo.db.health_records.find(current_query))
            
            # 将ObjectId转为字符串
            for r in results:
                r['_id'] = str(r['_id'])
                if 'record_date' in r:
                    r['record_date'] = r['record_date'].isoformat()
                if 'created_at' in r:
                    r['created_at'] = r['created_at'].isoformat()
                if 'updated_at' in r:
                    r['updated_at'] = r['updated_at'].isoformat()
                
                # 处理用药记录和生命体征的日期
                if 'medication' in r and r['medication']:
                    if 'start_date' in r['medication'] and r['medication']['start_date']:
                        r['medication']['start_date'] = r['medication']['start_date'].isoformat()
                    if 'end_date' in r['medication'] and r['medication']['end_date']:
                        r['medication']['end_date'] = r['medication']['end_date'].isoformat()
                
                if 'vital_signs' in r and r['vital_signs']:
                    for vs in r['vital_signs']:
                        if 'measured_at' in vs:
                            vs['measured_at'] = vs['measured_at'].isoformat()
            
            # 添加到所有结果中
            all_results.append(results)
            
            # 如果是真实查询索引，保存结果
            true_index = PIRQuery.decrypt_index(obfuscated_query['true_index'], 
                                               obfuscated_query['index_hash'], 
                                               patient_id)
            if i == true_index:
                real_results = results
        
        # 记录查询历史（仅记录真实查询）
        record_query_history(patient_id, 'pir_query', query_params, is_anonymous=True)
        
        return real_results, {
            'pir_metadata': {
                'total_queries': len(obfuscated_query['queries']),
                'index_hash': obfuscated_query['index_hash']
            }
        }
    else:
        # 常规查询
        results = list(mongo.db.health_records.find(query))
        
        # 将ObjectId转为字符串
        for r in results:
            r['_id'] = str(r['_id'])
            if 'record_date' in r:
                r['record_date'] = r['record_date'].isoformat()
            if 'created_at' in r:
                r['created_at'] = r['created_at'].isoformat()
            if 'updated_at' in r:
                r['updated_at'] = r['updated_at'].isoformat()
            
            # 处理用药记录和生命体征的日期
            if 'medication' in r and r['medication']:
                if 'start_date' in r['medication'] and r['medication']['start_date']:
                    r['medication']['start_date'] = r['medication']['start_date'].isoformat()
                if 'end_date' in r['medication'] and r['medication']['end_date']:
                    r['medication']['end_date'] = r['medication']['end_date'].isoformat()
            
            if 'vital_signs' in r and r['vital_signs']:
                for vs in r['vital_signs']:
                    if 'measured_at' in vs:
                        vs['measured_at'] = vs['measured_at'].isoformat()
        
        # 记录查询历史
        record_query_history(patient_id, 'standard_query', query_params, is_anonymous=False)
        
        return results, {'standard_query': True}

def record_query_history(patient_id, query_type, query_params, is_anonymous=False):
    """
    记录查询历史
    
    Args:
        patient_id: 患者ID
        query_type: 查询类型
        query_params: 查询参数
        is_anonymous: 是否匿名查询
    """
    query_history = {
        'user_id': patient_id,
        'query_type': query_type,
        'query_params': query_params,
        'is_anonymous': is_anonymous,
        'query_time': datetime.now()
    }
    
    mongo.db.query_history.insert_one(query_history) 