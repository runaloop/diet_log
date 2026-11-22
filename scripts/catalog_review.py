"""Keep annotation uncertainty separate from nutritional-value estimates."""
import sqlite3

from paths import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_review (
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK (field IN ('groups', 'fat')),
  status TEXT NOT NULL CHECK (status IN ('pending', 'exempt', 'estimated', 'unknown')),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  PRIMARY KEY (product_id, field),
  CHECK (status != 'exempt' OR field = 'groups')
);
"""


def migrate_decisions(con):
    """Old recipe questions are accepted unknowns, not a permanent user task."""
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='product_review'").fetchone()[0]
    if "'unknown'" in sql:
        return
    con.execute('ALTER TABLE product_review RENAME TO old_product_review')
    con.execute(SCHEMA.strip())
    con.execute("""INSERT INTO product_review
        SELECT product_id,field,CASE WHEN status='pending' THEN 'unknown' ELSE status END,reason
        FROM old_product_review WHERE field IN ('groups','fat')""")
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='product'").fetchone():
        con.execute("""UPDATE product SET review=0 WHERE id IN
            (SELECT product_id FROM product_review WHERE field='groups' AND status='unknown')""")
    con.execute('DROP TABLE old_product_review')


def set_decision(con, pid, field, reason=None, *, exempt=False, status='unknown'):
    con.execute('DELETE FROM product_review WHERE product_id=? AND field=?', (pid, field))
    if reason:
        con.execute('INSERT INTO product_review VALUES (?,?,?,?)',
                    (pid, field, 'exempt' if exempt else status, reason.strip()))


def pending_products(con):
    """Legacy review=1 remains visible even before reasons were introduced."""
    pending = {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='product_review'").fetchone():
        for pid, field, reason in con.execute(
                "SELECT product_id,field,reason FROM product_review "
                "WHERE status='pending' AND field IN ('groups', 'fat')"):
            pending.setdefault(pid, {})[field] = reason
    for pid, in con.execute('SELECT id FROM product WHERE review=1'):
        pending.setdefault(pid, {}).setdefault('groups', 'группы ещё не назначены')
    return pending


def product_warnings(con, pids=None):
    descriptions = {
        'groups': 'разметка групп не завершена; итог по группам может быть неполным',
        'fat': 'качество жира неизвестно; пока учитывается как нейтральное',
    }
    pending = pending_products(con)
    out = []
    for pid, name in con.execute('SELECT id,name FROM product ORDER BY name'):
        if pids is not None and pid not in pids:
            continue
        for field, reason in pending.get(pid, {}).items():
            out.append(f'⚠ {name}: {descriptions[field]} ({reason}).')
    return out


def warning_lines(names, path=None):
    """Exact names/aliases only, deduplicated; safe for old catalogs too."""
    path = path or DB_PATH
    if not names or not path.exists():
        return []
    names = {name.casefold() for name in names}
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        pids = {pid for pid, name in con.execute('SELECT id,name FROM product')
                if name.casefold() in names}
        pids.update(pid for pid, name in con.execute('SELECT product_id,text FROM alias')
                    if name.casefold() in names)
        return product_warnings(con, pids)
    finally:
        con.close()
