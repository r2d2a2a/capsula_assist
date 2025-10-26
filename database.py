import sqlite3
import datetime
import logging
from typing import List, Dict, Optional
import pytz

logger = logging.getLogger(__name__)

class TaskDatabase:
    def __init__(self, db_path: str = 'tasks.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица определений задач (шаблоны задач пользователей)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_definitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                frequency TEXT NOT NULL, -- 'daily' или 'weekly'
                days TEXT,               -- список дней недели через запятую: "0,1,2,3,4,5,6"
                reminder_time TEXT NOT NULL, -- HH:MM
                check_time TEXT NOT NULL,    -- HH:MM
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')

        # Таблица для хранения задач
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task_def_id INTEGER,
                task_type TEXT NOT NULL,
                date TEXT NOT NULL,
                reminder_sent BOOLEAN DEFAULT FALSE,
                check_sent BOOLEAN DEFAULT FALSE,
                completed BOOLEAN DEFAULT FALSE,
                completion_time TEXT,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Гарантируем уникальность задачи на дату
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_unique
            ON tasks(task_type, date)
        ''')
        # На случай существующей таблицы без столбца comment
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN comment TEXT')
        except sqlite3.OperationalError:
            # Столбец уже существует
            pass
        
        # На случай существующей таблицы без столбца check_sent
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN check_sent BOOLEAN DEFAULT FALSE')
        except sqlite3.OperationalError:
            # Столбец уже существует
            pass

        # Миграции: добавляем столбцы user_id и task_def_id, если отсутствуют
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN user_id INTEGER')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN task_def_id INTEGER')
        except sqlite3.OperationalError:
            pass

        # Индекс уникальности для новой модели: уникально по пользователю, определению и дате
        try:
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_unique_user_def_date
                ON tasks(user_id, task_def_id, date)
            ''')
        except sqlite3.OperationalError:
            pass
        
        # Таблица для хранения отчетов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL, -- 'daily' или 'weekly'
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                total_tasks INTEGER DEFAULT 0,
                completed_tasks INTEGER DEFAULT 0,
                completion_rate REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Миграция: добавить user_id в reports, если старая таблица без этого столбца
        # Проверяем, есть ли уже колонка user_id
        cursor.execute("PRAGMA table_info(reports)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'user_id' not in columns:
            try:
                cursor.execute('ALTER TABLE reports ADD COLUMN user_id INTEGER')
                conn.commit()
            except sqlite3.OperationalError as e:
                # Ошибка при добавлении столбца
                logger.error(f"Не удалось добавить колонку user_id в таблицу reports: {e}")
                pass
        
        conn.commit()
        conn.close()
    
    def add_task(self, task_type: str, date: str) -> int:
        """Добавить задачу"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO tasks (task_type, date)
            VALUES (?, ?)
        ''', (task_type, date))
        
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return task_id

    def acquire_send_lock(self, task_type: str, date: str) -> (bool, int):
        """Атомарно создать задачу (если нет) и установить флаг отправки напоминания.

        Возвращает кортеж (lock_acquired, task_id).
        Если lock_acquired = True — текущий процесс должен отправить напоминание.
        Если False — напоминание уже отправлялось другим процессом.
        """
        conn = sqlite3.connect(self.db_path)
        # Важно: включаем немедленную блокировку для предотвращения гонок между процессами
        conn.isolation_level = None
        cursor = conn.cursor()
        try:
            cursor.execute('BEGIN IMMEDIATE')
            cursor.execute('SELECT id, reminder_sent FROM tasks WHERE task_type = ? AND date = ?', (task_type, date))
            row = cursor.fetchone()
            if row:
                task_id, reminder_sent = row
                if reminder_sent:
                    cursor.execute('COMMIT')
                    return False, task_id
                # Помечаем как отправленное и разрешаем отправку из текущего процесса
                cursor.execute('UPDATE tasks SET reminder_sent = TRUE WHERE id = ?', (task_id,))
                cursor.execute('COMMIT')
                return True, task_id
            # Задачи еще нет — создаем сразу с reminder_sent = TRUE
            cursor.execute('''
                INSERT INTO tasks (task_type, date, reminder_sent)
                VALUES (?, ?, TRUE)
            ''', (task_type, date))
            task_id = cursor.lastrowid
            cursor.execute('COMMIT')
            return True, task_id
        except Exception:
            try:
                cursor.execute('ROLLBACK')
            except Exception:
                pass
            return False, -1
        finally:
            conn.close()

    def acquire_check_lock(self, task_type: str, date: str) -> (bool, int):
        """Атомарно получить право на отправку проверки выполнения.

        Возвращает кортеж (lock_acquired, task_id).
        Если lock_acquired = True — текущий процесс должен отправить проверку.
        Если False — проверка уже отправлялась другим процессом.
        """
        conn = sqlite3.connect(self.db_path)
        # Важно: включаем немедленную блокировку для предотвращения гонок между процессами
        conn.isolation_level = None
        cursor = conn.cursor()
        try:
            cursor.execute('BEGIN IMMEDIATE')
            cursor.execute('SELECT id, check_sent FROM tasks WHERE task_type = ? AND date = ?', (task_type, date))
            row = cursor.fetchone()
            if row:
                task_id, check_sent = row
                if check_sent:
                    cursor.execute('COMMIT')
                    return False, task_id
                # Помечаем как отправленное и разрешаем отправку из текущего процесса
                cursor.execute('UPDATE tasks SET check_sent = TRUE WHERE id = ?', (task_id,))
                cursor.execute('COMMIT')
                return True, task_id
            # Задачи еще нет — создаем сразу с check_sent = TRUE
            cursor.execute('''
                INSERT INTO tasks (task_type, date, check_sent)
                VALUES (?, ?, TRUE)
            ''', (task_type, date))
            task_id = cursor.lastrowid
            cursor.execute('COMMIT')
            return True, task_id
        except Exception:
            try:
                cursor.execute('ROLLBACK')
            except Exception:
                pass
            return False, -1
        finally:
            conn.close()

    # --------- Новая модель: многопользовательские задачи ---------

    def upsert_user(self, chat_id: int, username: Optional[str]) -> int:
        """Создать пользователя, если не существует, и вернуть его id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
            # Обновим username при изменении
            cursor.execute('UPDATE users SET username = ? WHERE id = ?', (username, user_id))
            conn.commit()
            conn.close()
            return user_id
        cursor.execute('INSERT INTO users (chat_id, username) VALUES (?, ?)', (chat_id, username))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id

    def get_user_by_chat_id(self, chat_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        columns = [d[0] for d in cursor.description]
        conn.close()
        return dict(zip(columns, row))

    def list_users(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users ORDER BY id')
        columns = [d[0] for d in cursor.description]
        users = [dict(zip(columns, r)) for r in cursor.fetchall()]
        conn.close()
        return users

    def count_task_definitions(self, user_id: int) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM task_definitions WHERE user_id = ? AND active = 1', (user_id,))
        row = cursor.fetchone()
        conn.close()
        return (row[0] or 0)

    def add_task_definition(self, user_id: int, name: str, frequency: str, days: List[int], reminder_time: str, check_time: str) -> int:
        """Создать определение задачи. days — список [0..6]."""
        days_str = ','.join(str(d) for d in sorted(set(days))) if days else ''
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO task_definitions (user_id, name, frequency, days, reminder_time, check_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, name, frequency, days_str, reminder_time, check_time))
        def_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return def_id

    def list_task_definitions(self, user_id: int) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM task_definitions WHERE user_id = ? AND active = 1 ORDER BY id', (user_id,))
        columns = [d[0] for d in cursor.description]
        defs = [dict(zip(columns, r)) for r in cursor.fetchall()]
        # Преобразуем days в список
        for d in defs:
            days_str = d.get('days') or ''
            d['days_list'] = [int(x) for x in days_str.split(',') if x.strip().isdigit()]
        conn.close()
        return defs

    def get_task_definition(self, user_id: int, def_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM task_definitions WHERE id = ? AND user_id = ? AND active = 1', (def_id, user_id))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        columns = [d[0] for d in cursor.description]
        result = dict(zip(columns, row))
        days_str = result.get('days') or ''
        result['days_list'] = [int(x) for x in days_str.split(',') if x.strip().isdigit()]
        conn.close()
        return result

    def update_task_definition(self, user_id: int, def_id: int, name: Optional[str] = None, frequency: Optional[str] = None,
                                days: Optional[List[int]] = None, reminder_time: Optional[str] = None, check_time: Optional[str] = None) -> bool:
        """Обновить поля определения задачи. Возвращает True, если обновлено >=1 строк."""
        set_parts = []
        params: List = []
        if name is not None:
            set_parts.append('name = ?')
            params.append(name)
        if frequency is not None:
            set_parts.append('frequency = ?')
            params.append(frequency)
        if days is not None:
            days_str = ','.join(str(d) for d in sorted(set(days))) if days else ''
            set_parts.append('days = ?')
            params.append(days_str)
        if reminder_time is not None:
            set_parts.append('reminder_time = ?')
            params.append(reminder_time)
        if check_time is not None:
            set_parts.append('check_time = ?')
            params.append(check_time)
        if not set_parts:
            return False
        params.extend([def_id, user_id])
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(f'UPDATE task_definitions SET {", ".join(set_parts)} WHERE id = ? AND user_id = ? AND active = 1', params)
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        return updated > 0

    def deactivate_task_definition(self, user_id: int, def_id: int) -> bool:
        """Пометить определение задачи как неактивное."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE task_definitions SET active = 0 WHERE id = ? AND user_id = ? AND active = 1', (def_id, user_id))
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        return updated > 0

    def acquire_send_lock_v2(self, user_id: int, task_def_id: int, date: str) -> (bool, int):
        """Новая версия блокировки для напоминаний (по пользователю и определению)."""
        conn = sqlite3.connect(self.db_path)
        conn.isolation_level = None
        cursor = conn.cursor()
        try:
            cursor.execute('BEGIN IMMEDIATE')
            cursor.execute('SELECT id, reminder_sent FROM tasks WHERE user_id = ? AND task_def_id = ? AND date = ?', (user_id, task_def_id, date))
            row = cursor.fetchone()
            if row:
                task_id, reminder_sent = row
                if reminder_sent:
                    cursor.execute('COMMIT')
                    return False, task_id
                cursor.execute('UPDATE tasks SET reminder_sent = TRUE WHERE id = ?', (task_id,))
                cursor.execute('COMMIT')
                return True, task_id
            # Вставляем синтетический task_type, чтобы удовлетворить NOT NULL и уникальный индекс (task_type, date)
            synthetic_task_type = f"u{user_id}_d{task_def_id}"
            cursor.execute('''
                INSERT INTO tasks (user_id, task_def_id, task_type, date, reminder_sent)
                VALUES (?, ?, ?, ?, TRUE)
            ''', (user_id, task_def_id, synthetic_task_type, date))
            task_id = cursor.lastrowid
            cursor.execute('COMMIT')
            return True, task_id
        except Exception:
            try:
                cursor.execute('ROLLBACK')
            except Exception:
                pass
            return False, -1
        finally:
            conn.close()

    def acquire_check_lock_v2(self, user_id: int, task_def_id: int, date: str) -> (bool, int):
        """Новая версия блокировки для проверок (по пользователю и определению)."""
        conn = sqlite3.connect(self.db_path)
        conn.isolation_level = None
        cursor = conn.cursor()
        try:
            cursor.execute('BEGIN IMMEDIATE')
            cursor.execute('SELECT id, check_sent FROM tasks WHERE user_id = ? AND task_def_id = ? AND date = ?', (user_id, task_def_id, date))
            row = cursor.fetchone()
            if row:
                task_id, check_sent = row
                if check_sent:
                    cursor.execute('COMMIT')
                    return False, task_id
                cursor.execute('UPDATE tasks SET check_sent = TRUE WHERE id = ?', (task_id,))
                cursor.execute('COMMIT')
                return True, task_id
            # Вставляем синтетический task_type аналогично acquire_send_lock_v2
            synthetic_task_type = f"u{user_id}_d{task_def_id}"
            cursor.execute('''
                INSERT INTO tasks (user_id, task_def_id, task_type, date, check_sent)
                VALUES (?, ?, ?, ?, TRUE)
            ''', (user_id, task_def_id, synthetic_task_type, date))
            task_id = cursor.lastrowid
            cursor.execute('COMMIT')
            return True, task_id
        except Exception:
            try:
                cursor.execute('ROLLBACK')
            except Exception:
                pass
            return False, -1
        finally:
            conn.close()

    def set_task_comment(self, task_type: str, date: str, comment: str):
        """Сохранить комментарий к задаче"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tasks SET comment = ? WHERE task_type = ? AND date = ?
        ''', (comment, task_type, date))
        conn.commit()
        conn.close()

    def set_task_comment_v2(self, user_id: int, task_def_id: int, date: str, comment: str):
        """Сохранить комментарий к задаче (по пользователю/определению)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tasks SET comment = ? WHERE user_id = ? AND task_def_id = ? AND date = ?
        ''', (comment, user_id, task_def_id, date))
        conn.commit()
        conn.close()
    
    def mark_reminder_sent(self, task_id: int):
        """Отметить, что напоминание отправлено"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tasks SET reminder_sent = TRUE WHERE id = ?
        ''', (task_id,))
        
        conn.commit()
        conn.close()
    
    def mark_task_completed(self, task_type: str, date: str, completed: bool = True):
        """Отметить задачу как выполненную/не выполненную"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        completion_time = datetime.datetime.now(pytz.timezone('Europe/Moscow')).isoformat() if completed else None
        
        cursor.execute('''
            UPDATE tasks 
            SET completed = ?, completion_time = ?
            WHERE task_type = ? AND date = ?
        ''', (completed, completion_time, task_type, date))
        
        conn.commit()
        conn.close()

    def mark_task_completed_v2(self, user_id: int, task_def_id: int, date: str, completed: bool = True):
        """Отметить задачу как выполненную/не выполненную (по пользователю/определению)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        completion_time = datetime.datetime.now(pytz.timezone('Europe/Moscow')).isoformat() if completed else None
        cursor.execute('''
            UPDATE tasks 
            SET completed = ?, completion_time = ?
            WHERE user_id = ? AND task_def_id = ? AND date = ?
        ''', (completed, completion_time, user_id, task_def_id, date))
        conn.commit()
        conn.close()
    
    def get_tasks_for_date(self, date: str) -> List[Dict]:
        """Получить все задачи на определенную дату (глобально, для совместимости)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tasks WHERE date = ? ORDER BY task_type
        ''', (date,))
        
        columns = [description[0] for description in cursor.description]
        tasks = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return tasks
    
    def get_tasks_for_date_by_user(self, user_id: int, date: str) -> List[Dict]:
        """Получить все задачи пользователя на дату"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM tasks WHERE user_id = ? AND date = ? ORDER BY task_def_id
        ''', (user_id, date))
        columns = [description[0] for description in cursor.description]
        tasks = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return tasks
    
    def get_tasks_for_period(self, start_date: str, end_date: str) -> List[Dict]:
        """Получить все задачи за период (глобально, для совместимости)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tasks 
            WHERE date BETWEEN ? AND ? 
            ORDER BY date, task_type
        ''', (start_date, end_date))
        
        columns = [description[0] for description in cursor.description]
        tasks = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return tasks
    
    def get_tasks_for_period_by_user(self, user_id: int, start_date: str, end_date: str) -> List[Dict]:
        """Получить все задачи пользователя за период"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM tasks 
            WHERE user_id = ? AND date BETWEEN ? AND ? 
            ORDER BY date, task_def_id
        ''', (user_id, start_date, end_date))
        columns = [description[0] for description in cursor.description]
        tasks = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return tasks
    
    def get_completion_stats(self, start_date: str, end_date: str) -> Dict:
        """Получить статистику выполнения за период (глобально, для совместимости)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total_tasks,
                SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) as completed_tasks
            FROM tasks 
            WHERE date BETWEEN ? AND ?
        ''', (start_date, end_date))
        
        result = cursor.fetchone()
        total_tasks = result[0] or 0
        completed_tasks = result[1] or 0
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        
        conn.close()
        
        return {
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'completion_rate': round(completion_rate, 1)
        }
    
    def get_completion_stats_by_user(self, user_id: int, start_date: str, end_date: str) -> Dict:
        """Получить статистику выполнения пользователя за период"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total_tasks,
                SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) as completed_tasks
            FROM tasks 
            WHERE user_id = ? AND date BETWEEN ? AND ?
        ''', (user_id, start_date, end_date))
        result = cursor.fetchone()
        total_tasks = result[0] or 0
        completed_tasks = result[1] or 0
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        conn.close()
        return {
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'completion_rate': round(completion_rate, 1)
        }
    
    def save_report(self, report_type: str, period_start: str, period_end: str, stats: Dict, user_id: Optional[int] = None):
        """Сохранить отчет. Для многопользовательской версии передавайте user_id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO reports (report_type, period_start, period_end, total_tasks, completed_tasks, completion_rate, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (report_type, period_start, period_end, stats['total_tasks'], stats['completed_tasks'], stats['completion_rate'], user_id))
        
        conn.commit()
        conn.close()
