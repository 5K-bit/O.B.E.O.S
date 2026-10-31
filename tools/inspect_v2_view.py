import sqlite3,json
from pathlib import Path
root=Path("artifacts/native-v2-offline")
for database in root.rglob("*.sqlite3"):
    con=sqlite3.connect(f"file:{database.as_posix()}?mode=ro",uri=True)
    tables={v[0] for v in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "scene" in tables:
        print("scene",con.execute("SELECT payload FROM scene").fetchone()[0])
    if "observations" in tables:
        print("history",con.execute("SELECT layer,count(*),min(lat),max(lat),min(lon),max(lon) FROM observations GROUP BY layer").fetchall())
    con.close()
