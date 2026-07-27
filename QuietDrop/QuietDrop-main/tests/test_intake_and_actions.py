from __future__ import annotations
from pathlib import Path

from quietdrop.actions import archive, rename
from quietdrop.config import Config
from quietdrop.db import connect, get_item, upsert_file


def test_upsert_and_get(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    con = connect(db)
    try:
        p = tmp_path / "in" / "a.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello", encoding="utf-8")

        st = p.stat()
        item_id = upsert_file(
            con,
            path=str(p),
            filename=p.name,
            size=int(st.st_size),
            mtime=float(st.st_mtime),
        )
        item = get_item(con, item_id)
        assert item is not None
        assert item.filename == "a.txt"
        assert item.status == "new"
    finally:
        con.close()


def test_archive_moves_and_dedupes_filename(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    src_dir = tmp_path / "drop"
    arc_dir = tmp_path / "archive"
    src_dir.mkdir()
    arc_dir.mkdir()

    cfg = Config(watched_folders=[src_dir], archive_folder=arc_dir)

    con = connect(db)
    try:
        # First file
        f1 = src_dir / "a.txt"
        f1.write_text("one", encoding="utf-8")
        st = f1.stat()
        id1 = upsert_file(con, path=str(f1), filename=f1.name, size=st.st_size, mtime=st.st_mtime)
        item1 = archive(con, cfg, id1)
        assert item1.status == "archived"
        assert Path(item1.path).exists()
        assert Path(item1.path).parent == arc_dir
        assert Path(item1.path).name == "a.txt"

        # Second file with same name should get a suffix
        f2 = src_dir / "a.txt"
        f2.write_text("two", encoding="utf-8")
        st2 = f2.stat()
        id2 = upsert_file(con, path=str(f2), filename=f2.name, size=st2.st_size, mtime=st2.st_mtime)
        item2 = archive(con, cfg, id2)
        assert item2.status == "archived"
        assert Path(item2.path).exists()
        assert Path(item2.path).parent == arc_dir
        assert Path(item2.path).name.startswith("a-")
        assert Path(item2.path).suffix == ".txt"
    finally:
        con.close()


def test_rename_moves_on_disk_and_updates_db(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    con = connect(db)
    try:
        p = tmp_path / "drop" / "x.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        st = p.stat()
        item_id = upsert_file(con, path=str(p), filename=p.name, size=st.st_size, mtime=st.st_mtime)

        item = rename(con, item_id, "y.txt")
        assert item.filename == "y.txt"
        assert Path(item.path).name == "y.txt"
        assert (tmp_path / "drop" / "y.txt").exists()
        assert not (tmp_path / "drop" / "x.txt").exists()
    finally:
        con.close()

