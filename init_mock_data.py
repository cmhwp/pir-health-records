#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
初始化模拟数据脚本

用法:
python init_mock_data.py

此脚本会创建以下模拟数据:
1. 医疗机构数据
2. 记录类型数据
3. 用户数据 (医生和患者)
"""

import os
import sys
import random
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

# 将应用目录添加到系统路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 创建应用上下文
from app import create_app
app = create_app(os.getenv('FLASK_ENV', 'development'))

# 导入必要的模型
from app.models import db
from app.models.user import User, Role
from app.models.role_models import PatientInfo, DoctorInfo, ResearcherInfo
from app.models.institution import Institution, CustomRecordType

def create_institutions():
    """创建模拟医疗机构数据"""
    print("正在创建医疗机构数据...")
    
    # 检查是否已存在机构数据
    existing_count = Institution.query.count()
    if existing_count > 0:
        print(f"已存在 {existing_count} 个医疗机构记录，跳过创建...")
        return
    
    institutions = [
        {
            'name': '北京协和医院',
            'code': 'BJXH001',
            'address': '北京市东城区帅府园1号',
            'phone': '010-69156114',
            'email': 'contact@pumch.cn',
            'website': 'https://www.pumch.cn',
            'description': '中国最早的西医院之一，是集医疗、教学、科研于一体的大型综合医院。',
            'logo_url': 'https://example.com/logos/peking_union.png',
        },
        {
            'name': '上海瑞金医院',
            'code': 'SHRJ002',
            'address': '上海市瑞金二路197号',
            'phone': '021-64370045',
            'email': 'info@rjh.com.cn',
            'website': 'https://www.rjh.com.cn',
            'description': '上海交通大学医学院附属瑞金医院，是一所融医疗、教学、科研、预防为一体的综合性三甲医院。',
            'logo_url': 'https://example.com/logos/ruijin.png',
        },
        {
            'name': '广州中山大学附属第一医院',
            'code': 'GZZSDY003',
            'address': '广州市中山二路58号',
            'phone': '020-87755766',
            'email': 'service@zsufh.com',
            'website': 'http://www.zsufh.com',
            'description': '中山大学附属第一医院始建于1910年，是华南地区规模最大、综合实力最强的大型综合性医院之一。',
            'logo_url': 'https://example.com/logos/zsufh.png',
        },
        {
            'name': '四川大学华西医院',
            'code': 'SCDHX004',
            'address': '四川省成都市国学巷37号',
            'phone': '028-85422114',
            'email': 'info@wchscu.cn',
            'website': 'https://www.wchscu.cn',
            'description': '四川大学华西医院是中国西部地区最大的医疗中心，集医疗、教学、科研、预防为一体。',
            'logo_url': 'https://example.com/logos/huaxi.png',
        },
        {
            'name': '武汉同济医院',
            'code': 'WHTJ005',
            'address': '湖北省武汉市解放大道1095号',
            'phone': '027-83662688',
            'email': 'contact@tjh.com.cn',
            'website': 'https://www.tjh.com.cn',
            'description': '华中科技大学同济医学院附属同济医院，是一所集医疗、教学、科研、急救、康复于一体的大型现代化综合性医院。',
            'logo_url': 'https://example.com/logos/tongji.png',
        },
        {
            'name': '天津医科大学总医院',
            'code': 'TJYK006',
            'address': '天津市和平区鞍山道154号',
            'phone': '022-60362255',
            'email': 'info@tmugs.com',
            'website': 'http://www.tmugs.com',
            'description': '天津医科大学总医院是一所集医疗、教学、科研、预防为一体的现代化综合性大学附属医院。',
            'logo_url': 'https://example.com/logos/tmugs.png',
        },
        {
            'name': '浙江大学医学院附属第一医院',
            'code': 'ZJDY007',
            'address': '浙江省杭州市庆春路79号',
            'phone': '0571-87236666',
            'email': 'webmaster@zjufh.org',
            'website': 'http://www.zy91.com',
            'description': '浙江大学医学院附属第一医院是一所以医疗、教学、科研为主的大型综合性医院。',
            'logo_url': 'https://example.com/logos/zjufh.png',
        },
        {
            'name': '中国医学科学院肿瘤医院',
            'code': 'ZGZL008',
            'address': '北京市朝阳区潘家园南里17号',
            'phone': '010-87788899',
            'email': 'service@cicams.ac.cn',
            'website': 'http://www.cicams.ac.cn',
            'description': '中国医学科学院肿瘤医院是国家级肿瘤专科医院，是集医疗、教学、科研、预防为一体的现代化综合性肿瘤医院。',
            'logo_url': 'https://example.com/logos/cicams.png',
        }
    ]
    
    # 创建机构数据
    for inst_data in institutions:
        institution = Institution(
            name=inst_data['name'],
            code=inst_data['code'],
            address=inst_data['address'],
            phone=inst_data['phone'],
            email=inst_data['email'],
            website=inst_data['website'],
            description=inst_data['description'],
            logo_url=inst_data['logo_url'],
            is_active=True
        )
        db.session.add(institution)
    
    db.session.commit()
    print(f"成功创建 {len(institutions)} 个医疗机构记录")

def create_record_types():
    """创建模拟记录类型数据"""
    print("正在创建记录类型数据...")
    
    # 获取现有记录类型的code列表，用于后面检查重复
    existing_codes = [rt.code for rt in CustomRecordType.query.all()]
    if existing_codes:
        print(f"发现 {len(existing_codes)} 个现有记录类型代码")
    
    record_types = [
        {
            'code': 'GENERAL_CHECKUP',
            'name': '常规检查',
            'description': '常规检查结果',
            'color': '#1890ff',
            'icon': '🏥'
        },
        {
            'code': 'LAB_RESULT',
            'name': '检验报告',
            'description': '各类临床检验结果，包括血常规、尿常规、生化检查等',
            'color': '#1890ff',
            'icon': '📊'
        },
        {
            'code': 'IMAGING',
            'name': '影像学检查',
            'description': 'X光、CT、核磁共振等影像学检查结果',
            'color': '#722ed1',
            'icon': '🔍'
        },
        {
            'code': 'PRESCRIPTION',
            'name': '处方',
            'description': '医生开具的药品处方记录',
            'color': '#52c41a',
            'icon': '💊'
        },
        {
            'code': 'SURGICAL',
            'name': '手术记录',
            'description': '手术过程、方式、结果等记录',
            'color': '#fa541c',
            'icon': '⚕️'
        },
        {
            'code': 'DIAGNOSIS',
            'name': '诊断记录',
            'description': '疾病诊断结果及描述',
            'color': '#13c2c2',
            'icon': '📝'
        },
        {
            'code': 'VITAL_SIGN',
            'name': '生命体征',
            'description': '体温、血压、脉搏、呼吸等生命体征记录',
            'color': '#eb2f96',
            'icon': '❤️'
        },
        {
            'code': 'VACCINATION',
            'name': '疫苗接种',
            'description': '疫苗接种记录，包括接种时间、疫苗种类等',
            'color': '#faad14',
            'icon': '💉'
        },
        {
            'code': 'ALLERGY',
            'name': '过敏记录',
            'description': '药物、食物或其他过敏原的过敏记录',
            'color': '#f5222d',
            'icon': '⚠️'
        },
        {
            'code': 'MEDICAL_HISTORY',
            'name': '既往病史',
            'description': '患者过去的疾病记录和就诊情况',
            'color': '#1d39c4',
            'icon': '📜'
        },
        {
            'code': 'FOLLOW_UP',
            'name': '随访记录',
            'description': '医生对患者的随访和复查记录',
            'color': '#096dd9',
            'icon': '🔄'
        },
        {
            'code': 'PIR_DATA',
            'name': 'PIR数据',
            'description': 'PIR数据',
            'color': '#096dd9',
            'icon': '🔄'
        }
    ]
    
    # 创建记录类型数据
    created_count = 0
    skipped_count = 0
    
    for type_data in record_types:
        # 检查是否已存在该记录类型
        if type_data['code'] in existing_codes:
            print(f"记录类型 {type_data['code']} 已存在，跳过创建...")
            skipped_count += 1
            continue
            
        record_type = CustomRecordType(
            code=type_data['code'],
            name=type_data['name'],
            description=type_data['description'],
            color=type_data['color'],
            icon=type_data['icon'],
            is_active=True
        )
        db.session.add(record_type)
        created_count += 1
    
    try:
        db.session.commit()
        print(f"成功创建 {created_count} 个记录类型，跳过 {skipped_count} 个已存在的记录类型")
    except Exception as e:
        db.session.rollback()
        print(f"创建记录类型时发生错误: {str(e)}")

def create_users():
    """创建模拟用户数据"""
    print("正在创建用户数据...")
    
    # 检查是否已存在大量用户数据
    existing_count = User.query.count()
    if existing_count > 10:  # 除了默认管理员以外还有用户
        print(f"已存在 {existing_count} 个用户记录，跳过创建...")
        return
    
    # 获取机构列表以关联到医生
    institutions = Institution.query.all()
    institution_names = [inst.name for inst in institutions] if institutions else ["默认医院"]
    
    # 创建医生用户
    doctors_data = [
        {
            'username': 'doctor_wang',
            'password': 'Doctor123',
            'email': 'wang@example.com',
            'full_name': '王医生',
            'phone': '13800138001',
            'doctor_info': {
                'specialty': '内科',
                'license_number': 'LIC20230001',
                'years_of_experience': 10,
                'education': '北京医科大学',
                'hospital': random.choice(institution_names),
                'department': '内科',
                'bio': '擅长处理各类内科疾病，尤其是心血管疾病。'
            }
        },
        {
            'username': 'doctor_li',
            'password': 'Doctor123',
            'email': 'li@example.com',
            'full_name': '李医生',
            'phone': '13800138002',
            'doctor_info': {
                'specialty': '外科',
                'license_number': 'LIC20230002',
                'years_of_experience': 15,
                'education': '上海交通大学医学院',
                'hospital': random.choice(institution_names),
                'department': '外科',
                'bio': '专注于普外科手术，有丰富的临床经验。'
            }
        },
        {
            'username': 'doctor_zhang',
            'password': 'Doctor123',
            'email': 'zhang@example.com',
            'full_name': '张医生',
            'phone': '13800138003',
            'doctor_info': {
                'specialty': '妇产科',
                'license_number': 'LIC20230003',
                'years_of_experience': 8,
                'education': '复旦大学医学院',
                'hospital': random.choice(institution_names),
                'department': '妇产科',
                'bio': '专注于妇产科疾病诊治，擅长产科手术。'
            }
        },
        {
            'username': 'doctor_liu',
            'password': 'Doctor123',
            'email': 'liu@example.com',
            'full_name': '刘医生',
            'phone': '13800138004',
            'doctor_info': {
                'specialty': '儿科',
                'license_number': 'LIC20230004',
                'years_of_experience': 12,
                'education': '中山大学医学院',
                'hospital': random.choice(institution_names),
                'department': '儿科',
                'bio': '擅长儿童疾病诊断和治疗，对儿童发育问题有深入研究。'
            }
        },
        {
            'username': 'doctor_chen',
            'password': 'Doctor123',
            'email': 'chen@example.com',
            'full_name': '陈医生',
            'phone': '13800138005',
            'doctor_info': {
                'specialty': '神经科',
                'license_number': 'LIC20230005',
                'years_of_experience': 20,
                'education': '四川大学华西医学院',
                'hospital': random.choice(institution_names),
                'department': '神经科',
                'bio': '在神经系统疾病诊断和治疗方面有丰富经验。'
            }
        }
    ]
    
    # 创建患者用户
    patients_data = [
        {
            'username': 'patient_zhao',
            'password': 'Patient123',
            'email': 'zhao@example.com',
            'full_name': '赵患者',
            'phone': '13900139001',
            'patient_info': {
                'gender': '男',
                'address': '北京市海淀区',
                'emergency_contact': '赵太太',
                'emergency_phone': '13900139101',
                'medical_history': '高血压',
                'allergies': '青霉素'
            }
        },
        {
            'username': 'patient_qian',
            'password': 'Patient123',
            'email': 'qian@example.com',
            'full_name': '钱患者',
            'phone': '13900139002',
            'patient_info': {
                'gender': '女',
                'address': '上海市浦东新区',
                'emergency_contact': '钱先生',
                'emergency_phone': '13900139102',
                'medical_history': '糖尿病',
                'allergies': '无'
            }
        },
        {
            'username': 'patient_sun',
            'password': 'Patient123',
            'email': 'sun@example.com',
            'full_name': '孙患者',
            'phone': '13900139003',
            'patient_info': {
                'gender': '男',
                'address': '广州市天河区',
                'emergency_contact': '孙太太',
                'emergency_phone': '13900139103',
                'medical_history': '无',
                'allergies': '磺胺类药物'
            }
        },
        {
            'username': 'patient_li',
            'password': 'Patient123',
            'email': 'li_patient@example.com',
            'full_name': '李患者',
            'phone': '13900139004',
            'patient_info': {
                'gender': '女',
                'address': '深圳市南山区',
                'emergency_contact': '李先生',
                'emergency_phone': '13900139104',
                'medical_history': '哮喘',
                'allergies': '花粉、尘螨'
            }
        },
        {
            'username': 'patient_zhou',
            'password': 'Patient123',
            'email': 'zhou@example.com',
            'full_name': '周患者',
            'phone': '13900139005',
            'patient_info': {
                'gender': '男',
                'address': '成都市武侯区',
                'emergency_contact': '周太太',
                'emergency_phone': '13900139105',
                'medical_history': '冠心病',
                'allergies': '无'
            }
        },
        {
            'username': 'patient_wu',
            'password': 'Patient123',
            'email': 'wu@example.com',
            'full_name': '吴患者',
            'phone': '13900139006',
            'patient_info': {
                'gender': '女',
                'address': '杭州市西湖区',
                'emergency_contact': '吴先生',
                'emergency_phone': '13900139106',
                'medical_history': '无',
                'allergies': '海鲜'
            }
        },
        {
            'username': 'patient_zheng',
            'password': 'Patient123',
            'email': 'zheng@example.com',
            'full_name': '郑患者',
            'phone': '13900139007',
            'patient_info': {
                'gender': '男',
                'address': '南京市鼓楼区',
                'emergency_contact': '郑太太',
                'emergency_phone': '13900139107',
                'medical_history': '胃溃疡',
                'allergies': '无'
            }
        },
        {
            'username': 'patient_wang',
            'password': 'Patient123',
            'email': 'wang_patient@example.com',
            'full_name': '王患者',
            'phone': '13900139008',
            'patient_info': {
                'gender': '女',
                'address': '武汉市江汉区',
                'emergency_contact': '王先生',
                'emergency_phone': '13900139108',
                'medical_history': '无',
                'allergies': '无'
            }
        }
    ]
    
    # 创建研究人员用户
    researchers_data = [
        {
            'username': 'researcher_yang',
            'password': 'Research123',
            'email': 'yang@example.com',
            'full_name': '杨研究员',
            'phone': '13700137001',
            'researcher_info': {
                'institution': '中国医学科学院',
                'department': '流行病学研究所',
                'research_area': '传染病流行病学',
                'education': '北京协和医学院博士',
                'publications': '《流行病学杂志》多篇论文',
                'projects': '国家自然科学基金项目',
                'bio': '从事流行病学研究多年，主要研究方向为传染病的流行规律及防控策略。'
            }
        },
        {
            'username': 'researcher_ma',
            'password': 'Research123',
            'email': 'ma@example.com',
            'full_name': '马研究员',
            'phone': '13700137002',
            'researcher_info': {
                'institution': '上海交通大学医学院',
                'department': '肿瘤研究中心',
                'research_area': '癌症基因治疗',
                'education': '复旦大学医学院博士',
                'publications': 'Nature、Science等国际期刊多篇论文',
                'projects': '国家重点研发计划',
                'bio': '专注于癌症基因治疗的研究，开发了多种新型基因治疗方法。'
            }
        },
        {
            'username': 'researcher_hu',
            'password': 'Research123',
            'email': 'hu@example.com',
            'full_name': '胡研究员',
            'phone': '13700137003',
            'researcher_info': {
                'institution': '中国疾病预防控制中心',
                'department': '慢性非传染性疾病预防控制所',
                'research_area': '心血管疾病预防',
                'education': '北京大学公共卫生学院博士',
                'publications': '《中华心血管病杂志》等多篇论文',
                'projects': '国家卫健委重点项目',
                'bio': '在心血管疾病预防与控制领域有多年研究经验，开发了多种社区干预模式。'
            }
        }
    ]
    
    # 创建医生用户
    for doctor_data in doctors_data:
        # 检查用户是否已存在
        existing_user = User.query.filter_by(username=doctor_data['username']).first()
        if existing_user:
            print(f"用户 {doctor_data['username']} 已存在，跳过创建...")
            continue
        
        doctor = User(
            username=doctor_data['username'],
            password=doctor_data['password'],
            email=doctor_data['email'],
            full_name=doctor_data['full_name'],
            phone=doctor_data['phone'],
            role=Role.DOCTOR,
            is_active=True
        )
        
        db.session.add(doctor)
        db.session.flush()  # 获取用户ID
        
        # 创建医生信息
        doctor_info = DoctorInfo(
            user_id=doctor.id,
            specialty=doctor_data['doctor_info']['specialty'],
            license_number=doctor_data['doctor_info']['license_number'],
            years_of_experience=doctor_data['doctor_info']['years_of_experience'],
            education=doctor_data['doctor_info']['education'],
            hospital=doctor_data['doctor_info']['hospital'],
            department=doctor_data['doctor_info']['department'],
            bio=doctor_data['doctor_info']['bio']
        )
        
        db.session.add(doctor_info)
    
    # 创建患者用户
    for patient_data in patients_data:
        # 检查用户是否已存在
        existing_user = User.query.filter_by(username=patient_data['username']).first()
        if existing_user:
            print(f"用户 {patient_data['username']} 已存在，跳过创建...")
            continue
        
        patient = User(
            username=patient_data['username'],
            password=patient_data['password'],
            email=patient_data['email'],
            full_name=patient_data['full_name'],
            phone=patient_data['phone'],
            role=Role.PATIENT,
            is_active=True
        )
        
        db.session.add(patient)
        db.session.flush()  # 获取用户ID
        
        # 创建患者信息
        patient_info = PatientInfo(
            user_id=patient.id,
            gender=patient_data['patient_info']['gender'],
            address=patient_data['patient_info']['address'],
            emergency_contact=patient_data['patient_info']['emergency_contact'],
            emergency_phone=patient_data['patient_info']['emergency_phone'],
            medical_history=patient_data['patient_info']['medical_history'],
            allergies=patient_data['patient_info']['allergies']
        )
        
        db.session.add(patient_info)
    
    # 创建研究人员用户
    for researcher_data in researchers_data:
        # 检查用户是否已存在
        existing_user = User.query.filter_by(username=researcher_data['username']).first()
        if existing_user:
            print(f"用户 {researcher_data['username']} 已存在，跳过创建...")
            continue
        
        researcher = User(
            username=researcher_data['username'],
            password=researcher_data['password'],
            email=researcher_data['email'],
            full_name=researcher_data['full_name'],
            phone=researcher_data['phone'],
            role=Role.RESEARCHER,
            is_active=True
        )
        
        db.session.add(researcher)
        db.session.flush()  # 获取用户ID
        
        # 创建研究人员信息
        researcher_info = ResearcherInfo(
            user_id=researcher.id,
            institution=researcher_data['researcher_info']['institution'],
            department=researcher_data['researcher_info']['department'],
            research_area=researcher_data['researcher_info']['research_area'],
            education=researcher_data['researcher_info']['education'],
            publications=researcher_data['researcher_info']['publications'],
            projects=researcher_data['researcher_info']['projects'],
            bio=researcher_data['researcher_info']['bio']
        )
        
        db.session.add(researcher_info)
    
    db.session.commit()
    print(f"成功创建 {len(doctors_data) + len(patients_data) + len(researchers_data)} 个用户记录")

def main():
    """主函数，执行所有初始化操作"""
    print("开始初始化模拟数据...")
    
    with app.app_context():
        # 按顺序创建各类数据
        try:
            create_institutions()
        except Exception as e:
            print(f"创建医疗机构数据时发生错误: {str(e)}")
        
        try:
            create_record_types()
        except Exception as e:
            print(f"创建记录类型数据时发生错误: {str(e)}")
        
        try:
            create_users()
        except Exception as e:
            print(f"创建用户数据时发生错误: {str(e)}")
    
    print("模拟数据初始化完成！")

if __name__ == "__main__":
    main() 