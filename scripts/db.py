#!/usr/bin/env python3
"""Catalog database — single source of truth for products (diet.db).

`diet.db` (SQLite) replaces products.md. The catalog is never hand-edited as
markdown anymore; all reads/writes go through this wrapper (see the `db` shim).

Subcommands:
  init                 create schema + seed Mediterranean groups (idempotent)
  migrate [products.md]  import the legacy markdown catalog into diet.db
  add NAME …           insert/update a product
  find SUBSTR          grep products by name or alias
  q "SQL"              run a raw read-only query
  tag NAME g=w …       set a product's food-group weights (clears review)
  priority NAME N      set a product's planner priority (0 default, <0 demote)
  rename OLD NEW       rename a product (keeps id → groups/aliases survive)
  review               list products still needing group tagging

Schema:
  product(id, name UNIQUE, portion_raw, portion_g, k, b, zh, u, fiber,
          prep_effort, estimated, review)
  alias(product_id, text)
  food_group(id, name UNIQUE, kind, quota_week)
  product_group(product_id, group_id, weight)         -- m2m, composites
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

from paths import ROOT, DB_PATH

# Mediterranean group model — STRATEGY.md §6. kind: пол | умеренно | потолок.
SEED_GROUPS = [
    ('овощи', 'пол', '≥2/день'),
    ('фрукты', 'пол', '1–2/день'),
    ('злаки', 'пол', 'ежедневно'),
    ('бобовые', 'пол', '≥3'),
    ('рыба', 'пол', '≥3'),
    ('орехи', 'пол', 'горсть/день'),
    ('молочка', 'пол', 'ежедневно'),
    ('птица', 'умеренно', '≤3'),
    ('яйца', 'умеренно', '2–4'),
    ('оливковое', 'умеренно', 'дефолт-жир'),
    ('красное_мясо', 'потолок', '≤1'),
    ('сладкое', 'потолок', '≤2'),
    ('добавки', 'потолок', 'фон'),
]

PORTION_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(г|гр|g)\b', re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS product (
  id          INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  portion_raw TEXT,
  portion_g   REAL,
  k           REAL, b REAL, zh REAL, u REAL, fiber REAL,
  prep_effort TEXT,
  estimated   INTEGER NOT NULL DEFAULT 0,
  review      INTEGER NOT NULL DEFAULT 1,
  fat_quality TEXT NOT NULL DEFAULT 'neutral',
  priority    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alias (
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  text       TEXT NOT NULL,
  PRIMARY KEY (product_id, text)
);
CREATE TABLE IF NOT EXISTS food_group (
  id         INTEGER PRIMARY KEY,
  name       TEXT UNIQUE NOT NULL,
  kind       TEXT NOT NULL,
  quota_week TEXT
);
CREATE TABLE IF NOT EXISTS product_group (
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  group_id   INTEGER NOT NULL REFERENCES food_group(id) ON DELETE CASCADE,
  weight     REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (product_id, group_id)
);
"""


def parse_float(s):
    if s is None:
        return None
    try:
        return float(str(s).strip().replace(',', '.'))
    except ValueError:
        return None


def parse_portion(raw):
    """'100г' -> (raw, 100.0); '1 порция' -> (raw, None)."""
    raw = (raw or '').strip()
    m = PORTION_RE.search(raw)
    return raw or None, (parse_float(m.group(1)) if m else None)


