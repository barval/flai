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
        "Выведи все места работы Валерия Барсукова в Сарове",
        "Выведи все места работы Валерия Барсукова в Москве", 
        "Выведи все места работы Валерия Барсукова в Нижнем Новгороде",
        "Выведи все места работы Валерия Барсукова в хронологическом порядке"
    ]
    
    print(f"\n{'='*80}")
    print(f"RERANKER_ENABLED: {rag.reranker_enabled}")
    print(f"TOP_K: {rag.top_k}, RERANK_TOP_K: {rag.rerank_top_k}")
    print(f"{'='*80}\n")
    
    results = []
    
    for query in queries:
        start_time = time.time()
        
        answer, error, model = rag.generate_answer(user_id, query, session_id, lang='ru', threshold=0.3)
        elapsed = time.time() - start_time
        
        if answer:
            # Count lines/entries in answer
            lines = len([l for l in answer.split('\n') if l.strip() and ('-' in l or '|' in l)])
            results.append({
                'query': query.replace('Выведи все места работы Валерия Барсукова ', ''),
                'answer': answer[:300] + '...' if len(answer) > 300 else answer,
                'time': elapsed,
                'len': len(answer)
            })
            print(f"Query: {query.replace('Выведи все места работы Валерия Барсукова ', '')}")
            print(f"Time: {elapsed:.1f}s | Answer length: {len(answer)} chars")
            print(f"Answer preview:\n{answer[:500]}...\n")
        else:
            print(f"Query: {query} - NO ANSWER (error: {error})")
    
    total_time = sum(r['time'] for r in results)
    print(f"\n{'='*80}")
    print(f"TOTAL TIME: {total_time:.1f}s")
    print(f"{'='*80}")
