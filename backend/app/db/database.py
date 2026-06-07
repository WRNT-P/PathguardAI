# app/db/database.py

import psycopg2

def get_db():
    return psycopg2.connect(
        host="localhost",
        database="pathguard",
        user="postgres",
        password="yourpassword"
    )