import random
import numpy as np
import time
import json
import psutil
import threading
from datetime import datetime, timedelta
from flask import current_app
import hashlib
import base64
from bson import ObjectId
from sklearn.metrics.pairwise import cosine_similarity

# 添加自定义JSON编码器
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, ObjectId):
            return str(obj)
        return super(DateTimeEncoder, self).default(obj)

from ..models import db
from ..models.health_records import HealthRecord, RecordVisibility
from ..utils.mongo_utils import get_mongo_db
from ..utils.log_utils import log_research, log_pir

# 改进的资源监控装饰器
def monitor_resources(func):
    """监控函数执行的CPU和内存使用，持续采样并记录平均值和最大值"""
    def wrapper(*args, **kwargs):
        process = psutil.Process()
        # 预热进程，确保初始读数稳定
        process.cpu_percent()
        time.sleep(0.1)
        
        # 监控数据结构
        resource_data = {
            'cpu_samples': [],
            'mem_samples': [],
            'monitoring': True
        }
        
        # 采样函数
        def resource_sampler():
            while resource_data['monitoring']:
                try:
                    cpu = process.cpu_percent()
                    mem = process.memory_info().rss / (1024**2)  # 转换为MB
                    resource_data['cpu_samples'].append(cpu)
                    resource_data['mem_samples'].append(mem)
                except:
                    pass  # 忽略潜在错误
                time.sleep(0.5)  # 每500ms采样一次
        
        # 启动监控线程
        monitor_thread = threading.Thread(target=resource_sampler)
        monitor_thread.daemon = True  # 设为守护线程，避免阻塞主程序
        monitor_thread.start()
        
        try:
            # 执行原函数
            result = func(*args, **kwargs)
            
            # 停止监控
            resource_data['monitoring'] = False
            monitor_thread.join(1.0)  # 等待监控线程最多1秒
            
            # 计算统计数据
            if resource_data['cpu_samples']:
                cpu_avg = sum(resource_data['cpu_samples']) / len(resource_data['cpu_samples'])
                cpu_max = max(resource_data['cpu_samples'])
                mem_avg = sum(resource_data['mem_samples']) / len(resource_data['mem_samples'])
                mem_max = max(resource_data['mem_samples'])
            else:
                cpu_avg = cpu_max = mem_avg = mem_max = 0
            
            # 将资源使用统计添加到结果中
            if isinstance(result, dict):
                result['cpu_usage'] = cpu_avg
                result['mem_usage'] = mem_avg
                result['cpu_usage_max'] = cpu_max
                result['mem_usage_max'] = mem_max
                result['resource_samples'] = len(resource_data['cpu_samples'])
            
            return result
        except Exception as e:
            # 停止监控
            resource_data['monitoring'] = False
            if monitor_thread.is_alive():
                monitor_thread.join(1.0)
            raise e
    
    return wrapper

# PIR 协议类型
class PIRProtocolType:
    BASIC = "basic"                # 基本PIR协议
    HOMOMORPHIC = "homomorphic"    # 同态加密PIR
    HYBRID = "hybrid"              # 混合PIR协议
    ONION = "onion"                # 洋葱路由PIR

# PIR性能指标
class PIRPerformanceMetric:
    QUERY_TIME = "query_time"          # 查询时间
    ACCURACY = "accuracy"              # 准确率
    COMMUNICATION_COST = "comm_cost"   # 通信开销
    SERVER_LOAD = "server_load"        # 服务器负载
    CLIENT_LOAD = "client_load"        # 客户端负载
    PRIVACY_LEVEL = "privacy_level"    # 隐私保护级别
    CPU_USAGE = "cpu_usage"            # CPU使用率
    MEM_USAGE = "mem_usage"            # 内存使用量
    CPU_USAGE_MAX = "cpu_usage_max"    # CPU使用率峰值
    MEM_USAGE_MAX = "mem_usage_max"    # 内存使用量峰值
    RESOURCE_SAMPLES = "resource_samples"  # 资源采样数量

