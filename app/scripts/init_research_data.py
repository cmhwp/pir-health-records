#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
初始化研究项目和团队成员数据脚本
使用方法: python -m app.scripts.init_research_data
"""

import sys
import os
import datetime
from datetime import timedelta
import random

# 添加项目根目录到sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(SCRIPT_DIR)))

from app import create_app
from app.models import db, User, Role
from app.models.researcher import ResearchProject, ProjectTeamMember, ProjectStatus

def get_random_date(start_date, end_date):
    """生成两个日期之间的随机日期"""
    time_between_dates = end_date - start_date
    days_between_dates = time_between_dates.days
    random_number_of_days = random.randrange(days_between_dates)
    return start_date + timedelta(days=random_number_of_days)

def init_research_data():
    """初始化研究项目和团队成员数据"""
    app = create_app()
    
    with app.app_context():
        print("开始初始化研究项目和团队成员数据...")
        
        # 获取所有研究员用户
        researchers = User.query.filter_by(role=Role.RESEARCHER).all()
        
        if not researchers:
            print("错误: 系统中没有研究员用户，请先创建研究员用户")
            return
        
        print(f"找到 {len(researchers)} 名研究员用户")
        
        # 删除现有的研究项目和团队成员数据(谨慎使用)
        print("删除现有的研究项目和团队成员数据...")
        db.session.query(ProjectTeamMember).delete()
        db.session.query(ResearchProject).delete()
        db.session.commit()
        
        # 研究项目示例数据
        research_projects = [
            {
                "title": "高血压新疗法研究",
                "description": "本研究旨在评估一种创新的高血压治疗方法，通过结合药物治疗和生活方式干预，降低患者血压并减少心血管疾病风险。研究将招募100名高血压患者，随机分配到实验组和对照组，观察6个月的治疗效果。",
                "status": ProjectStatus.IN_PROGRESS,
                "start_date": datetime.date(2023, 1, 15),
                "end_date": datetime.date(2023, 12, 31),
                "participants": 120
            },
            {
                "title": "糖尿病药物副作用分析",
                "description": "本研究旨在系统分析常用糖尿病药物的副作用及其发生概率，为临床用药提供更全面的参考依据。研究将收集并分析200名糖尿病患者的用药记录和不良反应报告，识别药物副作用模式和风险因素。",
                "status": ProjectStatus.PLANNING,
                "start_date": datetime.date(2023, 8, 1),
                "end_date": datetime.date(2024, 7, 31),
                "participants": 200
            },
            {
                "title": "肺癌早期筛查方法评估",
                "description": "本研究旨在评估一种新型肺癌早期筛查方法的有效性和经济性，通过对比新方法与传统筛查方法在检出率、准确性和成本方面的差异，为肺癌早期筛查策略提供科学依据。",
                "status": ProjectStatus.COMPLETED,
                "start_date": datetime.date(2022, 3, 10),
                "end_date": datetime.date(2023, 3, 9),
                "participants": 500
            },
            {
                "title": "抑郁症心理治疗方案比较研究",
                "description": "本研究旨在比较认知行为疗法(CBT)和正念疗法(MBCT)对抑郁症患者的治疗效果，通过随机对照试验评估两种治疗方案在症状缓解、复发率和生活质量改善方面的差异。",
                "status": ProjectStatus.PAUSED,
                "start_date": datetime.date(2023, 5, 1),
                "end_date": datetime.date(2024, 4, 30),
                "participants": 150
            },
            {
                "title": "新冠康复患者长期健康状况追踪",
                "description": "本研究旨在追踪新冠肺炎康复患者的长期健康状况，包括肺功能、免疫功能和神经系统功能等多方面评估，为新冠后遗症的预防和治疗提供临床依据。",
                "status": ProjectStatus.IN_PROGRESS,
                "start_date": datetime.date(2022, 9, 1),
                "end_date": datetime.date(2025, 8, 31),
                "participants": 300
            }
        ]
        
        # 创建项目和分配研究员
        created_projects = []
        for idx, project_data in enumerate(research_projects):
            # 为每个项目随机选择一个研究员作为创建者
            researcher = random.choice(researchers)
            
            project = ResearchProject(
                title=project_data["title"],
                description=project_data["description"],
                status=project_data["status"],
                start_date=project_data["start_date"],
                end_date=project_data["end_date"],
                participants=project_data["participants"],
                researcher_id=researcher.id
            )
            
            db.session.add(project)
            db.session.flush()  # 获取项目ID
            
            print(f"创建项目: {project.title} (创建者: {researcher.full_name})")
            created_projects.append(project)
        
        # 提交项目数据
        db.session.commit()
        
        # 团队成员角色列表
        team_roles = [
            "主要研究员", "数据分析师", "临床协调员", "医学统计师", 
            "研究助理", "质量控制专员", "伦理顾问", "临床医生"
        ]
        
        # 为每个项目添加2-4名团队成员
        for project in created_projects:
            # 获取除项目创建者外的其他研究员
            available_researchers = [r for r in researchers if r.id != project.researcher_id]
            
            # 如果没有足够的研究员，则跳过
            if not available_researchers:
                continue
                
            # 随机选择2-4名不重复的研究员作为团队成员
            num_members = min(random.randint(2, 4), len(available_researchers))
            team_researchers = random.sample(available_researchers, num_members)
            
            for team_researcher in team_researchers:
                # 随机选择一个角色
                role = random.choice(team_roles)
                
                member = ProjectTeamMember(
                    name=team_researcher.full_name,
                    role=role,
                    project_id=project.id
                )
                
                db.session.add(member)
                print(f"  添加团队成员: {member.name} (角色: {member.role})")
        
        # 提交团队成员数据
        db.session.commit()
        
        print("研究项目和团队成员数据初始化完成!")

if __name__ == "__main__":
    init_research_data() 