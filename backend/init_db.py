import mysql.connector

config = {
    'user': 'ari',
    'password': 'ari',
    'host': 'localhost',
    'port': '3307',
    'database': 'privateafter_db',
    'raise_on_warnings': True
}

conn = None
cursor = None
try:
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    # Tabela para encodings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS encodings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL,
            encoding JSON NOT NULL
        )
    """)

    # Tabela para cameras
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cameras (
            id INT AUTO_INCREMENT PRIMARY KEY,
            camera_id VARCHAR(255) UNIQUE NOT NULL,
            url VARCHAR(255) NOT NULL
        )
    """)

    conn.commit()
    print("Tabelas criadas com sucesso.")
except mysql.connector.Error as err:
    print(f"Erro: {err}")
finally:
    if cursor is not None:
        cursor.close()
    if conn is not None:
        conn.close()