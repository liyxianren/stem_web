#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forum Posts Table Schema Update Script
Add education_level field to forum_posts table for complete three-tier indexing

Please backup database before execution!
"""

import pymysql
import os
from datetime import datetime
import sys

# Set console encoding for Windows
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

def get_db_connection():
    """Get database connection from environment variables or default config"""
    try:
        # Try to get database config from environment variables
        if os.environ.get('DATABASE_URL'):
            # Parse DATABASE_URL (format: mysql://user:password@host:port/database)
            import re
            db_url = os.environ.get('DATABASE_URL')
            match = re.match(r'mysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', db_url)
            if match:
                user, password, host, port, database = match.groups()
                return pymysql.connect(
                    host=host,
                    port=int(port),
                    user=user,
                    password=password,
                    database=database,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        
        # Zeabur MySQL database config
        return pymysql.connect(
            host='sha1.clusters.zeabur.com',
            port=31890,
            user='root',
            password='NgiW5632UC0vmTD7ZlEuPAye9ao41JM8',  
            database='zeabur',
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

def check_column_exists(cursor, table_name, column_name):
    """Check if specified column exists in table"""
    cursor.execute(f"""
        SELECT COLUMN_NAME 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_SCHEMA = DATABASE() 
        AND TABLE_NAME = '{table_name}' 
        AND COLUMN_NAME = '{column_name}'
    """)
    return cursor.fetchone() is not None

def update_forum_schema():
    """Update forum_posts table structure to support three-tier indexing"""
    connection = get_db_connection()
    if not connection:
        print("ERROR: Unable to connect to database, please check configuration")
        return False
    
    try:
        cursor = connection.cursor()
        
        print("Checking current forum_posts table structure...")
        
        # Check if education_level field exists
        if check_column_exists(cursor, 'forum_posts', 'education_level'):
            print("FOUND: education_level field already exists, skipping")
        else:
            print("ADDING: education_level field to forum_posts table...")
            cursor.execute("""
                ALTER TABLE forum_posts 
                ADD COLUMN education_level VARCHAR(20) DEFAULT NULL 
                COMMENT 'Education level: igcse, alevel, ap, competition, university'
                AFTER category
            """)
            print("SUCCESS: education_level field added")
        
        # Check if subject field exists
        if check_column_exists(cursor, 'forum_posts', 'subject'):
            print("FOUND: subject field already exists, skipping")
        else:
            print("ADDING: subject field to forum_posts table...")
            cursor.execute("""
                ALTER TABLE forum_posts 
                ADD COLUMN subject VARCHAR(20) DEFAULT NULL 
                COMMENT 'Subject: math, physics, chemistry, biology'
                AFTER education_level
            """)
            print("SUCCESS: subject field added")
        
        # Create composite index for three-tier indexing optimization
        print("CREATING: Composite index for query optimization...")
        try:
            cursor.execute("""
                CREATE INDEX idx_forum_three_tier 
                ON forum_posts (subject, education_level, category, status)
            """)
            print("SUCCESS: Composite index created")
        except pymysql.Error as e:
            if "Duplicate key name" in str(e):
                print("FOUND: Composite index already exists, skipping")
            else:
                print(f"WARNING: Index creation warning: {e}")
        
        connection.commit()
        print("\nForum table structure update completed!")
        print("\nUpdated three-tier index structure:")
        print("   Tier 1 - subject: math, physics, chemistry, biology")
        print("   Tier 2 - education_level: igcse, alevel, ap, competition, university") 
        print("   Tier 3 - category: books, homework, tests, notes, questions")
        
        # Display updated table structure
        cursor.execute("DESCRIBE forum_posts")
        columns = cursor.fetchall()
        print("\nForum_posts table field list:")
        for col in columns:
            comment = col.get('Comment', 'No comment')
            print(f"   {col['Field']} ({col['Type']}) - {comment or 'No comment'}")
            
        return True
        
    except Exception as e:
        print(f"ERROR: Update failed: {e}")
        connection.rollback()
        return False
    finally:
        cursor.close()
        connection.close()

def main():
    print("=" * 60)
    print("Forum Posts Three-Tier Index Structure Update Tool")
    print("=" * 60)
    print(f"Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("WARNING: Please ensure database is backed up before proceeding!")
    
    # Auto-confirm for script execution
    print("\nAuto-confirming database structure update...")
    print("Proceeding with update...")
    
    success = update_forum_schema()
    if success:
        print("\nDatabase structure update completed! Now supports full three-tier indexing")
        print("Suggestion: Update application code to use new field structure")
    else:
        print("\nUpdate failed, please check error messages")

if __name__ == "__main__":
    main()