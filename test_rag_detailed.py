#!/usr/bin/env python3
import sys
import time
import re
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
        ("хронология", "Выведи все места работы Валерия Барсукова в хронологическом порядке"),
    ]
    
    results = []
    
    for short_name, query in queries:
        start_time = time.time()
        
        answer, error, model = rag.generate_answer(user_id, query, session_id, lang='ru', threshold=0.3)
        elapsed = time.time() - start_time
        
        # Count entries in answer (lines with company names)
        companies = []
        if answer:
            # Find company patterns
            lines = answer.split('\n')
            for line in lines:
                # Look for company names (capitalized words, ORG types)
                if any(x in line.upper() for x in ['ООО', 'ЗАО', 'ОАО', 'СБЕР', 'АЛИДИ', 'ГЛОБУС', 'СИНТЕК']):
                    if line.strip() and len(line.strip()) > 5:
                        companies.append(line.strip()[:80])
        
        results.append({
            'query': short_name,
            'answer': answer if answer else error,
            'time': elapsed,
            'companies': len(companies),
            'company_list': companies
        })
        
        print(f"\n=== {short_name} ===")
        print(f"Time: {elapsed:.1f}s | Found: {len(companies)} companies")
        if answer:
            print(answer[:800])
    
    # Print summary table
    print("\n" + "="*80)
    print("SUMMARY TABLE")
    print("="*80)
    print(f"{'Query':<25} {'Time':<10} {'Companies Found':<20}")
    print("-"*60)
    for r in results:
        print(f"{r['query']:<25} {r['time']:.1f}s     {r['companies']}")
