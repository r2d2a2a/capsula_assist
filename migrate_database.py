#!/usr/bin/env python3
"""
Скрипт для миграции базы данных - добавляет недостающую колонку user_id в таблицу reports
"""

import sqlite3
import sys
from pathlib import Path

def migrate_database(db_path='tasks.db'):
    """Добавить колонку user_id в таблицу reports, если её нет"""
    try:
        print(f"🔧 Миграция базы данных: {db_path}")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем структуру таблицы reports
        cursor.execute("PRAGMA table_info(reports)")
        columns = [row[1] for row in cursor.fetchall()]
        
        print(f"📋 Текущие колонки в таблице reports: {columns}")
        
        if 'user_id' not in columns:
            print("➕ Добавляю колонку user_id...")
            cursor.execute('ALTER TABLE reports ADD COLUMN user_id INTEGER')
            conn.commit()
            print("✅ Колонка user_id успешно добавлена!")
        else:
            print("✅ Колонка user_id уже существует")
        
        # Проверяем результат
        cursor.execute("PRAGMA table_info(reports)")
        columns_after = [row[1] for row in cursor.fetchall()]
        print(f"📋 Колонки после миграции: {columns_after}")
        
        conn.close()
        print("✅ Миграция завершена успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при миграции: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    db_file = 'tasks.db'
    if len(sys.argv) > 1:
        db_file = sys.argv[1]
    
    success = migrate_database(db_file)
    sys.exit(0 if success else 1)

