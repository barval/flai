import redis
import pickle
import uuid
import time
import threading
import sqlite3
from .utils import get_current_time_in_timezone, get_current_time_in_timezone_for_db
from .db import save_message, CHAT_DB_PATH

class RedisRequestQueue:
    def __init__(self, app):
        self.app = app
        self.redis = redis.from_url(app.config['REDIS_URL'], decode_responses=False)
        self.queue_key = 'request_queue'
        self.processing_key = 'processing_requests'
        self.results_key = 'request_results'
        self.user_requests_key = 'user_requests'
        self.timeouts = {}
        self.start_worker()

    def start_worker(self):
        thread = threading.Thread(target=self._worker_loop, daemon=True)
        thread.start()
        self.app.logger.info("RedisRequestQueue: воркер запущен")

    def _worker_loop(self):
        self.app.logger.info("RedisRequestQueue: запуск цикла обработки")
        while True:
            try:
                result = self.redis.blpop(self.queue_key, timeout=5)
                if not result:
                    continue
                queue_key, task_data = result
                task = pickle.loads(task_data)
                queue_time = time.time() - task.get('timestamp', time.time())
                if queue_time > 300:
                    self.app.logger.warning(f"Задача {task['id']} слишком долго ждала в очереди ({queue_time:.1f}с). Отмена.")
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'error',
                        'error': f'Запрос отменён - слишком долгое ожидание в очереди ({queue_time:.1f}с)',
                        'result': {'session_id': task['session_id']},
                        'timestamp': time.time()
                    }))
                    continue
                self.app.logger.info(f"RedisRequestQueue: получена задача {task['id']} из очереди для сеанса {task['session_id']}, ожидание в очереди: {queue_time:.1f}с")
                self.redis.hset(self.processing_key, task['id'], task_data)
                try:
                    # Выполняем задачу в контексте приложения
                    with self.app.app_context():
                        result_data = self._process_request(task)
                    if 'session_id' not in result_data:
                        result_data['session_id'] = task['session_id']
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'completed',
                        'result': result_data,
                        'timestamp': time.time()
                    }))
                    self.app.logger.info(f"RedisRequestQueue: задача {task['id']} выполнена успешно для сеанса {task['session_id']}")
                except Exception as e:
                    self.app.logger.error(f"RedisRequestQueue: ошибка обработки задачи {task['id']}: {str(e)}")
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'error',
                        'error': str(e),
                        'result': {'session_id': task['session_id']},
                        'timestamp': time.time()
                    }))
                finally:
                    self.redis.hdel(self.processing_key, task['id'])
            except Exception as e:
                self.app.logger.error(f"RedisRequestQueue: ошибка в worker loop: {str(e)}")
                time.sleep(1)

    def add_request(self, user_id, session_id, request_data, user_class):
        request_id = str(uuid.uuid4())
        timestamp = time.time()
        task = {
            'id': request_id,
            'user_id': user_id,
            'session_id': session_id,
            'data': request_data,
            'timestamp': timestamp,
            'user_class': user_class,
            'session_title': self._get_session_title(session_id)
        }
        self.app.logger.info(f"RedisRequestQueue.add_request: добавление задачи {request_id} для сеанса {session_id} с временем {timestamp}")
        self.redis.rpush(self.queue_key, pickle.dumps(task))
        self.redis.sadd(f"{self.user_requests_key}:{user_id}", request_id)
        queue_length = self.redis.llen(self.queue_key)
        estimated_wait = max(1, queue_length * 5)
        position_info = {'position': queue_length, 'estimated_seconds': estimated_wait}
        self.app.logger.info(f"RedisRequestQueue.add_request: задача добавлена, позиция={queue_length}")
        return request_id, position_info

    def get_user_queue_counts(self, user_id):
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

    def _get_session_title(self, session_id):
        try:
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                c = conn.cursor()
                c.execute('SELECT title FROM chat_sessions WHERE id = ?', (session_id,))
                row = c.fetchone()
                return row[0] if row else "Неизвестный сеанс"
        except Exception as e:
            self.app.logger.error(f"Ошибка получения заголовка сессии: {str(e)}")
            return "Неизвестный сеанс"

    def _process_request(self, task):
        self.app.logger.info(f"RedisRequestQueue._process_request: обработка задачи {task['id']} для сеанса {task['session_id']}")
        user_id = task['user_id']
        session_id = task['session_id']
        request_data = task['data']
        processing_start_time = time.time()
        current_time_str = get_current_time_in_timezone(self.app)

        request_type = request_data.get('type', 'text')
        message_text = request_data.get('text', '')
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')

        if request_type == 'text':
            router_start_time = time.time()
            router_result = self.app.modules['base'].process_message(message_text, current_time_str)
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
            model_used = self.app.config['LLM_CHAT_MODEL']
            is_error = False
            process_time = 0

            if action_type == 'image':
                if 'image' in self.app.modules and self.app.modules['image'].available:
                    mm_start_time = time.time()
                    prompt_data, error = self.app.modules['multimodal'].generate_image_params(query)
                    mm_time = round(time.time() - mm_start_time, 1)
                    if error:
                        final_response = f"⚠️ {error}"
                        model_used = 'system'
                        is_error = True
                        process_time = mm_time
                    else:
                        gen_start_time = time.time()
                        image_result = self.app.modules['image']._call_automatic1111(prompt_data)
                        gen_time = round(time.time() - gen_start_time, 1)
                        if image_result['success']:
                            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                            image_result['mm_time'] = mm_time
                            image_result['gen_time'] = gen_time
                            image_result['mm_model'] = self.app.config['LLM_MULTIMODAL_MODEL']
                            image_result['gen_model'] = self.app.config['AUTOMATIC1111_MODEL']
                            message_text = f"Изображение сгенерировано по запросу: {query}"
                            save_message(
                                session_id, 'assistant', message_text,
                                image_result['image_data'], image_result['file_type'],
                                image_result['file_name'], self.app.config['AUTOMATIC1111_MODEL'],
                                response_time={'mm_time': mm_time, 'gen_time': gen_time},
                                mm_time=str(mm_time), gen_time=str(gen_time),
                                mm_model=self.app.config['LLM_MULTIMODAL_MODEL'],
                                gen_model=self.app.config['AUTOMATIC1111_MODEL']
                            )
                            return {
                                'response': message_text,
                                'session_id': session_id,
                                'model_used': self.app.config['AUTOMATIC1111_MODEL'],
                                'assistant_timestamp': completion_time_for_db,
                                'generated_image': image_result['image_data'],
                                'file_name': image_result['file_name'],
                                'file_size': image_result['file_size'],
                                'file_type': image_result['file_type'],
                                'mm_time': mm_time,
                                'gen_time': gen_time,
                                'mm_model': image_result['mm_model'],
                                'gen_model': image_result['gen_model'],
                                'response_time': {'mm_time': mm_time, 'gen_time': gen_time, 'mm_model': image_result['mm_model'], 'gen_model': image_result['gen_model']},
                                'is_error': False
                            }
                        else:
                            final_response = f"⚠️ {image_result['error']}"
                            model_used = 'system'
                            is_error = True
                            process_time = mm_time + gen_time
                else:
                    final_response = "⚠️ Модуль генерации изображений недоступен"
                    model_used = 'system'
                    is_error = True
                    process_time = 0

            elif action_type == 'camera':
                if 'cam' in self.app.modules and self.app.modules['cam'].available:
                    camera_start_time = time.time()
                    # Исправлено: передаём user_id и query
                    camera_result = self.app.modules['cam'].get_snapshot(user_id, query)
                    camera_time = round(time.time() - camera_start_time, 1)
                    if camera_result['success']:
                        completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
                        camera_model = 'camera'
                        save_message(
                            session_id, 'assistant',
                            f"Изображение с камеры: {camera_result['room_name']}",
                            camera_result['image_data'], camera_result['image_type'],
                            camera_result['file_name'], camera_model,
                            response_time=str(camera_time)
                        )
                        first_message = {
                            'response': f"Изображение с камеры: {camera_result['room_name']}",
                            'session_id': session_id,
                            'model_used': camera_model,
                            'assistant_timestamp': completion_time_for_db,
                            'generated_image': camera_result['image_data'],
                            'file_name': camera_result['file_name'],
                            'file_size': camera_result['file_size'],
                            'file_type': camera_result['image_type'],
                            'response_time': camera_time,
                            'is_error': False
                        }
                        messages = [first_message]
                        if message_text and 'multimodal' in self.app.modules and self.app.modules['multimodal'].available:
                            mm_start_time = time.time()
                            bot_reply, error = self.app.modules['multimodal'].process_image_with_text(
                                camera_result['image_data'], message_text, current_time_str
                            )
                            mm_time = round(time.time() - mm_start_time, 1)
                            if error:
                                bot_reply = f"⚠️ {error}"
                                is_error = True
                            else:
                                is_error = False
                            save_message(
                                session_id, 'assistant', bot_reply,
                                model_name=self.app.config['LLM_MULTIMODAL_MODEL'],
                                response_time=str(mm_time)
                            )
                            second_message = {
                                'response': bot_reply,
                                'session_id': session_id,
                                'model_used': self.app.config['LLM_MULTIMODAL_MODEL'],
                                'assistant_timestamp': get_current_time_in_timezone_for_db(self.app),
                                'response_time': mm_time,
                                'is_error': is_error
                            }
                            messages.append(second_message)
                        return {'messages': messages, 'session_id': session_id}
                    else:
                        final_response = f"⚠️ {camera_result['error']}"
                        model_used = 'system'
                        is_error = True
                        process_time = camera_time
                else:
                    final_response = "⚠️ Модуль видеонаблюдения недоступен"
                    model_used = 'system'
                    is_error = True
                    process_time = 0

            elif action_type == 'reasoning':
                if router_result.get('needs_reasoning'):
                    reasoning_start_time = time.time()
                    final_response = self.app.modules['base'].process_reasoning(query, current_time_str)
                    process_time = round(time.time() - reasoning_start_time, 1)
                    model_used = self.app.config['LLM_REASONING_MODEL']
                else:
                    process_time = 0
                    final_response = query
                is_error = False

            else:  # action_type == 'none'
                process_time = router_time
                final_response = query
                is_error = False

            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            if final_response:
                save_message(session_id, 'assistant', final_response, model_name=model_used, response_time=str(process_time))
            return {
                'response': final_response,
                'session_id': session_id,
                'model_used': model_used,
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,
                'is_error': is_error
            }

        elif request_type == 'image' and file_data:
            process_start_time = time.time()
            is_error = False
            if 'multimodal' in self.app.modules and self.app.modules['multimodal'].available:
                file_size = int((len(file_data) * 3) / 4) if file_data else 0
                is_valid, error = self.app.modules['multimodal'].validate_image(file_data, file_type, file_name, file_size)
                if is_valid:
                    bot_reply, error = self.app.modules['multimodal'].process_image_with_text(file_data, message_text, current_time_str)
                    process_time = round(time.time() - process_start_time, 1)
                    if error:
                        bot_reply = f"⚠️ {error}"
                        is_error = True
                else:
                    bot_reply = f"⚠️ {error}"
                    process_time = round(time.time() - process_start_time, 1)
                    is_error = True
            else:
                bot_reply = "⚠️ Мультимодальная модель недоступна"
                process_time = round(time.time() - process_start_time, 1)
                is_error = True
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            save_message(session_id, 'assistant', bot_reply, model_name=self.app.config['LLM_MULTIMODAL_MODEL'] if 'multimodal' in self.app.modules else 'system', response_time=str(process_time))
            return {
                'response': bot_reply,
                'session_id': session_id,
                'model_used': self.app.config['LLM_MULTIMODAL_MODEL'] if 'multimodal' in self.app.modules else 'system',
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,
                'is_error': is_error
            }

        else:
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            return {
                'error': 'Неизвестный тип запроса',
                'session_id': session_id,
                'assistant_timestamp': completion_time_for_db,
                'is_error': True,
                'response_time': 0
            }

    def get_user_requests_status(self, user_id):
        result = {'processing': None, 'queued': [], 'recent_completed': []}
        user_requests = self.redis.smembers(f"{self.user_requests_key}:{user_id}")
        user_requests = {r.decode() if isinstance(r, bytes) else r for r in user_requests}

        processing_tasks = self.redis.hgetall(self.processing_key)
        for req_id, task_data in processing_tasks.items():
            req_id = req_id.decode() if isinstance(req_id, bytes) else req_id
            if req_id in user_requests:
                task = pickle.loads(task_data)
                task['status'] = 'processing'
                result['processing'] = self._format_request_info(task)

        queue_length = self.redis.llen(self.queue_key)
        queue_tasks = self.redis.lrange(self.queue_key, 0, queue_length - 1) if queue_length > 0 else []
        position = 1
        for task_data in queue_tasks:
            task = pickle.loads(task_data)
            if task['user_id'] == user_id:
                task['status'] = 'queued'
                task['position_info'] = {'position': position, 'estimated_seconds': max(1, position * 5)}
                result['queued'].append(self._format_request_info(task))
            position += 1
        return result

    def _format_request_info(self, task):
        type_icons = {'text': '💬', 'image': '🎨', 'camera': '📷', 'reasoning': '🧠', 'audio': '🎤'}
        return {
            'id': task['id'],
            'session_id': task['session_id'],
            'session_title': task.get('session_title', 'Неизвестный сеанс'),
            'type': task['data'].get('type', 'unknown'),
            'type_icon': type_icons.get(task['data'].get('type', 'unknown'), '📄'),
            'status': task.get('status', 'queued'),
            'position_info': task.get('position_info', {'position': '?', 'estimated_seconds': 5}),
            'preview': task['data'].get('preview', '')
        }

    def check_result(self, request_id):
        result_data = self.redis.hget(self.results_key, request_id)
        if result_data:
            return pickle.loads(result_data)
        return None