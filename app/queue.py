# app/queue.py
import redis
import pickle
import uuid
import time
import threading
import sqlite3
import os
from typing import Dict, Any, Optional, Tuple, List
from .utils import get_current_time_in_timezone, get_current_time_in_timezone_for_db, save_uploaded_file
from .db import save_message, CHAT_DB_PATH, update_document_index_status, \
    INDEX_STATUS_PENDING, INDEX_STATUS_INDEXING, INDEX_STATUS_INDEXED, INDEX_STATUS_FAILED, \
    get_current_time_for_db
from .model_config import get_model_config


class RedisRequestQueue:
    def __init__(self, app):
        self.app = app
        self.redis = redis.from_url(app.config['REDIS_URL'], decode_responses=False)
        self.queue_key = 'request_queue'
        self.processing_key = 'processing_requests'
        self.results_key = 'request_results'
        self.user_requests_key = 'user_requests'
        self.start_worker()

    def start_worker(self):
        thread = threading.Thread(target=self._worker_loop, daemon=True)
        thread.start()
        self.app.logger.info("RedisRequestQueue: worker started")

    def _worker_loop(self):
        self.app.logger.info("RedisRequestQueue: processing loop started")
        while True:
            try:
                result = self.redis.blpop(self.queue_key, timeout=5)
                if not result:
                    continue
                queue_key, task_data = result
                task = pickle.loads(task_data)
                queue_time = time.time() - task.get('timestamp', time.time())
                if queue_time > 300:
                    self.app.logger.warning(f"Task {task['id']} waited too long in queue ({queue_time:.1f}s). Cancelling.")
                    template = self.app.modules['base']._(
                        'Request cancelled - too long in queue ({queue_time:.1f}s)',
                        lang=task.get('lang', 'ru')
                    )
                    error_text = template.format(queue_time=queue_time)
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'error',
                        'error': error_text,
                        'result': {'session_id': task.get('session_id')},
                        'timestamp': time.time()
                    }))
                    continue
                self.app.logger.info(f"RedisRequestQueue: got task {task['id']} from queue for session {task.get('session_id')}, queue time: {queue_time:.1f}s")
                self.redis.hset(self.processing_key, task['id'], task_data)
                try:
                    with self.app.app_context():
                        result_data = self._process_request(task)
                        if 'session_id' not in result_data and task.get('session_id'):
                            result_data['session_id'] = task.get('session_id')
                        self.redis.hset(self.results_key, task['id'], pickle.dumps({
                            'status': 'completed',
                            'result': result_data,
                            'timestamp': time.time()
                        }))
                        self.app.logger.info(f"RedisRequestQueue: task {task['id']} completed successfully for session {task.get('session_id')}")
                except Exception as e:
                    self.app.logger.error(f"RedisRequestQueue: error processing task {task['id']}: {str(e)}")
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'error',
                        'error': str(e),
                        'result': {'session_id': task.get('session_id')},
                        'timestamp': time.time()
                    }))
                finally:
                    self.redis.hdel(self.processing_key, task['id'])
            except Exception as e:
                self.app.logger.error(f"RedisRequestQueue: error in worker loop: {str(e)}")
                time.sleep(1)

    def add_request(self, user_id: str, session_id: str, request_data: Dict[str, Any],
                    user_class: int, lang: str = 'ru') -> Tuple[str, Dict[str, Any]]:
        request_id = str(uuid.uuid4())
        timestamp = time.time()
        task = {
            'id': request_id,
            'user_id': user_id,
            'session_id': session_id,
            'data': request_data,
            'timestamp': timestamp,
            'user_class': user_class,
            'session_title': self._get_session_title(session_id, lang),
            'lang': lang
        }
        self.app.logger.info(f"RedisRequestQueue.add_request: adding task {request_id} for session {session_id} at {timestamp}")
        self.redis.rpush(self.queue_key, pickle.dumps(task))
        self.redis.sadd(f"{self.user_requests_key}:{user_id}", request_id)
        queue_length = self.redis.llen(self.queue_key)
        estimated_wait = max(1, queue_length * 5)
        position_info = {'position': queue_length, 'estimated_seconds': estimated_wait}
        self.app.logger.info(f"RedisRequestQueue.add_request: task added, position={queue_length}")
        return request_id, position_info

    def add_index_task(self, user_id: str, doc_id: str, file_path: str, lang: str = 'ru') -> str:
        request_id = str(uuid.uuid4())
        timestamp = time.time()
        task = {
            'id': request_id,
            'user_id': user_id,
            'type': 'index_document',
            'doc_id': doc_id,
            'file_path': file_path,
            'timestamp': timestamp,
            'lang': lang
        }
        self.app.logger.info(f"RedisRequestQueue.add_index_task: adding task {request_id} for document {doc_id}")
        self.redis.rpush(self.queue_key, pickle.dumps(task))
        return request_id

    def add_reindex_all_task(self, lang: str = 'ru') -> str:
        request_id = str(uuid.uuid4())
        timestamp = time.time()
        task = {
            'id': request_id,
            'type': 'reindex_all_embeddings',
            'timestamp': timestamp,
            'lang': lang
        }
        self.app.logger.info(f"RedisRequestQueue.add_reindex_all_task: adding task {request_id}")
        self.redis.rpush(self.queue_key, pickle.dumps(task))
        return request_id

    def get_user_queue_counts(self, user_id: str) -> Tuple[int, int]:
        total = self.redis.llen(self.queue_key)
        if total == 0:
            return 0, 0
        items = self.redis.lrange(self.queue_key, 0, -1)
        user_count = 0
        for item in items:
            try:
                task = pickle.loads(item)
                if task.get('user_id') == user_id:
                    user_count += 1
            except:
                continue
        return user_count, total

    def _get_session_title(self, session_id: str, lang: str = 'ru') -> str:
        try:
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                c = conn.cursor()
                c.execute('SELECT title FROM chat_sessions WHERE id = ?', (session_id,))
                row = c.fetchone()
                return row[0] if row else self.app.modules['base']._('Unknown session', lang=lang)
        except Exception as e:
            self.app.logger.error(f"Error getting session title: {str(e)}")
            return self.app.modules['base']._('Unknown session', lang=lang)

    def _get_model_name(self, module_type: str) -> Optional[str]:
        config = get_model_config(module_type)
        return config.get('model_name') if config else None

    def _try_rag_answer(self, query: str, session_id: str, user_id: str, lang: str) -> Tuple[Optional[str], Optional[str]]:
        rag = self.app.modules.get('rag')
        if rag and rag.available:
            answer, error, model_name = rag.generate_answer(user_id, query, session_id, lang=lang)
            if answer is not None and error is None:
                return answer, model_name
        return None, None

    def _process_request(self, task: Dict[str, Any]) -> Dict[str, Any]:
        self.app.logger.info(f"RedisRequestQueue._process_request: processing task {task['id']}")
        if task.get('type') == 'index_document':
            return self._process_index_task(task)
        if task.get('type') == 'reindex_all_embeddings':
            return self._process_reindex_all_task(task)
        if task.get('type') == 'transcribe_audio':
            return self._process_transcribe_task(task)

        user_id = task['user_id']
        session_id = task['session_id']
        request_data = task['data']
        lang = task.get('lang', 'ru')
        processing_start_time = time.time()
        current_time_str = get_current_time_in_timezone(self.app)
        request_type = request_data.get('type', 'text')
        message_text = request_data.get('text', '')
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')

        # Handle audio files (voice messages and audio uploads)
        if file_type and file_type.startswith('audio/'):
            return self._process_audio_task(task, request_data, session_id, user_id, lang)

        if request_type == 'text':
            router_start_time = time.time()
            router_result = self.app.modules['base'].process_message(message_text, current_time_str, lang=lang, session_id=session_id)
            router_time = round(time.time() - router_start_time, 1)
            if 'error' in router_result:
                completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                return {
                    'error': router_result['error'],
                    'session_id': session_id,
                    'assistant_timestamp': completion_time_for_db,
                    'is_error': True,
                    'response_time': router_time
                }
            action_type = router_result['action']
            query = router_result['query']
            final_response = ""
            model_used = self._get_model_name('chat') or 'unknown'
            is_error = False
            process_time = 0
            message_id = None

            if action_type == 'reasoning':
                rag_start_time = time.time()
                rag_answer, rag_model_name = self._try_rag_answer(query, session_id, user_id, lang)
                rag_time = round(time.time() - rag_start_time, 1)
                if rag_answer is not None:
                    completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                    model_used = rag_model_name + " (RAG)" if rag_model_name else 'unknown (RAG)'
                    message_id = save_message(session_id, 'assistant', rag_answer,
                                              model_name=model_used, response_time=str(rag_time))
                    return {
                        'response': rag_answer,
                        'session_id': session_id,
                        'model_used': model_used,
                        'assistant_timestamp': completion_time_for_db,
                        'response_time': rag_time,
                        'is_error': False,
                        'message_id': message_id
                    }
                self.app.logger.info(f"RAG returned no answer, falling back to reasoning model for query: {query[:50]}...")

            if action_type == 'image':
                if 'image' in self.app.modules and self.app.modules['image'].available:
                    mm_start_time = time.time()
                    prompt_data, error = self.app.modules['multimodal'].generate_image_params(query, lang=lang)
                    mm_time = round(time.time() - mm_start_time, 1)
                    if error:
                        final_response = f"⚠️ {error}"
                        model_used = 'system'
                        is_error = True
                        process_time = mm_time
                    else:
                        gen_start_time = time.time()
                        image_result = self.app.modules['image']._call_automatic1111(prompt_data, lang=lang)
                        gen_time = round(time.time() - gen_start_time, 1)
                        if image_result['success']:
                            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                            image_result['mm_time'] = mm_time
                            image_result['gen_time'] = gen_time
                            image_result['mm_model'] = self._get_model_name('multimodal') or 'unknown'
                            image_result['gen_model'] = self.app.config['AUTOMATIC1111_MODEL']
                            template = self.app.modules['base']._('Image generated from request: {query}', lang=lang)
                            message_text = template.format(query=query)
                            file_path = None
                            if image_result.get('image_data'):
                                file_path = save_uploaded_file(
                                    file_data=image_result['image_data'],
                                    filename=image_result['file_name'],
                                    session_id=session_id,
                                    upload_folder=self.app.config['UPLOAD_FOLDER']
                                )
                            msg_id = save_message(
                                session_id, 'assistant', message_text,
                                file_data=None,
                                file_type=image_result['file_type'],
                                file_name=image_result['file_name'],
                                file_path=file_path,
                                model_name=self.app.config['AUTOMATIC1111_MODEL'],
                                response_time={'mm_time': mm_time, 'gen_time': gen_time},
                                mm_time=str(mm_time), gen_time=str(gen_time),
                                mm_model=image_result['mm_model'],
                                gen_model=image_result['gen_model']
                            )
                            return {
                                'response': message_text,
                                'session_id': session_id,
                                'model_used': self.app.config['AUTOMATIC1111_MODEL'],
                                'assistant_timestamp': completion_time_for_db,
                                'file_path': file_path,
                                'file_name': image_result['file_name'],
                                'file_size': image_result['file_size'],
                                'file_type': image_result['file_type'],
                                'mm_time': mm_time,
                                'gen_time': gen_time,
                                'mm_model': image_result['mm_model'],
                                'gen_model': image_result['gen_model'],
                                'response_time': {'mm_time': mm_time, 'gen_time': gen_time, 'mm_model': image_result['mm_model'], 'gen_model': image_result['gen_model']},
                                'is_error': False,
                                'message_id': msg_id
                            }
                        else:
                            final_response = f"⚠️ {image_result['error']}"
                            model_used = 'system'
                            is_error = True
                            process_time = mm_time + gen_time
                else:
                    final_response = "⚠️ " + self.app.modules['base']._('Image generation module unavailable', lang=lang)
                    model_used = 'system'
                    is_error = True
                    process_time = 0
            elif action_type == 'camera':
                if 'cam' in self.app.modules and self.app.modules['cam'].available:
                    camera_start_time = time.time()
                    camera_result = self.app.modules['cam'].get_snapshot(user_id, query, lang=lang)
                    camera_time = round(time.time() - camera_start_time, 1)
                    if camera_result['success']:
                        completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                        camera_model = 'camera'
                        template = self.app.modules['base']._('Camera snapshot: {room_name}', lang=lang)
                        translated_text = template.format(room_name=camera_result['room_name'])
                        file_path = None
                        if camera_result.get('image_data'):
                            file_path = save_uploaded_file(
                                file_data=camera_result['image_data'],
                                filename=camera_result['file_name'],
                                session_id=session_id,
                                upload_folder=self.app.config['UPLOAD_FOLDER']
                            )
                        msg_id = save_message(
                            session_id, 'assistant',
                            translated_text,
                            file_data=None,
                            file_type=camera_result['image_type'],
                            file_name=camera_result['file_name'],
                            file_path=file_path,
                            model_name=camera_model,
                            response_time=str(camera_time)
                        )
                        first_message = {
                            'response': translated_text,
                            'session_id': session_id,
                            'model_used': camera_model,
                            'assistant_timestamp': completion_time_for_db,
                            'file_path': file_path,
                            'file_name': camera_result['file_name'],
                            'file_size': camera_result['file_size'],
                            'file_type': camera_result['image_type'],
                            'response_time': camera_time,
                            'is_error': False,
                            'message_id': msg_id
                        }
                        messages = [first_message]
                        if message_text and 'multimodal' in self.app.modules and self.app.modules['multimodal'].available:
                            mm_start_time = time.time()
                            bot_reply, error = self.app.modules['multimodal'].process_image_with_text(
                                camera_result['image_data'], message_text, current_time_str, lang=lang, session_id=session_id
                            )
                            mm_time = round(time.time() - mm_start_time, 1)
                            if error:
                                bot_reply = f"⚠️ {error}"
                                is_error = True
                            else:
                                is_error = False
                            mm_model_name = self._get_model_name('multimodal') or 'unknown'
                            msg_id2 = save_message(
                                session_id, 'assistant', bot_reply,
                                model_name=mm_model_name,
                                response_time=str(mm_time)
                            )
                            second_message = {
                                'response': bot_reply,
                                'session_id': session_id,
                                'model_used': mm_model_name,
                                'assistant_timestamp': get_current_time_in_timezone_for_db(self.app),
                                'response_time': mm_time,
                                'is_error': is_error,
                                'message_id': msg_id2
                            }
                            messages.append(second_message)
                        return {'messages': messages, 'session_id': session_id}
                    else:
                        final_response = f"⚠️ {camera_result['error']}"
                        model_used = 'system'
                        is_error = True
                        process_time = camera_time
                else:
                    final_response = "⚠️ " + self.app.modules['base']._('Camera module unavailable', lang=lang)
                    model_used = 'system'
                    is_error = True
                    process_time = 0
            elif action_type == 'reasoning':
                if router_result.get('needs_reasoning'):
                    reasoning_start_time = time.time()
                    final_response = self.app.modules['base'].process_reasoning(query, current_time_str, lang=lang, session_id=session_id)
                    process_time = round(time.time() - reasoning_start_time, 1)
                    model_used = self._get_model_name('reasoning') or 'unknown'
                else:
                    process_time = 0
                    final_response = query
                is_error = False
            elif action_type == 'rag':
                rag_module = self.app.modules.get('rag')
                if rag_module and rag_module.available:
                    rag_start_time = time.time()
                    answer, error, model_name = rag_module.generate_answer(user_id, query, session_id, lang=lang)
                    process_time = round(time.time() - rag_start_time, 1)
                    if error:
                        final_response = f"⚠️ {error}"
                        model_used = 'system'
                        is_error = True
                    else:
                        final_response = answer
                        model_used = (model_name + " (RAG)") if model_name else 'unknown (RAG)'
                        is_error = False
                else:
                    final_response = "⚠️ " + self.app.modules['base']._('RAG module unavailable', lang=lang)
                    model_used = 'system'
                    is_error = True
                    process_time = 0
            else:
                process_time = router_time
                final_response = query
                is_error = False

            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            if final_response:
                message_id = save_message(session_id, 'assistant', final_response, model_name=model_used, response_time=str(process_time))
                return {
                    'response': final_response,
                    'session_id': session_id,
                    'model_used': model_used,
                    'assistant_timestamp': completion_time_for_db,
                    'response_time': process_time,
                    'is_error': is_error,
                    'message_id': message_id
                }

        elif request_type == 'image' and file_data:
            process_start_time = time.time()
            is_error = False
            if 'multimodal' in self.app.modules and self.app.modules['multimodal'].available:
                file_size = int((len(file_data) * 3) / 4) if file_data else 0
                is_valid, error = self.app.modules['multimodal'].validate_image(file_data, file_type, file_name, file_size)
                if is_valid:
                    bot_reply, error = self.app.modules['multimodal'].process_image_with_text(file_data, message_text, current_time_str, lang=lang, session_id=session_id)
                    process_time = round(time.time() - process_start_time, 1)
                    if error:
                        bot_reply = f"⚠️ {error}"
                        is_error = True
                    else:
                        is_error = False
                else:
                    bot_reply = "⚠️ " + (error or self.app.modules['base']._('Invalid image', lang))
                    process_time = round(time.time() - process_start_time, 1)
                    is_error = True
            else:
                bot_reply = "⚠️ " + self.app.modules['base']._('Multimodal model unavailable', lang)
                process_time = round(time.time() - process_start_time, 1)
                is_error = True

            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            mm_model_name = self._get_model_name('multimodal') or 'unknown'
            message_id = save_message(session_id, 'assistant', bot_reply, model_name=mm_model_name, response_time=str(process_time))
            return {
                'response': bot_reply,
                'session_id': session_id,
                'model_used': mm_model_name,
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,
                'is_error': is_error,
                'message_id': message_id
            }
        else:
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            return {
                'error': self.app.modules['base']._('Unknown request type', lang=lang),
                'session_id': session_id,
                'assistant_timestamp': completion_time_for_db,
                'is_error': True,
                'response_time': 0
            }

    def _process_audio_task(self, task: Dict[str, Any], request_data: Dict[str, Any], 
                           session_id: str, user_id: str, lang: str) -> Dict[str, Any]:
        """Process audio file (voice message or audio upload) via transcription."""
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')
        voice_record = request_data.get('voice_record', False)
        user_class = task.get('user_class', 2)
        
        process_start_time = time.time()
        
        # Check if audio module is available
        audio_module = self.app.modules.get('audio')
        if not audio_module or not audio_module.available:
            process_time = round(time.time() - process_start_time, 1)
            error_msg = "⚠️ " + self.app.modules['base']._('Audio service unavailable', lang)
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            message_id = save_message(session_id, 'assistant', error_msg, model_name='system', response_time=str(process_time))
            return {
                'response': error_msg,
                'session_id': session_id,
                'model_used': 'system',
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,
                'is_error': True,
                'message_id': message_id
            }
        
        # Perform transcription
        transcribed_text = audio_module.transcribe(file_data, file_type, file_name, lang=lang)
        process_time = round(time.time() - process_start_time, 1)
        
        if transcribed_text is None:
            error_msg = "⚠️ " + self.app.modules['base']._('Failed to recognize speech', lang)
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            message_id = save_message(session_id, 'assistant', error_msg, model_name='system', response_time=str(process_time))
            return {
                'response': error_msg,
                'session_id': session_id,
                'model_used': 'system',
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,
                'is_error': True,
                'message_id': message_id
            }
        
        # Save transcribed message
        from flask_babel import force_locale
        with force_locale(lang):
            system_content = '🎤 ' + self.app.modules['base']._('Transcribed') + ': ' + transcribed_text
        
        transcribed_message_id = save_message(
            session_id, 'assistant', system_content,
            model_name='whisper', response_time=str(process_time)
        )
        
        # If voice record, queue text for processing
        if voice_record:
            text_request_data = {
                'type': 'text',
                'text': transcribed_text,
                'preview': (transcribed_text[:50] + '...') if transcribed_text else self.app.modules['base']._('Voice request', lang)
            }
            new_request_id, _ = self.add_request(
                user_id, session_id, text_request_data, user_class, lang=lang
            )
            
            return {
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'request_id': new_request_id,
                'session_id': session_id,
                'response_time': process_time
            }
        else:
            # Audio file upload without further processing
            return {
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'session_id': session_id,
                'response_time': process_time
            }

    def _process_transcribe_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Process audio transcription task asynchronously."""
        user_id = task['user_id']
        session_id = task['session_id']
        request_data = task['data']
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')
        voice_record = request_data.get('voice_record', False)
        lang = task.get('lang', 'ru')
        user_class = task.get('user_class', 2)

        # Perform transcription
        audio_module = self.app.modules.get('audio')
        if not audio_module or not audio_module.available:
            error_msg = self.app.modules['base']._('Audio service unavailable', lang)
            return {'error': error_msg, 'session_id': session_id, 'is_error': True}

        transcribed_text = audio_module.transcribe(file_data, file_type, file_name, lang=lang)
        if transcribed_text is None:
            error_msg = self.app.modules['base']._('Failed to recognize speech', lang)
            return {'error': error_msg, 'session_id': session_id, 'is_error': True}

        # Save assistant message with transcribed text
        from flask_babel import force_locale
        with force_locale(lang):
            system_content = '🎤 ' + self.app.modules['base']._('Transcribed') + ': ' + transcribed_text
        transcribed_message_id = save_message(session_id, 'assistant', system_content, model_name='whisper', response_time='0')

        # If this is a voice recording, we want to process the transcribed text as a text request
        if voice_record:
            # Create a new text request for processing
            text_request_data = {
                'type': 'text',
                'text': transcribed_text,
                'preview': (transcribed_text[:50] + '...') if transcribed_text else self.app.modules['base']._('Voice request', lang=lang)
            }
            # Add to queue (this will be processed by the same worker, but as a new task)
            new_request_id, _ = self.app.request_queue.add_request(
                user_id, session_id, text_request_data, user_class, lang=lang
            )
            # Return the result indicating transcription done and the new request ID for processing
            return {
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'request_id': new_request_id,
                'session_id': session_id,
                'response_time': 0  # will be updated later
            }
        else:
            # If not voice_record, just return the transcribed text (e.g., for audio file upload without immediate processing)
            return {
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'session_id': session_id,
                'response_time': 0
            }

    def _process_index_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = task['doc_id']
        file_path = task['file_path']
        user_id = task['user_id']
        lang = task.get('lang', 'ru')
        indexing_started_at = get_current_time_for_db()
        update_document_index_status(doc_id, INDEX_STATUS_INDEXING, indexing_started_at=indexing_started_at)
        rag = self.app.modules.get('rag')
        if not rag or not rag.available:
            error_msg = "RAG module unavailable"
            update_document_index_status(doc_id, INDEX_STATUS_FAILED)
            return {'success': False, 'error': error_msg, 'doc_id': doc_id}
        try:
            success, message = rag.index_document(user_id, doc_id, file_path)
            if success:
                indexed_at = get_current_time_for_db()
                embedding_model = self._get_model_name('embedding') or 'unknown'
                update_document_index_status(doc_id, INDEX_STATUS_INDEXED, indexed_at=indexed_at, embedding_model=embedding_model)
                self.app.logger.info(f"Set embedding_model for doc {doc_id} to {embedding_model}")
                return {'success': True, 'message': message, 'doc_id': doc_id}
            else:
                update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                return {'success': False, 'error': message, 'doc_id': doc_id}
        except Exception as e:
            self.app.logger.error(f"Indexing failed for doc {doc_id}: {e}")
            update_document_index_status(doc_id, INDEX_STATUS_FAILED)
            return {'success': False, 'error': str(e), 'doc_id': doc_id}

    def _process_reindex_all_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        self.app.logger.info("Starting reindex of all documents with new embedding model.")
        lang = task.get('lang', 'ru')
        from app.db import CHAT_DB_PATH
        import sqlite3
        conn = sqlite3.connect(CHAT_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT id, user_id, file_path FROM documents')
        documents = c.fetchall()
        conn.close()
        rag = self.app.modules.get('rag')
        if not rag or not rag.available:
            self.app.logger.error("RAG module not available for reindexing")
            return {'success': False, 'error': 'RAG module unavailable'}
        total = len(documents)
        success_count = 0
        fail_count = 0
        doc_ids = [doc['id'] for doc in documents]
        if doc_ids:
            placeholders = ','.join(['?'] * len(doc_ids))
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                c = conn.cursor()
                c.execute(f'''
                    UPDATE documents
                    SET index_status = ?, indexed_at = NULL, indexing_started_at = NULL, embedding_model = NULL
                    WHERE id IN ({placeholders})
                ''', [INDEX_STATUS_PENDING] + doc_ids)
                conn.commit()
            self.app.logger.info(f"Set {len(doc_ids)} documents to pending status")
        for doc in documents:
            doc_id = doc['id']
            user_id = doc['user_id']
            file_path = doc['file_path']
            documents_folder = self.app.config['DOCUMENTS_FOLDER']
            full_path = os.path.join(documents_folder, file_path)
            self.app.logger.info(f"Reindexing document {doc_id} for user {user_id}")
            try:
                rag.delete_document(doc_id, user_id)
            except Exception as e:
                self.app.logger.error(f"Failed to delete old vectors for doc {doc_id}: {e}")
            try:
                indexing_started_at = get_current_time_for_db()
                update_document_index_status(doc_id, INDEX_STATUS_INDEXING, indexing_started_at=indexing_started_at)
                success, message = rag.index_document(user_id, doc_id, full_path)
                if success:
                    indexed_at = get_current_time_for_db()
                    embedding_model = self._get_model_name('embedding') or 'unknown'
                    update_document_index_status(doc_id, INDEX_STATUS_INDEXED, indexed_at=indexed_at, embedding_model=embedding_model)
                    self.app.logger.info(f"Set embedding_model for doc {doc_id} to {embedding_model}")
                    success_count += 1
                    self.app.logger.info(f"Reindexed doc {doc_id}: {message}")
                else:
                    update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                    fail_count += 1
                    self.app.logger.error(f"Failed to reindex doc {doc_id}: {message}")
            except Exception as e:
                update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                fail_count += 1
                self.app.logger.error(f"Exception reindexing doc {doc_id}: {e}")
        self.app.logger.info(f"Reindex all completed. Total: {total}, Success: {success_count}, Failed: {fail_count}")
        return {'success': True, 'total': total, 'success_count': success_count, 'failed_count': fail_count}

    def get_user_requests_status(self, user_id: str, lang: str = 'ru') -> Dict[str, Any]:
        result = {'processing': None, 'queued': [], 'recent_completed': []}
        user_requests = self.redis.smembers(f"{self.user_requests_key}:{user_id}")
        user_requests = {r.decode() if isinstance(r, bytes) else r for r in user_requests}
        processing_tasks = self.redis.hgetall(self.processing_key)
        for req_id, task_data in processing_tasks.items():
            req_id = req_id.decode() if isinstance(req_id, bytes) else req_id
            if req_id in user_requests:
                task = pickle.loads(task_data)
                task['status'] = 'processing'
                result['processing'] = self._format_request_info(task, lang)
        queue_length = self.redis.llen(self.queue_key)
        queue_tasks = self.redis.lrange(self.queue_key, 0, queue_length - 1) if queue_length > 0 else []
        position = 1
        for task_data in queue_tasks:
            task = pickle.loads(task_data)
            if task.get('user_id') == user_id:
                task['status'] = 'queued'
                task['position_info'] = {'position': position, 'estimated_seconds': max(1, position * 5)}
                result['queued'].append(self._format_request_info(task, lang))
                position += 1
        return result

    def _format_request_info(self, task: Dict[str, Any], lang: str = 'ru') -> Dict[str, Any]:
        type_icons = {'text': '💬', 'image': '🎨', 'camera': '📷', 'reasoning': '🧠', 'audio': '🎤', 'index_document': '📄', 'transcribe_audio': '🎤'}
        return {
            'id': task['id'],
            'session_id': task.get('session_id'),
            'session_title': task.get('session_title', self.app.modules['base']._('Unknown session', lang=lang)),
            'type': task.get('data', {}).get('type', task.get('type', 'unknown')),
            'type_icon': type_icons.get(task.get('data', {}).get('type', task.get('type', 'unknown')), '📄'),
            'status': task.get('status', 'queued'),
            'position_info': task.get('position_info', {'position': '?', 'estimated_seconds': 5}),
            'preview': task.get('data', {}).get('preview', '')
        }

    def check_result(self, request_id: str) -> Optional[Dict[str, Any]]:
        result_data = self.redis.hget(self.results_key, request_id)
        if result_data:
            return pickle.loads(result_data)
        return None