import os
import sqlite3


DB_PATH = os.path.join(os.getcwd(), "tracker.db")


def clear_database():
    """Drops tables and cleans up database structure completely."""
    if not os.path.exists(DB_PATH):
        print(f"⚠️ Database file '{DB_PATH}' does not exist.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS products;")
    cursor.execute("DROP TABLE IF EXISTS price_history;")

    conn.commit()
    conn.close()
    print("✅ Database tables dropped successfully!")


if __name__ == "__main__":
    clear_database()