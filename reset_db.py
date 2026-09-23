import sqlite3

DB_NAME = 'price_tracker.db'


def clear_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Wipe product list and price history
    cursor.execute('DELETE FROM products;')
    cursor.execute('DELETE FROM price_history;')

    conn.commit()
    conn.close()
    print("✅ Database cleared successfully! All tracked products and history removed.")


if __name__ == "__main__":
    clear_database()