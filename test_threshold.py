#!/usr/bin/env python3
import sys
import time
sys.path.insert(0, '/app')

import os
os.environ['SECRET_KEY'] = 'test'

from app import create_app
from modules.rag import RagModule
from app.db import create_session

app = create_app()
with app.app_context():
    rag = RagModule()
    rag.init_app(app)
    
    user_id = "valery"
    session_id = create_session(user_id, lang='ru')
    
    queries = [
        ("в Сарове", "Выведи все места работы Валерия Барсукова в Сарове"),
        ("в Москве", "Выведи все места работы Валерия Барсукова в Москве"),
        ("в Нижнем Новгороде", "Выведи все места работы Валерия Барсукова в Нижнем Новгороде"),
    ]
    
    # Count companies in answer
    def count_companies(answer):
        if not answer:
            return 0
        # Look for company patterns - count lines with year ranges (job entries)
        lines = answer.split('\n')
        count = 0
        for line in lines:
            # Count lines that look like job entries (contain year patterns like 1997, 2000, 2010 etc)
            if any(x in line for x in ['199', '200', '201', '202']):
                if '–' in line or '-' in line:
                    count += 1
        return count
    
    print("="*70)
    print("THRESHOLD COMPARISON")
    print("="*70)
    
    results_03 = []
    results_015 = []
    
    for short_name, query in queries:
        # threshold=0.3
        answer_03, _, _ = rag.generate_answer(user_id, query, session_id, lang='ru', threshold=0.3)
        cnt_03 = count_companies(answer_03)
        results_03.append((short_name, cnt_03))
        
        # threshold=0.15
        answer_015, _, _ = rag.generate_answer(user_id, query, session_id, lang='ru', threshold=0.15)
        cnt_015 = count_companies(answer_015)
        results_015.append((short_name, cnt_015))
    
    print(f"\n{'Запрос':<25} {'threshold=0.3':<15} {'threshold=0.15':<15} {'Эталон':<10}")
    print("-"*65)
    for i, (name, _) in enumerate(results_03):
        print(f"{name:<25} {results_03[i][1]:<15} {results_015[i][1]:<15}", end="")
        if name == "в Сарове":
            print(" 6")
        elif name == "в Москве":
            print(" 5")
        elif name == "в Нижнем Новгороде":
            print(" 5")
    
    total_03 = sum(x[1] for x in results_03)
    total_015 = sum(x[1] for x in results_015)
    print("-"*65)
    print(f"{'ИТОГО':<25} {total_03:<15} {total_015:<15} 16")
