import sqlite3

conn = sqlite3.connect("organizer.db")
cursor = conn.cursor()

print("--- ZDJĘCIA W BAZIE ---")
cursor.execute("SELECT id, file_path, processed FROM photos")
for row in cursor.fetchall():
    print(f"ID: {row[0]} | Ścieżka: {row[1]} | Przetworzone: {row[2]}")

print("\n--- WYKRYTE TWARZE (Nieznane klastry) ---")
cursor.execute("SELECT id, photo_id, person_id FROM faces")
for row in cursor.fetchall():
    print(f"Face ID: {row[0]} | Photo ID: {row[1]} | Przypisana Osoba (ID): {row[2]}")

conn.close()