def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute('PRAGMA foreign_keys = ON')
    # SQLite LIKE/LOWER fold case only for ASCII; register a Unicode-aware lower
    # so Cyrillic search ('скрембл' vs 'Скрембл') is case-insensitive.
    con.create_function('ulower', 1, lambda s: s.lower() if s else s)
    # Idempotent column adds for catalogs predating these fields.
    for ddl in (
        "ALTER TABLE product ADD COLUMN fat_quality TEXT NOT NULL DEFAULT 'neutral'",
        "ALTER TABLE product ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            con.execute(ddl)
        except sqlite3.OperationalError:
            pass
    con.commit()
    return con


def cmd_init(con, args):
    con.executescript(SCHEMA)
    con.executemany(
        'INSERT OR IGNORE INTO food_group(name, kind, quota_week) VALUES (?,?,?)',
        SEED_GROUPS)
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM food_group').fetchone()[0]
    print(f'schema ok | food_group: {n}')


def cmd_migrate(con, args):
    cmd_init(con, args)
    src = Path(args.source)
    rows = 0
    for line in src.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        if not (s.startswith('|') and s.endswith('|')):
            continue
        cells = [c.strip() for c in s[1:-1].split('|')]
        if len(cells) < 8 or cells[0] in ('Продукт',) or set(cells[0]) <= set(':-'):
            continue
        name = cells[0]
        if not name:
            continue
        portion_raw, portion_g = parse_portion(cells[2])
        cur = con.execute(
            """INSERT INTO product(name, portion_raw, portion_g, k, b, zh, u, fiber)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 portion_raw=excluded.portion_raw, portion_g=excluded.portion_g,
                 k=excluded.k, b=excluded.b, zh=excluded.zh, u=excluded.u,
                 fiber=excluded.fiber
               RETURNING id""",
            (name, portion_raw, portion_g, parse_float(cells[3]),
             parse_float(cells[4]), parse_float(cells[5]),
             parse_float(cells[6]), parse_float(cells[7])))
        pid = cur.fetchone()[0]
        for a in (x.strip() for x in cells[1].split(',')):
            if a and a.lower() != name.lower():
                con.execute('INSERT OR IGNORE INTO alias(product_id, text) VALUES (?,?)',
                            (pid, a))
        rows += 1
    con.commit()
    total = con.execute('SELECT COUNT(*) FROM product').fetchone()[0]
    print(f'migrated {rows} rows | product total: {total}')


def cmd_add(con, args):
    portion_raw, portion_g = parse_portion(args.port)
    cur = con.execute(
        """INSERT INTO product(name, portion_raw, portion_g, k, b, zh, u, fiber,
                               prep_effort, estimated, fat_quality, priority)
           VALUES (?,?,?,?,?,?,?,?,?,?,COALESCE(?,'neutral'),COALESCE(?,0))
           ON CONFLICT(name) DO UPDATE SET
             portion_raw=excluded.portion_raw, portion_g=excluded.portion_g,
             k=excluded.k, b=excluded.b, zh=excluded.zh, u=excluded.u,
             fiber=excluded.fiber, prep_effort=excluded.prep_effort,
             estimated=excluded.estimated,
             fat_quality=COALESCE(?, product.fat_quality),
             priority=COALESCE(?, product.priority)
           RETURNING id""",
        (args.name, portion_raw, portion_g, args.k, args.b, args.zh, args.u,
         args.fiber, args.prep, int(args.estimate), args.fat_quality,
         args.priority, args.fat_quality, args.priority))
    pid = cur.fetchone()[0]
    if args.alias:
        for a in (x.strip() for x in args.alias.split(',')):
            if a:
                con.execute('INSERT OR IGNORE INTO alias(product_id, text) VALUES (?,?)',
                            (pid, a))
    con.commit()
    print(f'ok #{pid} {args.name}')


def cmd_find(con, args):
    like = f'%{args.substr.lower()}%'
    rows = con.execute(
        """SELECT DISTINCT p.name, p.portion_raw, p.k, p.b, p.zh, p.u, p.fiber,
                  p.fat_quality
           FROM product p LEFT JOIN alias a ON a.product_id = p.id
           WHERE ulower(p.name) LIKE ? OR ulower(a.text) LIKE ?
           ORDER BY p.name""", (like, like)).fetchall()
    if not rows:
        print('— ничего —')
        return
    for name, port, k, b, zh, u, fib, fq in rows:
        tag = '' if fq == 'neutral' else f' жир:{fq}'
        print(f'{name} [{port}] К{k} Б{b} Ж{zh} У{u} Клет{fib}{tag}')


def cmd_q(con, args):
    if not re.match(r'\s*select\b', args.sql, re.IGNORECASE):
        sys.exit('q: только SELECT')
    cur = con.execute(args.sql)
    cols = [d[0] for d in cur.description]
    print('\t'.join(cols))
    for row in cur.fetchall():
        print('\t'.join('' if v is None else str(v) for v in row))


def cmd_tag(con, args):
    row = con.execute('SELECT id FROM product WHERE name = ?', (args.name,)).fetchone()
    if not row:
        sys.exit(f'tag: продукт не найден: {args.name}')
    pid = row[0]
    for pair in args.pairs:
        grp, _, w = pair.partition('=')
        g = con.execute('SELECT id FROM food_group WHERE name = ?', (grp.strip(),)).fetchone()
        if not g:
            sys.exit(f'tag: нет группы {grp!r} (см. food_group)')
        con.execute(
            """INSERT INTO product_group(product_id, group_id, weight) VALUES (?,?,?)
               ON CONFLICT(product_id, group_id) DO UPDATE SET weight=excluded.weight""",
            (pid, g[0], parse_float(w) if w else 1.0))
    con.execute('UPDATE product SET review = 0 WHERE id = ?', (pid,))
    con.commit()
    tags = con.execute(
        """SELECT mg.name, pg.weight FROM product_group pg
           JOIN food_group mg ON mg.id = pg.group_id WHERE pg.product_id = ?""",
        (pid,)).fetchall()
    print(f'{args.name}: ' + ', '.join(f'{n}={w:g}' for n, w in tags))


def cmd_priority(con, args):
    cur = con.execute('UPDATE product SET priority = ? WHERE name = ?',
                      (args.level, args.name))
    if cur.rowcount == 0:
        sys.exit(f'priority: продукт не найден: {args.name}')
    con.commit()
    print(f'{args.name}: priority={args.level}')


def cmd_rename(con, args):
    if con.execute('SELECT 1 FROM product WHERE name = ?', (args.new,)).fetchone():
        sys.exit(f'rename: имя уже занято: {args.new}')
    cur = con.execute('UPDATE product SET name = ? WHERE name = ?',
                      (args.new, args.old))
    if cur.rowcount == 0:
        sys.exit(f'rename: продукт не найден: {args.old}')
    if args.keep_alias:
        pid = con.execute('SELECT id FROM product WHERE name = ?',
                          (args.new,)).fetchone()[0]
        con.execute('INSERT OR IGNORE INTO alias(product_id, text) VALUES (?,?)',
                    (pid, args.old))
    con.commit()
    print(f'{args.old} → {args.new}')


def cmd_review(con, args):
    rows = con.execute(
        'SELECT name FROM product WHERE review = 1 ORDER BY name').fetchall()
    total = con.execute('SELECT COUNT(*) FROM product').fetchone()[0]
    print(f'без разметки групп: {len(rows)}/{total}')
    for (name,) in rows[:args.limit]:
        print(f'  {name}')
    if len(rows) > args.limit:
        print(f'  … ещё {len(rows) - args.limit}')


def main():
    p = argparse.ArgumentParser(prog='db')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init')

    m = sub.add_parser('migrate')
    m.add_argument('source', nargs='?', default=str(ROOT / 'products.md'))

    a = sub.add_parser('add')
    a.add_argument('name')
    a.add_argument('--port', default='')
    a.add_argument('--k', type=float)
    a.add_argument('--b', type=float)
    a.add_argument('--zh', type=float)
    a.add_argument('--u', type=float)
    a.add_argument('--fiber', type=float)
    a.add_argument('--alias', default='')
    a.add_argument('--prep', choices=['low', 'med', 'high'])
    a.add_argument('--estimate', action='store_true')
    a.add_argument('--fat-quality', dest='fat_quality',
                   choices=['good', 'neutral', 'bad'])
    a.add_argument('--priority', type=int,
                   help='planner priority (0 default, <0 demote, >0 prefer)')

    f = sub.add_parser('find')
    f.add_argument('substr')

    qp = sub.add_parser('q')
    qp.add_argument('sql')

    t = sub.add_parser('tag')
    t.add_argument('name')
    t.add_argument('pairs', nargs='+', help='группа=вес …')

    pr = sub.add_parser('priority')
    pr.add_argument('name')
    pr.add_argument('level', type=int)

    rn = sub.add_parser('rename')
    rn.add_argument('old')
    rn.add_argument('new')
    rn.add_argument('--keep-alias', action='store_true',
                    help='keep the old name as a searchable alias')

    r = sub.add_parser('review')
    r.add_argument('--limit', type=int, default=40)

    args = p.parse_args()
    con = connect()
    {'init': cmd_init, 'migrate': cmd_migrate, 'add': cmd_add, 'find': cmd_find,
     'q': cmd_q, 'tag': cmd_tag, 'priority': cmd_priority, 'rename': cmd_rename,
     'review': cmd_review}[args.cmd](con, args)
    con.close()


if __name__ == '__main__':
    main()