def generate_mock_health_data(count=100, structured=True, record_types=None):
    """
    生成模拟健康数据，用于PIR实验
    
    Args:
        count: 生成的记录数量
        structured: 是否使用结构化数据（True）或随机数据（False）
        record_types: 指定生成的记录类型列表，为None则使用所有类型
        
    Returns:
        生成的模拟健康数据列表
    """
    mock_data = []
    
    # 如果未指定记录类型，使用一些常见类型
    if not record_types:
        record_types = [
            "GENERAL_CHECKUP",
            "LAB_RESULT",
            "IMAGING",
            "PRESCRIPTION",
            "SURGICAL",
            "DIAGNOSIS",
            "VITAL_SIGN",
            "VACCINATION",
            "ALLERGY",
            "MEDICAL_HISTORY",
            "FOLLOW_UP"
        ]
    
    # 模拟患者和医生ID范围
    patient_ids = list(range(1001, 1050))
    doctor_ids = list(range(2001, 2020))
    
    # 模拟疾病列表
    diseases = ["高血压", "糖尿病", "冠心病", "肺炎", "胃炎", "头痛", "感冒", "过敏", "哮喘", "抑郁症", "肺结核", "脑卒中"]
    
    # 模拟药物列表
    medications = ["阿司匹林", "布洛芬", "氯雷他定", "二甲双胍", "苯磺酸氨氯地平", "辛伐他汀", "甲状腺素钠", "氨苯砜", "呋塞米", "氯沙坦钾"]
    
    # 模拟疫苗列表
    vaccines = ["新冠疫苗", "流感疫苗", "乙肝疫苗", "麻疹疫苗", "肺炎疫苗", "百白破疫苗", "脊髓灰质炎疫苗", "水痘疫苗"]
    
    # 模拟手术类型
    surgery_types = ["阑尾切除术", "胆囊切除术", "心脏搭桥术", "髋关节置换术", "白内障手术", "剖腹产", "扁桃体切除术", "胃旁路术", "腹腔镜手术"]
    
    # 模拟过敏源
    allergies = ["花粉", "尘螨", "海鲜", "花生", "牛奶", "青霉素", "小麦", "鸡蛋", "大豆", "乳胶"]
    
    # 模拟病史
    medical_histories = ["哮喘病史", "心脏病史", "高血压病史", "糖尿病病史", "癫痫病史", "肿瘤病史", "抑郁症病史", "骨折病史", "家庭遗传病史"]
    
    # 模拟随访类型
    follow_up_types = ["术后随访", "药物治疗随访", "康复治疗随访", "疾病管理随访", "精神健康随访", "慢性病随访", "血糖控制随访"]
        
    # 生成记录的起始时间和结束时间
    start_date = datetime(2020, 1, 1)
    end_date = datetime.now()
    date_range = (end_date - start_date).days
    
    for i in range(count):
        if structured:
            # 生成结构化的模拟健康数据
            record_type = random.choice(record_types)
            patient_id = random.choice(patient_ids)
            doctor_id = random.choice(doctor_ids)
            
            # 模拟记录日期
            random_days = random.randint(0, date_range)
            record_date = start_date + timedelta(days=random_days)
            
            # 基础记录数据
            record = {
                "_id": ObjectId(),
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "record_type": record_type,
                "title": f"{random.choice(diseases)}检查记录",
                "description": f"患者{patient_id}的{record_type}记录，记录日期为{record_date}",
                "record_date": record_date,
                "created_at": datetime.now(),
                "visibility": "researcher",  # 设为研究员可见
                "is_encrypted": random.choice([True, False]),
                "pir_protected": True  # 默认设置为PIR保护
            }
            
            # 根据记录类型添加特定字段
            if record_type == "LAB_RESULT":
                record.update({
                    "blood_pressure": f"{random.randint(90, 140)}/{random.randint(60, 90)}",
                    "heart_rate": random.randint(60, 100),
                    "blood_sugar": round(random.uniform(3.9, 10.0), 1),
                    "cholesterol": round(random.uniform(2.8, 6.5), 1)
                })
            elif record_type == "PRESCRIPTION":
                record.update({
                    "medications": random.sample(medications, random.randint(1, 3)),
                    "dosage": [f"{random.randint(1, 3)}次/天" for _ in range(random.randint(1, 3))],
                    "duration": f"{random.randint(1, 14)}天"
                })
            elif record_type == "DIAGNOSIS":
                record.update({
                    "diagnosis": random.choice(diseases),
                    "severity": random.choice(["轻度", "中度", "重度"]),
                    "notes": f"患者表现出{random.choice(diseases)}的典型症状"
                })  
            elif record_type == "VITAL_SIGN":
                record.update({
                    "temperature": round(random.uniform(36.5, 37.5), 1),
                    "blood_pressure": f"{random.randint(90, 140)}/{random.randint(60, 90)}",
                    "heart_rate": random.randint(60, 100)
                })
            elif record_type == "VACCINATION":
                record.update({
                    "vaccine_type": random.choice(vaccines),
                    "date": record_date,
                    "notes": f"接种了{random.choice(vaccines)}疫苗"
                })      
            elif record_type == "SURGICAL":
                record.update({
                    "surgery_type": random.choice(surgery_types),
                    "notes": f"进行了{random.choice(surgery_types)}手术"
                })
            elif record_type == "ALLERGY":
                record.update({
                    "allergy_type": random.choice(allergies),
                    "notes": f"对{random.choice(allergies)}过敏"
                })      
            elif record_type == "MEDICAL_HISTORY":
                record.update({
                    "medical_history": random.choice(medical_histories),
                    "notes": f"有{random.choice(medical_histories)}病史"
                })
            elif record_type == "FOLLOW_UP":
                record.update({
                    "follow_up_date": record_date,
                    "notes": f"进行了{random.choice(follow_up_types)}随访"
                })

            # 转换datetime和ObjectId为可序列化格式
            serializable_record = json.loads(json.dumps(record, cls=DateTimeEncoder))
            serializable_record["_id"] = ObjectId(serializable_record["_id"])
            mock_data.append(serializable_record)
        else:
            # 生成随机数据(仅用于PIR算法测试，不包含有意义的医疗信息)
            random_data = np.random.rand(50)  # 生成50维随机向量
            mock_data.append({
                "_id": ObjectId(),
                "data_vector": random_data.tolist(),
                "record_type": random.choice(record_types),
                "pir_protected": True
            })
    
    return mock_data

