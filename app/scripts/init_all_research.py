#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
初始化所有研究相关数据脚本
使用方法: python -m app.scripts.init_all_research
"""

import sys
import os

# 添加项目根目录到sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(SCRIPT_DIR)))

from app.scripts.init_researcher_users import init_researcher_users
from app.scripts.init_research_data import init_research_data

def init_all_research():
    """初始化所有研究相关数据"""
    print("=== 开始初始化所有研究相关数据 ===")
    
    # 步骤1：初始化研究员用户
    print("\n第1步：初始化研究员用户...")
    init_researcher_users()
    
    # 步骤2：初始化研究项目和团队成员数据
    print("\n第2步：初始化研究项目和团队成员数据...")
    init_research_data()
    
    print("\n=== 所有研究相关数据初始化完成! ===")

if __name__ == "__main__":
    init_all_research() 