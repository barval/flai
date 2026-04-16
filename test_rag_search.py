#!/usr/bin/env python3
import sys
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
    
    print(f"TOP_K: {rag.top_k}")
    print(f"RERANK_TOP_K: {rag.rerank_top_k}")
    print(f"CHUNK_SIZE: {rag.chunk_size}")
    print(f"CHUNK_OVERLAP: {rag.chunk_overlap}")
    print(f"RERANKER_ENABLED: {rag.reranker_enabled}")
    
    user_id = "valery"
    session_id = create_session(user_id, lang='ru')
    
    queries = [
        "Выведи все места работы Валерия Барсукова в Сарове",
        "Выведи все места работы Валерия Барсукова в Москве", 
        "Выведи все места работы Валерия Барсукова в Нижнем Новгороде",
        "Выведи все места работы Валерия Барсукова в хронологическом порядке"
    ]
    
    for query in queries:
        print(f"\n{'='*70}")
        print(f"QUERY: {query}")
        print(f"{'='*70}")
        
        answer, error, model = rag.generate_answer(user_id, query, session_id, lang='ru', threshold=0.3)
        
        if error:
            print(f"ERROR: {error}")
        elif answer:
            print(f"ANSWER ({model}):")
            print(answer[:2500])
            if len(answer) > 2500:
                print("...[truncated]")
        else:
            print("No answer returned (no relevant documents)")
