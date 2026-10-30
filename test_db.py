import getpass
import psycopg

password = getpass.getpass("Enter PostgreSQL password: ")

try:
    with psycopg.connect(
        host="localhost",
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=password
    ) as connection:
        print("Successfully connected to PostgreSQL!")

except psycopg.Error as error:
    print("Database connection failed:", error)