def calculate_server_load(protocol_type, num_records, vector_size, params):
    """完整的服务端负载计算模型（支持所有PIR协议）"""
    # 基础负载基准（基于协议类型）
    base_load = {
        PIRProtocolType.BASIC: 8,       # 线性扫描基础负载
        PIRProtocolType.HOMOMORPHIC: 35, # 同态加密基础负载
        PIRProtocolType.HYBRID: 18,      # 混合协议基础负载
        PIRProtocolType.ONION: 12       # 洋葱路由基础负载
    }.get(protocol_type, 15)           # 未知协议默认15%

    # 数据规模因子（更精确的非线性增长模型）
    if protocol_type == PIRProtocolType.BASIC:
        # 基本PIR：线性增长
        scale_factor = 1.0 + (num_records / 1000) * 0.08
    elif protocol_type == PIRProtocolType.HOMOMORPHIC:
        # 同态加密：超线性增长
        scale_factor = 1.0 + (num_records / 1000) * 0.15 + 0.05 * np.sqrt(num_records / 1000)
    elif protocol_type == PIRProtocolType.HYBRID:
        # 混合协议：次线性增长（因分块优化）
        partitions = params.get('database_partitions', max(4, int(np.sqrt(num_records))))
        scale_factor = 1.0 + np.sqrt(num_records / 1000) * 0.1
    elif protocol_type == PIRProtocolType.ONION:
        # 洋葱路由：非线性增长（随路由层数指数级增长）
        layers = params.get('layers', 3)
        scale_factor = 1.0 + (num_records / 1000) * 0.1 * layers
    else:
        # 默认线性增长
        scale_factor = 1.0 + 0.08 * np.log1p(num_records * vector_size / 1000)
    
    # 协议特定参数调整
    protocol_factor = 1.0
    if protocol_type == PIRProtocolType.HOMOMORPHIC:
        # 同态加密参数影响
        encryption_bits = params.get('encryption_bits', 2048)
        poly_degree = params.get('polynomial_degree', 4096)
        
        # 加密强度影响（2048位为基准）
        protocol_factor *= 1.0 + 0.15 * (encryption_bits / 2048 - 1)
        
        # 多项式计算复杂度
        protocol_factor *= 1.0 + 0.05 * np.log2(poly_degree / 4096)

        # 数据量对同态计算的影响（CPU负载饱和效应）
        if num_records > 5000:
            cpu_saturation = min(2.0, 1.0 + 0.2 * np.log10(num_records / 5000))
            protocol_factor *= cpu_saturation

    elif protocol_type == PIRProtocolType.HYBRID:
        # 混合协议分块优化
        partitions = max(4, params.get('database_partitions', 4))
        # 自动根据数据量调整分区数 
        if params.get('auto_partition', True) and num_records > 1000:
            partitions = max(partitions, int(np.sqrt(num_records)))
        
        protocol_factor *= 0.9 ** np.log2(partitions/4)  # 每翻倍分区降低10%负载

    elif protocol_type == PIRProtocolType.ONION:
        # 洋葱路由网络拓扑影响
        layers = max(1, params.get('layers', 3))
        nodes = params.get('nodes_per_layer', 5)
        
        # 层数影响（每层+8%）
        protocol_factor *= 1.0 + 0.08 * layers
        
        # 节点数影响（指数衰减）
        protocol_factor *= 1.0 + 0.02 * np.sqrt(nodes)
        
        # 大规模数据的路由负载
        if num_records > 2000:
            protocol_factor *= 1.0 + 0.15 * np.log10(num_records / 2000)

    # 动态衰减因子（基于向量维度）
    dim_decay = 1.0 - 0.1 * np.log1p(vector_size / 50)  # 50维为基准
    dim_decay = max(0.7, min(1.2, dim_decay))  # 限制衰减幅度

    # 最终计算（包含所有调整因子）
    final_load = base_load * scale_factor * protocol_factor * dim_decay
    
    # 边界约束与格式化
    return round(max(5.0, min(95.0, final_load)), 1)  # 限制在5%-95%之间

def calculate_client_load(protocol_type, vector_size, params):
    """完整的客户端负载计算模型（支持所有协议类型）"""
    # 获取数据库记录数
    num_records = params.get('num_records', 1000)
    
    # 基础通信开销（所有协议共有）
    base_communication = 0.5  # 最低通信开销0.5%

    # 协议特定基础负载（根据不同协议设置不同基准）
    base_loads = {
        PIRProtocolType.BASIC: 2.0,      # 基本PIR基础负载
        PIRProtocolType.HOMOMORPHIC: 5.0, # 同态加密基础负载
        PIRProtocolType.HYBRID: 3.0,      # 混合协议基础负载
        PIRProtocolType.ONION: 4.0        # 洋葱路由基础负载
    }
    load = base_loads.get(protocol_type, 2.0)  # 默认2.0%
    
    # 根据协议类型计算随数据规模变化的负载
    if protocol_type == PIRProtocolType.BASIC:
        # 基本PIR协议：线性增长
        # 公式：0.1% per 1000条记录 (反映查询向量生成开销)
        load += 0.1 * (num_records / 1000)  # 每千条记录增加0.1%
        
    elif protocol_type == PIRProtocolType.HOMOMORPHIC:
        # 同态加密协议：次线性增长
        encryption_bits = params.get('encryption_bits', 2048)
        polynomial_degree = params.get('polynomial_degree', 4096)
        
        # 加密操作开销（与向量维度和加密强度相关）
        encrypt_cost = vector_size * 0.015 * (encryption_bits / 2048)
        
        # 多项式计算开销（对数衰减）
        poly_cost = 2.5 * np.log1p(polynomial_degree / 1024)
        
        # 数据规模增加导致的参数调整影响（次线性）
        data_scale_factor = 1.0 + 0.05 * np.log10(num_records / 1000 + 1)
        
        load += (encrypt_cost + poly_cost) * data_scale_factor

    elif protocol_type == PIRProtocolType.HYBRID:
        # 混合协议：分区导致的中等增长
        partitions = params.get('database_partitions', 4)
        encryption_bits = params.get('encryption_bits', 1024)
        
        # 自动根据数据量调整分区数
        if params.get('auto_partition', True) and num_records > 1000:
            partitions = max(partitions, int(np.sqrt(num_records)))
        
        # 分块处理开销（分区越多单块开销越低）
        partition_cost = 1.2 * np.sqrt(partitions / 4)
        
        # 轻量级加密开销
        encrypt_cost = 0.8 * (encryption_bits / 1024) * np.log1p(vector_size)
        
        load += partition_cost + encrypt_cost

    elif protocol_type == PIRProtocolType.ONION:
        # 洋葱路由协议：显著增加
        layers = params.get('layers', 3)
        nodes_per_layer = params.get('nodes_per_layer', 5)
        
        # 多层加密开销（每层基础2%，节点数影响）
        layer_cost = 2.0 * layers * (1 + 0.1 * nodes_per_layer)
        
        # 路径验证开销
        verification_cost = 0.5 * layers
        
        # 数据规模影响（较大影响）
        data_scale_impact = 1.0 + 0.2 * (num_records / 1000)
        
        load += (layer_cost + verification_cost) * data_scale_impact

    else:
        # 未知协议默认负载
        load = 15.0  # 保守估计值

    # 动态调整因子
    dynamic_factor = 1.0
    
    # 向量维度衰减因子（维度越大，单位维度开销越低）
    dim_decay = 1.0 - 0.2 * np.log1p(vector_size / 50)  # 50维为基准
    dynamic_factor *= max(0.6, dim_decay)  # 衰减下限60%

    # 最终计算（叠加基础通信开销）
    total_load = base_communication + (load * dynamic_factor)
    
    # 边界约束与格式化
    return round(max(0.5, min(95.0, total_load)), 2)  # 限制在0.5%-95%之间

