# app/routes/debug.py
# Debug API endpoints - only enabled when DEBUG_API_ENABLED=true in .env
from flask import Blueprint, jsonify, request, current_app
from flask_wtf.csrf import CSRFProtect
import logging

bp = Blueprint('debug', __name__)
logger = logging.getLogger(__name__)
csrf = CSRFProtect()


def debug_api_required(f):
    """Decorator to require DEBUG_API_ENABLED=true and exempt from CSRF."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_app.config.get('DEBUG_API_ENABLED'):
            return jsonify({'error': 'Debug API disabled'}), 403
        return f(*args, **kwargs)
    decorated._csrf_exempt = True
    return decorated


def get_rag_module():
    """Get RAG module from current app."""
    return current_app.modules.get('rag')


@bp.route('/api/rag/search', methods=['POST'])
@debug_api_required
def rag_search():
    """Debug RAG search endpoint - returns raw chunks without LLM processing.
    Only works when DEBUG_API_ENABLED=true in .env.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400
    
    query = data.get('query', '')
    user_id = data.get('user_id', 'valery')
    top_k = data.get('top_k', 80)
    
    if not query:
        return jsonify({'error': 'query parameter required'}), 400
    
    rag = get_rag_module()
    if not rag or not rag.available:
        return jsonify({'error': 'RAG module not available'}), 503
    
    chunks, scores = rag.search(user_id, query, top_k=top_k)
    
    results = []
    for i, (chunk, score) in enumerate(zip(chunks, scores)):
        results.append({
            'index': i,
            'doc_id': chunk.get('doc_id', ''),
            'text': chunk.get('text', ''),
            'score': score,
            'metadata': {
                k: v for k, v in chunk.items() 
                if k not in ('text', 'doc_id')
            }
        })
    
    return jsonify({
        'query': query,
        'user_id': user_id,
        'chunks_found': len(chunks),
        'results': results
    })


@bp.route('/api/rag/stats', methods=['GET'])
@debug_api_required
def rag_stats():
    """Debug RAG stats endpoint."""
    rag = get_rag_module()
    
    if not rag or not rag.available:
        return jsonify({'error': 'RAG module not available'}), 503
    
    return jsonify({
        'available': rag.available,
        'top_k': rag.top_k,
        'chunk_size': rag.chunk_size,
        'chunk_overlap': rag.chunk_overlap,
        'chunk_strategy': rag.chunk_strategy,
        'collection_name_prefix': rag.collection_name_prefix,
    })


@bp.route('/api/debug/health', methods=['GET'])
@debug_api_required
def debug_health():
    """Debug health check - more detailed than /health."""
    from . import get_database, get_llamacpp, get_qdrant, get_redis
    
    results = {
        'web': 'ok',
        'database': 'unknown',
        'redis': 'unknown',
        'qdrant': 'unknown',
        'llamacpp': 'unknown',
    }
    
    try:
        db = get_database()
        if db:
            results['database'] = 'ok'
    except:
        results['database'] = 'error'
    
    try:
        r = get_redis()
        if r:
            results['redis'] = 'ok'
    except:
        results['redis'] = 'error'
    
    try:
        import requests
        resp = requests.get(f"{current_app.config.get('LLAMACPP_URL', 'http://flai-llamacpp:8033')}/v1/models", timeout=2)
        if resp.status_code == 200:
            results['llamacpp'] = 'ok'
    except:
        results['llamacpp'] = 'error'
    
    try:
        qdrant_url = current_app.config.get('QDRANT_URL', 'http://flai-qdrant:6333')
        import requests
        resp = requests.get(f"{qdrant_url}/collections", timeout=2)
        if resp.status_code == 200:
            results['qdrant'] = 'ok'
    except:
        results['qdrant'] = 'error'
    
    return jsonify(results)


@bp.route('/api/debug/documents', methods=['GET'])
@debug_api_required
def list_documents():
    """List all documents for a user."""
    user_id = request.args.get('user_id')
    if not user_id:
        user_id = 'valery'
    
    logger.info(f"list_documents: user_id={user_id}")
    
    try:
        from app.database import get_db_connection
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, filename, file_path, indexed_at, file_size FROM documents WHERE user_id=%s ORDER BY indexed_at", (user_id,))
        docs = c.fetchall()
        conn.close()
        
        result = {
            'user_id': user_id,
            'documents_count': len(docs),
            'documents': []
        }
        for doc in docs:
            d = dict(doc) if hasattr(doc, 'keys') else {'id': doc[0], 'filename': doc[1], 'file_path': doc[2], 'indexed_at': doc[3], 'file_size': doc[4]}
            result['documents'].append({
                'id': str(d.get('id', '')),
                'filename': d.get('filename', ''),
                'file_path': d.get('file_path', ''),
                'indexed_at': str(d.get('indexed_at')) if d.get('indexed_at') else None,
                'file_size': d.get('file_size', 0)
            })
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"list_documents error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/debug/qdrant/<user_id>', methods=['GET'])
@debug_api_required
def qdrant_collection_info(user_id: str):
    """Get Qdrant collection info for a user."""
    rag = get_rag_module()
    if not rag or not rag.available:
        return jsonify({'error': 'RAG not available'}), 503
    
    try:
        info = rag.qdrant_client.get_collection(f"user_{user_id}")
        return jsonify({
            'collection': f"user_{user_id}",
            'vectors_count': info.vectors_count,
            'points_count': info.points_count,
            'segments_count': info.segments_count,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/debug/qdrant/<user_id>/search', methods=['POST'])
@debug_api_required
def qdrant_direct_search(user_id: str):
    """Direct Qdrant search without RAG processing."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON required'}), 400
    
    query = data.get('query', '')
    if not query:
        return jsonify({'error': 'query required'}), 400
    
    limit = data.get('limit', 10)
    
    rag = get_rag_module()
    if not rag or not rag.available:
        return jsonify({'error': 'RAG not available'}), 503
    
    # Get embedding
    emb = rag._get_embedding(query)
    if not emb:
        return jsonify({'error': 'Failed to get embedding'}), 500
    
    collection_name = f"user_{user_id}"
    from qdrant_client import models
    
    try:
        results = rag.qdrant_client.search(
            collection_name=collection_name,
            query_vector=emb,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
            ),
            limit=limit
        )
        
        return jsonify({
            'query': query,
            'limit': limit,
            'found': len(results),
            'results': [
                {
                    'doc_id': r.payload.get('doc_id', ''),
                    'score': r.score,
                    'text': r.payload.get('text', '')[:100]
                }
                for r in results
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500