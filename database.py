import sqlite3
import datetime
from typing import List, Dict, Optional
import pytz

class TaskDatabase:
    def __init__(self, db_path: str = 'tasks.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица для хранения задач
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                date TEXT NOT NULL,
                reminder_sent BOOLEAN DEFAULT FALSE,
                completed BOOLEAN DEFAULT FALSE,
                completion_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
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
    
    def get_tasks_for_date(self, date: str) -> List[Dict]:
        """Получить все задачи на определенную дату"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tasks WHERE date = ? ORDER BY task_type
        ''', (date,))
        
        columns = [description[0] for description in cursor.description]
        tasks = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return tasks
    
    def get_tasks_for_period(self, start_date: str, end_date: str) -> List[Dict]:
        """Получить все задачи за период"""
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
    
    def get_completion_stats(self, start_date: str, end_date: str) -> Dict:
        """Получить статистику выполнения за период"""
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
    
    def save_report(self, report_type: str, period_start: str, period_end: str, stats: Dict):
        """Сохранить отчет"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO reports (report_type, period_start, period_end, total_tasks, completed_tasks, completion_rate)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (report_type, period_start, period_end, stats['total_tasks'], stats['completed_tasks'], stats['completion_rate']))
        
        conn.commit()
        conn.close()
