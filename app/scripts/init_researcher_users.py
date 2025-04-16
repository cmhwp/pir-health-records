#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
初始化研究员用户脚本
使用方法: python -m app.scripts.init_researcher_users
"""

import sys
import os
import datetime
from werkzeug.security import generate_password_hash

# 添加项目根目录到sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(SCRIPT_DIR)))

from app import create_app
from app.models import db, User, Role
from app.models.role_models import ResearcherInfo

def init_researcher_users():
    """初始化研究员用户数据"""
    app = create_app()
    
    with app.app_context():
        print("开始初始化研究员用户数据...")
        
        # 研究员用户示例数据
        researcher_users = [
            {
                "username": "researcher01",
                "password": "password123",
                "email": "researcher01@example.com",
                "full_name": "张研究",
                "phone": "13800001001",
                "info": {
                    "institution": "北京医学研究所",
                    "department": "心血管研究部",
                    "research_area": "高血压治疗研究",
                    "education": "北京医科大学医学博士",
                    "bio": "专注于高血压和心血管疾病的防治研究，在国内外核心期刊发表学术论文20余篇。"
                }
            },
            {
                "username": "researcher02",
                "password": "password123",
                "email": "researcher02@example.com",
                "full_name": "王研究",
                "phone": "13800001002",
                "info": {
                    "institution": "上海医科大学",
                    "department": "内分泌科",
                    "research_area": "糖尿病研究",
                    "education": "上海医科大学医学博士",
                    "bio": "专注于糖尿病的发病机制与新药研发，参与多项国家级研究项目。"
                }
            },
            {
                "username": "researcher03",
                "password": "password123",
                "email": "researcher03@example.com",
                "full_name": "李研究",
                "phone": "13800001003",
                "info": {
                    "institution": "广州医学院",
                    "department": "肿瘤研究中心",
                    "research_area": "肺癌早期筛查",
                    "education": "美国约翰霍普金斯大学医学博士",
                    "bio": "专注于肿瘤早期筛查和精准治疗研究，曾在《自然》和《科学》等顶级期刊发表论文。"
                }
            },
            {
                "username": "researcher04",
                "password": "password123",
                "email": "researcher04@example.com",
                "full_name": "赵研究",
                "phone": "13800001004",
                "info": {
                    "institution": "中国心理健康研究中心",
                    "department": "临床心理学部",
                    "research_area": "抑郁症治疗",
                    "education": "北京大学心理学博士",
                    "bio": "专注于抑郁症和焦虑症的心理治疗研究，开发了多种适合中国患者的治疗方案。"
                }
            },
            {
                "username": "researcher05",
                "password": "password123",
                "email": "researcher05@example.com",
                "full_name": "钱研究",
                "phone": "13800001005",
                "info": {
                    "institution": "武汉病毒研究所",
                    "department": "传染病研究部",
                    "research_area": "新冠后遗症研究",
                    "education": "清华大学医学博士",
                    "bio": "专注于新冠病毒及其长期健康影响研究，参与了多项国家应急攻关项目。"
                }
            },
            {
                "username": "researcher06",
                "password": "password123",
                "email": "researcher06@example.com",
                "full_name": "孙研究",
                "phone": "13800001006",
                "info": {
                    "institution": "中国医学科学院",
                    "department": "基因组医学研究所",
                    "research_area": "精准医疗",
                    "education": "复旦大学生物医学博士",
                    "bio": "专注于基因组学与个体化医疗研究，开发了多种基于基因组数据的疾病预测模型。"
                }
            }
        ]
        
        # 创建研究员用户
        for researcher_data in researcher_users:
            # 检查用户是否已存在
            existing_user = User.query.filter_by(username=researcher_data["username"]).first()
            if existing_user:
                print(f"用户已存在: {researcher_data['username']} ({researcher_data['full_name']})")
                continue
                
            # 创建新用户
            user = User(
                username=researcher_data["username"],
                password_hash=generate_password_hash(researcher_data["password"]),
                email=researcher_data["email"],
                full_name=researcher_data["full_name"],
                phone=researcher_data["phone"],
                role=Role.RESEARCHER,
                is_active=True,
                created_at=datetime.datetime.now()
            )
            
            db.session.add(user)
            db.session.flush()  # 获取用户ID
            
            # 创建研究员信息
            info_data = researcher_data["info"]
            researcher_info = ResearcherInfo(
                user_id=user.id,
                institution=info_data["institution"],
                department=info_data["department"],
                research_area=info_data["research_area"],
                education=info_data["education"],
                bio=info_data["bio"]
            )
            
            db.session.add(researcher_info)
            print(f"创建研究员用户: {user.username} ({user.full_name})")
        
        # 提交数据
        db.session.commit()
        
        print("研究员用户数据初始化完成!")

if __name__ == "__main__":
    init_researcher_users() 