def calculate_communication_cost(protocol_type, num_records, vector_size, params):
    """
    精细化通信成本计算函数
    
    Args:
        protocol_type: PIR协议类型
        num_records: 数据库记录数量
        vector_size: 数据向量维度
        params: 协议参数配置字典
        
    Returns:
        通信成本（单位：字节）
    """
    # 基础协议参数
    encryption_bits = params.get('encryption_bits', 2048)
    noise_level = params.get('noise_level', 0.0)
    layers = params.get('layers', 3)
    nodes_per_layer = params.get('nodes_per_layer', 5)
    
    # 自动分区调整
    partitions = params.get('database_partitions', 4)
    if params.get('auto_partition', True) and protocol_type == PIRProtocolType.HYBRID:
        partitions = max(partitions, int(np.sqrt(num_records)))
    
    # 噪声处理相关参数
    noise_data_factor = 1.0 + 0.2 * noise_level  # 噪声数据膨胀系数
    metadata_size = 128  # 元数据基础大小（协议类型、时间戳等）
    
    # 分阶段计算模型
    request_cost = 0    # 请求阶段通信量
    response_cost = 0   # 响应阶段通信量
    
    # 协议特定计算模型 - 基于实验数据规模变化
    if protocol_type == PIRProtocolType.BASIC:
        # 基础PIR：线性增长（传输完整查询向量 + 噪声数据）
        # 从1000到10000条记录，通信量增加10倍
        element_size = 4 * noise_data_factor
        request_cost = num_records * element_size  # 线性增长
        response_cost = vector_size * 4  # 响应向量不随数据量变化
        
    elif protocol_type == PIRProtocolType.HOMOMORPHIC:
        # 同态加密：基本稳定（仅略微增加）
        # 从1000到10000条记录，通信量仅增加约20%
        cipher_size = (encryption_bits // 8) * 2
        poly_degree = params.get('polynomial_degree', 4096)
        
        # 请求阶段：仅索引信息与数据量相关，加密查询部分基本不变
        request_base = 512  # 基础请求大小
        index_overhead = np.log2(max(2, num_records)) * 8  # 索引开销
        
        # 加密查询成本基本不变，轻微增加以适应更大数据库
        encrypt_query_cost = vector_size * cipher_size * (1 + 0.05*poly_degree/4096)
        # 数据量增加对请求大小的影响很小
        db_size_factor = 1.0 + 0.02 * np.log10(num_records / 1000 + 1)
        
        request_cost = request_base + index_overhead + (encrypt_query_cost * db_size_factor)
        
        # 响应阶段：基本不变，仅加一点验证数据
        response_cost = (vector_size * cipher_size) + 256  # 固定大小
        
        # 响应略微增加（约20%）
        response_cost *= 1.0 + 0.02 * np.log10(num_records / 1000 + 1)
        
    elif protocol_type == PIRProtocolType.HYBRID:
        # 混合协议：中等增长（随分区数增加）
        # 从1000到10000条记录，通信量增加约3-5倍（因分区数约增加3倍）
        partition_size = max(1, num_records // partitions)
        index_bits = np.ceil(np.log2(partitions))
        
        # 请求阶段：分区元数据 + 局部查询
        request_cost = (index_bits / 8) + (partition_size * 4)
        
        # 响应阶段：加密块数据 + 块校验
        block_response = (vector_size * (encryption_bits//16))
        validation_data = partitions * 32  # 每分区32字节校验
        
        response_cost = block_response + validation_data
        
    elif protocol_type == PIRProtocolType.ONION:
        # 洋葱路由：线性增长（与数据量成正比）
        # 从1000到10000条记录，通信量增加约10倍
        layer_overhead = 64 * layers
        payload_size = vector_size * 4 * (num_records / 1000)  # 线性增长
        
        # 路由信息
        route_info = (layers * 128) + (nodes_per_layer * 16)
        # 数据请求信息随数据规模增长
        data_request = num_records * 0.05  # 每记录0.05字节的请求信息
        
        request_cost = route_info + data_request
        
        # 响应负载
        response_cost = (payload_size * (1.1**layers)) + layer_overhead
        
    else:
        # 默认线性增长
        request_cost = num_records * 4
        response_cost = vector_size * 4
    
    # 噪声处理额外开销
    if noise_level > 0:
        request_cost += 32  # 噪声参数元数据
        response_cost *= (1 + 0.5 * noise_level)
    
    # 添加基础元数据
    total_cost = request_cost + response_cost + metadata_size
    
    # 小规模优化系数（仅对大规模数据有效）
    if num_records > 10000:
        optimization_factor = 1.0 - 0.15 * np.log10(num_records / 10000)
        total_cost *= max(0.7, optimization_factor)
    
    # 协议复杂度调整
    complexity_adjust = {
        PIRProtocolType.BASIC: 1.0,
        PIRProtocolType.HOMOMORPHIC: 1.2,
        PIRProtocolType.HYBRID: 0.9,
        PIRProtocolType.ONION: 1.15
    }.get(protocol_type, 1.0)
    
    return int(total_cost * complexity_adjust)

def evaluate_accuracy(actual, expected):
    """基于余弦相似度的准确率评估"""
    if actual is None or expected is None:
        return 0.0
    
    actual = np.array(actual).flatten()
    expected = np.array(expected).flatten()
    
    min_len = min(len(actual), len(expected))
    actual = actual[:min_len]
    expected = expected[:min_len]
    
    if np.linalg.norm(actual) == 0 or np.linalg.norm(expected) == 0:
        return 0.0 if not np.array_equal(actual, expected) else 1.0
    
    similarity = cosine_similarity([actual], [expected])[0][0]
    return max(0.0, similarity)

def simulate_network_latency(layers):
    """基于层数的网络延迟模拟"""
    base_latency = 0.1  # 基础延迟100ms
    total_latency = 0
    for _ in range(layers):
        hop_latency = base_latency + random.uniform(-0.05, 0.05)
        total_latency += max(0.05, hop_latency)
        time.sleep(hop_latency)
    return total_latency

def calculate_privacy_level(protocol_config):
    """综合隐私评估模型"""
    protocol_type = protocol_config.get('protocol_type')
    base_scores = {
        PIRProtocolType.BASIC: 3.0,
        PIRProtocolType.HOMOMORPHIC: 8.5,
        PIRProtocolType.HYBRID: 6.0,
        PIRProtocolType.ONION: 7.0
    }
    
    noise_bonus = min(2.0, protocol_config.get('noise_level',0)*10)
    key_bits = protocol_config.get('encryption_bits',128)
    crypto_strength = min(2.0, (key_bits-128)/512)
    
    topology_bonus = 0
    if protocol_type == PIRProtocolType.ONION:
        topology_bonus = min(1.5, protocol_config.get('layers',3)*0.5)
    
    return min(10.0, base_scores.get(protocol_type, 5.0) 
               + noise_bonus 
               + crypto_strength 
               + topology_bonus)

def configure_pir_protocol(protocol_type, params=None):
    """
    配置PIR协议参数
    
    Args:
        protocol_type: PIR协议类型
        params: 协议特定参数
        
    Returns:
        配置好的PIR协议参数
    """
    default_params = {
        PIRProtocolType.BASIC: {
            "noise_level": 0.0,
            "query_expansion": 1,
            "database_padding": 0
        },
        PIRProtocolType.HOMOMORPHIC: {
            "encryption_bits": 1024,
            "noise_level": 0.1,
            "polynomial_degree": 4096
        },
        PIRProtocolType.HYBRID: {
            "encryption_bits": 2048,
            "noise_level": 0.05,
            "compression_ratio": 0.8,
            "database_partitions": 4
        },
        PIRProtocolType.ONION: {
            "layers": 3,
            "nodes_per_layer": 5,
            "timeout_ms": 500
        }
    }
    
    # 如果未提供协议参数，使用默认值
    if not params:
        params = {}
    
    # 合并默认参数和提供的参数
    if protocol_type in default_params:
        config = default_params[protocol_type].copy()
        config.update(params)
    else:
        # 如果未知协议类型，使用基本PIR协议
        config = default_params[PIRProtocolType.BASIC].copy()
        config.update(params)
    
    # 添加协议类型到配置中
    config["protocol_type"] = protocol_type
    
    return config

@monitor_resources
def execute_pir_query_experiment(data, target_indices, protocol_config):
    """
    执行PIR查询实验
    
    Args:
        data: 用于查询的数据
        target_indices: 要查询的目标索引列表
        protocol_config: PIR协议配置
        
    Returns:
        查询结果和性能指标
    """
    from ..utils.pir_utils import PIRQuery
    
    results = []
    metrics = {
        PIRPerformanceMetric.QUERY_TIME: [],
        PIRPerformanceMetric.ACCURACY: [],
        PIRPerformanceMetric.COMMUNICATION_COST: [],
        PIRPerformanceMetric.SERVER_LOAD: [],
        PIRPerformanceMetric.CLIENT_LOAD: [],
        PIRPerformanceMetric.PRIVACY_LEVEL: [],
        PIRPerformanceMetric.CPU_USAGE: [],
        PIRPerformanceMetric.MEM_USAGE: [],
        PIRPerformanceMetric.CPU_USAGE_MAX: [],
        PIRPerformanceMetric.MEM_USAGE_MAX: [],
        PIRPerformanceMetric.RESOURCE_SAMPLES: []
    }
    
    # 记录实验开始时间
    start_time = time.time()
    
    # 将数据转换为矩阵形式
    data_matrix = []
    for item in data:
        # 检查是否有data_vector字段
        if "data_vector" in item:
            data_matrix.append(item["data_vector"])
        else:
            # 如果没有，将对象转换为向量
            vector = PIRQuery.encode_health_record(item)
            data_matrix.append(vector)
    
    # 转换为numpy数组
    data_matrix = np.array(data_matrix)
    
    # 获取数据维度信息
    num_records = len(data_matrix)
    vector_size = data_matrix.shape[1] if len(data_matrix.shape) > 1 else 1
    
    # 记录数据规模信息，便于调试和分析
    current_app.logger.info(f"PIR实验数据规模: {num_records}条记录, 向量维度: {vector_size}")
    
    # 将数据库规模添加到协议参数中，以便负载计算函数使用
    protocol_config['num_records'] = num_records
    
    # 按协议类型设置加密密钥大小（单位：比特）
    key_sizes = {
        PIRProtocolType.BASIC: 128,
        PIRProtocolType.HOMOMORPHIC: protocol_config.get("encryption_bits", 2048),
        PIRProtocolType.HYBRID: protocol_config.get("encryption_bits", 1024),
        PIRProtocolType.ONION: 256
    }
    
    # 根据不同的协议类型执行查询
    protocol_type = protocol_config.get("protocol_type", PIRProtocolType.BASIC)
    
    # 在这里记录系统资源基线状态
    if hasattr(psutil, 'cpu_percent'):
        baseline_cpu = psutil.cpu_percent(interval=0.1)
    else:
        baseline_cpu = 0
    
    if hasattr(psutil, 'virtual_memory'):
        baseline_mem = psutil.virtual_memory().percent
    else:
        baseline_mem = 0
    
    current_app.logger.info(f"基线CPU使用率: {baseline_cpu}%, 内存使用率: {baseline_mem}%")
    
    for target_idx in target_indices:
        if target_idx >= num_records:
            current_app.logger.warning(f"目标索引{target_idx}超出数据范围{num_records}，将跳过")
            continue
            
        query_start_time = time.time()
        result = None
        expected_result = data_matrix[target_idx] if target_idx < len(data_matrix) else None
        accuracy = 1.0  # 默认准确率
        query_result = {}  # 用于存储包含资源使用情况的结果
        
        try:
            # 基本PIR协议
            if protocol_type == PIRProtocolType.BASIC:
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 添加可配置的噪声
                noise_level = protocol_config.get("noise_level", 0.0)
                if noise_level > 0:
                    noise = np.random.normal(0, noise_level, query_vector.shape)
                    query_vector = query_vector + noise
                    query_vector = np.clip(query_vector, 0, 1)
                
                # 执行查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
            # 同态加密PIR - 基于Paillier同态加密
            elif protocol_type == PIRProtocolType.HOMOMORPHIC:
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 精确的加密耗时模拟
                key_bit_size = key_sizes[PIRProtocolType.HOMOMORPHIC]
                encryption_time = (key_bit_size / 1024) * 0.05  # 模拟密钥大小对加密时间的影响
                time.sleep(encryption_time)  # 模拟加密耗时
                
                # 执行同态加密下的查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
                # 模拟解密开销
                decryption_time = (key_bit_size / 1024) * 0.03
                time.sleep(decryption_time)  # 模拟解密耗时
                
                # 添加同态加密的随机误差
                result = result + np.random.normal(0, 0.001, result.shape)
                
            # 混合PIR协议 - 结合局部数据库和加密方法
            elif protocol_type == PIRProtocolType.HYBRID:
                # 获取或计算分区数
                partitions = protocol_config.get("database_partitions", 4)
                if protocol_config.get("auto_partition", True):
                    partitions = max(partitions, int(np.sqrt(num_records)))
                protocol_config["database_partitions"] = partitions
                
                # 划分数据库
                partition_size = max(1, num_records // partitions)
                
                # 记录分区信息
                current_app.logger.info(f"HYBRID PIR使用{partitions}个分区，每分区约{partition_size}条记录")
                
                # 确定目标在哪个分区
                target_partition = target_idx // partition_size
                local_idx = target_idx % partition_size
                
                # 创建局部查询向量
                query_vector = PIRQuery.create_query_vector(partition_size, local_idx)
                
                # 混合PIR需要一次轻量级加密
                key_bit_size = key_sizes[PIRProtocolType.HYBRID]
                encryption_time = (key_bit_size / 1024) * 0.01
                time.sleep(encryption_time)  # 模拟加密耗时
                
                # 提取目标分区数据
                start_idx = target_partition * partition_size
                end_idx = min(start_idx + partition_size, len(data_matrix))
                partition_data = data_matrix[start_idx:end_idx]
                
                # 执行查询
                if len(partition_data) > 0:
                    result = PIRQuery.process_query(partition_data, query_vector)
                else:
                    # 处理边界情况
                    result = np.zeros(vector_size)
                    accuracy = 0.0
                
            # 洋葱路由PIR - 多层加密路由
            elif protocol_type == PIRProtocolType.ONION:
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 获取洋葱路由层数
                layers = protocol_config.get("layers", 3)
                
                # 洋葱路由网络延迟模拟
                network_latency = simulate_network_latency(layers)
                
                # 执行查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
                # 模拟多层解密
                for layer in range(layers):
                    layer_decryption_time = 0.008 * (layers - layer)  # 模拟每层解密时间递减
                    time.sleep(layer_decryption_time)
                
            else:
                # 未知协议类型，使用基本PIR
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                result = PIRQuery.process_query(data_matrix, query_vector)
                
            # 准确率评估改进
            accuracy = evaluate_accuracy(result, expected_result)
            
            # 使用新的通信成本计算函数
            comm_cost = calculate_communication_cost(
                protocol_type, 
                num_records, 
                vector_size, 
                protocol_config
            )
            
        except Exception as e:
            current_app.logger.error(f"执行查询失败: {str(e)}")
            result = np.zeros(vector_size if vector_size > 1 else 1)
            accuracy = 0.0
            comm_cost = 0
        
        query_end_time = time.time()
        query_time = query_end_time - query_start_time
        
        # 计算服务器和客户端负载
        server_load = calculate_server_load(protocol_type, num_records, vector_size, protocol_config)
        client_load = calculate_client_load(protocol_type, vector_size, protocol_config)
        privacy_level = calculate_privacy_level(protocol_config)
        
        # 记录详细的计算过程（便于调试）
        current_app.logger.debug(
            f"[性能指标] 协议:{protocol_type}, 记录数:{num_records}, "
            f"服务器负载:{server_load}, 客户端负载:{client_load}, "
            f"通信成本:{comm_cost}字节, 查询时间:{query_time:.6f}秒"
        )
        
        # 记录性能指标
        metrics[PIRPerformanceMetric.QUERY_TIME].append(query_time)
        metrics[PIRPerformanceMetric.COMMUNICATION_COST].append(comm_cost)
        metrics[PIRPerformanceMetric.SERVER_LOAD].append(server_load)
        metrics[PIRPerformanceMetric.CLIENT_LOAD].append(client_load)
        metrics[PIRPerformanceMetric.PRIVACY_LEVEL].append(privacy_level)
        metrics[PIRPerformanceMetric.ACCURACY].append(accuracy)
        
        # 添加到结果列表
        results.append({
            "target_index": target_idx,
            "result": result.tolist() if isinstance(result, np.ndarray) else result,
            "original_data": data[target_idx] if target_idx < len(data) else None,
            "query_time": query_time,
            "comm_cost": comm_cost,
            "accuracy": accuracy,
            "privacy_level": privacy_level
        })
    
    # 记录实验结束时间
    end_time = time.time()
    total_time = end_time - start_time
    
    # 计算平均指标
    avg_metrics = {}
    for metric_name, values in metrics.items():
        if values:
            avg_metrics[metric_name] = np.mean(values)
    
    # 添加资源监控数据（会由monitor_resources装饰器填充）
    result_dict = {
        "results": results,
        "metrics": avg_metrics,
        "protocol": protocol_config,
        "total_query_time": total_time,
        "start_time": datetime.fromtimestamp(start_time),
        "end_time": datetime.fromtimestamp(end_time)
    }
    
    # 记录实验日志
    log_pir(
        message=f"执行PIR实验查询，协议类型: {protocol_type}",
        details={
            "protocol_config": protocol_config,
            "data_size": len(data),
            "target_count": len(target_indices),
            "metrics": avg_metrics,
            "baseline_cpu": baseline_cpu,
            "baseline_mem": baseline_mem
        }
    )
    
    return result_dict

def analyze_experiment_results(experiment_results, baseline_results=None):
    """
    分析实验结果，比较不同协议性能
    
    Args:
        experiment_results: 当前实验结果
        baseline_results: 基准实验结果(可选)
        
    Returns:
        分析报告
    """
    report = {
        "summary": {},
        "comparisons": {},
        "recommendations": []
    }
    
    # 提取当前实验指标
    current_metrics = experiment_results.get("metrics", {})
    current_protocol = experiment_results.get("protocol", {}).get("protocol_type", "unknown")
    
    # 汇总当前实验数据
    report["summary"] = {
        "protocol": current_protocol,
        "query_time": current_metrics.get(PIRPerformanceMetric.QUERY_TIME, 0),
        "accuracy": current_metrics.get(PIRPerformanceMetric.ACCURACY, 0),
        "comm_cost": current_metrics.get(PIRPerformanceMetric.COMMUNICATION_COST, 0),
        "privacy_level": current_metrics.get(PIRPerformanceMetric.PRIVACY_LEVEL, 0),
        "cpu_usage": current_metrics.get(PIRPerformanceMetric.CPU_USAGE, 0),
        "mem_usage": current_metrics.get(PIRPerformanceMetric.MEM_USAGE, 0),
        "cpu_usage_max": current_metrics.get(PIRPerformanceMetric.CPU_USAGE_MAX, 0),
        "mem_usage_max": current_metrics.get(PIRPerformanceMetric.MEM_USAGE_MAX, 0),
        "resource_samples": current_metrics.get(PIRPerformanceMetric.RESOURCE_SAMPLES, 0),
        "total_query_time": experiment_results.get("total_query_time", 0),
        "timestamp": datetime.now().isoformat()
    }
    
    # 添加资源监控有效性检查
    has_resource_data = report["summary"]["cpu_usage"] > 0 or report["summary"]["mem_usage"] > 0
    if not has_resource_data:
        report["recommendations"].append(
            "未检测到有效的资源监控数据，请检查监控设置或系统配置是否正确"
        )
    
    # 生成硬件资源建议
    if report["summary"]["cpu_usage"] > 50:  # CPU使用率超过50%
        report["recommendations"].append(
            "检测到高CPU使用率，建议使用硬件加速或优化加密算法参数"
        )
    if report["summary"]["mem_usage"] > 100:  # 内存使用超过100MB
        report["recommendations"].append(
            "检测到高内存消耗，建议优化数据分块策略或使用流式处理"
        )
    
    # 协议特定建议
    if current_protocol == PIRProtocolType.HOMOMORPHIC:
        if report["summary"]["comm_cost"] < 1024:  # 通信成本小于1KB
            report["recommendations"].append(
                "同态加密参数可能过于激进，建议增加加密强度提升隐私级别"
            )
        
        if report["summary"]["cpu_usage"] > 70:  # CPU使用率超过70%
            report["recommendations"].append(
                "同态加密计算开销较高，建议增加服务器计算能力或使用专用加密硬件"
            )
    
    # 理论模型与实际资源使用的一致性检查
    server_load = current_metrics.get(PIRPerformanceMetric.SERVER_LOAD, 0)
    cpu_usage = current_metrics.get(PIRPerformanceMetric.CPU_USAGE, 0)
    if server_load > 1000 and cpu_usage < 10:
        # 理论负载高但实际CPU使用率低，可能表明算法模型与实际不符
        report["recommendations"].append(
            "理论服务器负载与实际CPU使用率不一致，建议校准负载计算模型"
        )
    
    # 如果有基准结果，进行比较分析
    if baseline_results:
        baseline_metrics = baseline_results.get("metrics", {})
        baseline_protocol = baseline_results.get("protocol", {}).get("protocol_type", "unknown")
        
        # 计算性能差异
        for metric in [PIRPerformanceMetric.QUERY_TIME, 
                      PIRPerformanceMetric.ACCURACY,
                      PIRPerformanceMetric.COMMUNICATION_COST,
                      PIRPerformanceMetric.SERVER_LOAD,
                      PIRPerformanceMetric.CLIENT_LOAD,
                      PIRPerformanceMetric.PRIVACY_LEVEL,
                      PIRPerformanceMetric.CPU_USAGE,
                      PIRPerformanceMetric.MEM_USAGE,
                      PIRPerformanceMetric.CPU_USAGE_MAX,
                      PIRPerformanceMetric.MEM_USAGE_MAX]:
            
            current_value = current_metrics.get(metric, 0)
            baseline_value = baseline_metrics.get(metric, 0)
            
            if baseline_value != 0:
                diff_percent = ((current_value - baseline_value) / baseline_value) * 100
            else:
                diff_percent = 0
                
            is_improvement = False
            # 对于查询时间、通信成本、服务器负载、客户端负载、CPU和内存使用，较低的值更好
            if metric in [PIRPerformanceMetric.QUERY_TIME, 
                         PIRPerformanceMetric.COMMUNICATION_COST,
                         PIRPerformanceMetric.SERVER_LOAD,
                         PIRPerformanceMetric.CLIENT_LOAD,
                         PIRPerformanceMetric.CPU_USAGE,
                         PIRPerformanceMetric.MEM_USAGE,
                         PIRPerformanceMetric.CPU_USAGE_MAX,
                         PIRPerformanceMetric.MEM_USAGE_MAX]:
                is_improvement = diff_percent < 0
            else:  # 对于准确率和隐私级别，较高的值更好
                is_improvement = diff_percent > 0
                
            report["comparisons"][metric] = {
                "current": current_value,
                "baseline": baseline_value,
                "diff_percent": diff_percent,
                "is_improvement": is_improvement
            }
            
        # 生成比较建议
        if report["comparisons"].get(PIRPerformanceMetric.QUERY_TIME, {}).get("diff_percent", 0) > 20:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 比 {baseline_protocol} 查询时间增加超过20%，"
                "考虑优化查询参数或使用更高效的协议"
            )
            
        if report["comparisons"].get(PIRPerformanceMetric.PRIVACY_LEVEL, {}).get("diff_percent", 0) < -10:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 隐私保护级别比 {baseline_protocol} 低10%以上，"
                "考虑使用更强的隐私保护协议"
            )
            
        if report["comparisons"].get(PIRPerformanceMetric.COMMUNICATION_COST, {}).get("is_improvement", False):
            report["recommendations"].append(
                f"当前协议 {current_protocol} 通信成本比 {baseline_protocol} 更优，"
                "适合带宽有限的环境"
            )
            
        # 资源使用比较建议
        if report["comparisons"].get(PIRPerformanceMetric.CPU_USAGE, {}).get("diff_percent", 0) > 30:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 的CPU使用率比 {baseline_protocol} 高30%以上，"
                "考虑减少计算复杂度或使用更高效的算法实现"
            )
            
        if report["comparisons"].get(PIRPerformanceMetric.MEM_USAGE, {}).get("diff_percent", 0) > 30:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 的内存使用比 {baseline_protocol} 高30%以上，"
                "考虑优化内存使用模式或增加服务器资源"
            )
    else:
        # 没有基准结果时，根据绝对指标生成建议
        query_time = current_metrics.get(PIRPerformanceMetric.QUERY_TIME, 0)
        if query_time > 1.0:  # 查询时间超过1秒
            report["recommendations"].append(
                "查询时间较长，考虑使用数据库分区或缓存优化"
            )
            
        privacy_level = current_metrics.get(PIRPerformanceMetric.PRIVACY_LEVEL, 0)
        if privacy_level < 5:  # 隐私保护级别较低
            report["recommendations"].append(
                "隐私保护级别较低，建议升级到同态加密或混合PIR协议"
            )
            
        accuracy = current_metrics.get(PIRPerformanceMetric.ACCURACY, 0)
        if accuracy < 0.95:  # 准确率低于95%
            report["recommendations"].append(
                "查询准确率较低，建议检查数据质量或优化查询算法"
            )
    
    return report 