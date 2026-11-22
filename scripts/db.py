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
  search-priority NAME N  set search display priority (higher appears first)
  rename OLD NEW       rename a product (keeps id → groups/aliases survive)
  review               list products still needing group tagging
  choices              list valid food groups and fat-quality values

Schema:
  product(id, name UNIQUE, portion_raw, portion_g, serving_g, k, b, zh, u,
          fiber, prep_effort, estimated, review)
    portion_raw/portion_g — the portion the macros are given for (usually 100г);
    serving_g — optional grams of one real serving, logged when no weight is named
    search_priority — display order among equally suitable search matches;
                      never selects an ambiguous product automatically
  alias(product_id, text)
  food_group(id, name UNIQUE, kind, quota_week)
  product_group(product_id, group_id, weight)         -- m2m, composites
"""
import argparse
import math
import re
import sqlite3
import sys
from pathlib import Path

from paths import ROOT, DB_PATH
import catalog_match
from catalog_review import SCHEMA as REVIEW_SCHEMA, set_decision, product_warnings, pending_products, migrate_decisions

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
    ('обработка', 'потолок', '≤2'),
    ('добавки', 'потолок', 'фон'),
]

PORTION_RE = re.compile(r'([+-]?\d+(?:[.,]\d+)?)\s*(г|гр|g)\b', re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS product (
  id          INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  portion_raw TEXT,
  portion_g   REAL,
  serving_g   REAL,
  k           REAL, b REAL, zh REAL, u REAL, fiber REAL,
  prep_effort TEXT,
  estimated   INTEGER NOT NULL DEFAULT 0,
  review      INTEGER NOT NULL DEFAULT 1,
  fat_quality TEXT NOT NULL DEFAULT 'neutral',
  priority    INTEGER NOT NULL DEFAULT 0,
  avail       TEXT,
  search_priority INTEGER NOT NULL DEFAULT 0
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
        "ALTER TABLE product ADD COLUMN serving_g REAL",
        "ALTER TABLE product ADD COLUMN avail TEXT",
        "ALTER TABLE product ADD COLUMN search_priority INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            con.execute(ddl)
        except sqlite3.OperationalError:
            pass
    con.executescript(REVIEW_SCHEMA)
    # Retire the removed amino-acid feature in existing catalogs. Keep food
    # macros, aliases, groups and all other outstanding review decisions.
    with con:
        con.execute('BEGIN')
        migrate_decisions(con)
        con.execute('DROP TABLE IF EXISTS product_protein_profile')
        con.execute('DROP TABLE IF EXISTS protein_profile')
        if con.execute("SELECT 1 FROM product_review WHERE field='protein' LIMIT 1").fetchone():
            con.execute("DELETE FROM product_review WHERE field='protein'")
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
    existing = con.execute('SELECT id FROM product WHERE name=?', (args.name,)).fetchone()
    if not args.name.strip():
        sys.exit('add: нужно непустое имя')
    for key in ('k', 'b', 'zh', 'u', 'fiber'):
        value = getattr(args, key)
        if value is not None and (not math.isfinite(value) or value < 0):
            sys.exit(f'add: --{key} должен быть конечным неотрицательным числом')
    if not existing:
        missing = [f'--{key}' for key in ('port', 'k', 'b', 'zh', 'u')
                   if getattr(args, key) is None or getattr(args, key) == '']
        if missing:
            sys.exit('add: новый продукт не записан; нужны ' + ', '.join(missing) +
                     '. Справочник: scripts/db choices; правила: docs/agent/catalog.md')
    groups = parse_groups(con, args.group or [])
    if args.groups_estimate is not None and not args.group:
        sys.exit('add: --groups-estimate требует --group')
    if args.fat_estimate is not None and args.fat_quality is None:
        sys.exit('add: --fat-estimate требует --fat-quality')
    for key in ('no_groups', 'review_groups', 'fat_unknown', 'groups_estimate', 'fat_estimate'):
        if getattr(args, key) is not None and not getattr(args, key).strip():
            sys.exit(f'add: --{key.replace("_", "-")} требует непустую причину')
    portion_raw, portion_g = parse_portion(args.port)
    if not existing and not portion_raw:
        sys.exit('add: новому продукту нужна непустая --port')
    if portion_raw and re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', portion_raw):
        sys.exit('add: у --port нужны единицы, например 100г, 330мл или 1 шт')
    if portion_g is not None and (not math.isfinite(portion_g) or portion_g <= 0):
        sys.exit('add: --port должен задавать положительную порцию')
    serving_g = parse_float(re.sub(r'\s*(г|гр|g)\s*$', '', args.serving, flags=re.I)) \
        if args.serving else None
    if args.serving and (serving_g is None or not math.isfinite(serving_g) or serving_g <= 0):
        sys.exit(f'--serving: нужны граммы, получено «{args.serving}»')
    fat_per_serving = args.zh or 0
    if portion_g and serving_g:
        fat_per_serving *= serving_g / portion_g
    if (not existing and args.fat_quality is None and args.fat_unknown is None
            and (fat_per_serving >= 5 or (args.zh and args.zh * 9 >= args.k * 0.3))):
        args.fat_unknown = 'Источник жира не указан; нейтральный по умолчанию'
    if not existing and not (args.group or args.no_groups or args.review_groups):
        args.review_groups = 'Состав не указан; вклад в группы неизвестен'
    avail = parse_avail(args.avail) if args.avail else None
    # An update keeps every field the caller did not pass: `add "<name>" --alias x`
    # must not blank out the macros. None therefore means "leave alone", so a
    # value can be corrected but never cleared through this command.
    estimated = args.estimate
    cur = con.execute(
        """INSERT INTO product(name, portion_raw, portion_g, serving_g, k, b, zh, u,
                               fiber, prep_effort, estimated, fat_quality, priority,
                               avail, search_priority)
           VALUES (?,?,?,?,?,?,?,?,?,?,COALESCE(?,0),COALESCE(?,'neutral'),
                   COALESCE(?,0),?,COALESCE(?,0))
           ON CONFLICT(name) DO UPDATE SET
             portion_raw=COALESCE(excluded.portion_raw, product.portion_raw),
             portion_g=COALESCE(excluded.portion_g, product.portion_g),
             serving_g=COALESCE(excluded.serving_g, product.serving_g),
             k=COALESCE(excluded.k, product.k),
             b=COALESCE(excluded.b, product.b),
             zh=COALESCE(excluded.zh, product.zh),
             u=COALESCE(excluded.u, product.u),
             fiber=COALESCE(excluded.fiber, product.fiber),
             prep_effort=COALESCE(excluded.prep_effort, product.prep_effort),
             estimated=COALESCE(?, product.estimated),
             fat_quality=COALESCE(?, product.fat_quality),
             priority=COALESCE(?, product.priority),
             avail=COALESCE(?, product.avail),
             search_priority=COALESCE(?, product.search_priority)
           RETURNING id""",
        (args.name, portion_raw, portion_g, serving_g, args.k, args.b, args.zh, args.u,
         args.fiber, args.prep, estimated, args.fat_quality,
         args.priority, avail, args.search_priority, estimated, args.fat_quality,
         args.priority, avail, args.search_priority))
    pid = cur.fetchone()[0]
    if args.alias:
        for a in (x.strip() for x in args.alias.split(',')):
            if a:
                con.execute('INSERT OR IGNORE INTO alias(product_id, text) VALUES (?,?)',
                            (pid, a))
    if args.group or args.no_groups or args.review_groups:
        con.execute('DELETE FROM product_group WHERE product_id=?', (pid,))
        con.executemany('INSERT INTO product_group VALUES (?,?,?)',
                        [(pid, gid, weight) for gid, weight in groups])
        con.execute('UPDATE product SET review=0 WHERE id=?', (pid,))
        set_decision(con, pid, 'groups', args.no_groups or args.review_groups or args.groups_estimate,
                     exempt=bool(args.no_groups),
                     status='estimated' if args.groups_estimate else 'unknown')
    if args.fat_quality is not None or args.fat_unknown:
        if args.fat_unknown:
            con.execute("UPDATE product SET fat_quality='neutral' WHERE id=?", (pid,))
        set_decision(con, pid, 'fat', args.fat_unknown or args.fat_estimate,
                     status='estimated' if args.fat_estimate else 'unknown')
    con.commit()
    print(f'ok #{pid} {args.name}')
    tags = con.execute('''SELECT g.name,pg.weight FROM product_group pg
        JOIN food_group g ON g.id=pg.group_id WHERE pg.product_id=?''', (pid,)).fetchall()
    print('группы: ' + (', '.join(f'{name}={weight:g}' for name, weight in tags) or 'нет'))
    quality, = con.execute('SELECT fat_quality FROM product WHERE id=?', (pid,)).fetchone()
    print(f'качество жира: {quality}')
    for field, status, reason in con.execute(
            'SELECT field,status,reason FROM product_review WHERE product_id=?', (pid,)):
        print(f'{field}: {status} — {reason}')


def parse_groups(con, pairs):
    groups = []
    seen = set()
    for pair in pairs:
        name, sep, raw = pair.partition('=')
        row = con.execute('SELECT id FROM food_group WHERE name=?', (name.strip(),)).fetchone()
        if not row:
            sys.exit(f'группы: нет группы {name!r}; см. scripts/db choices')
        weight = parse_float(raw) if sep else 1.0
        if weight is None or not math.isfinite(weight) or weight <= 0:
            sys.exit('группы: вес должен быть конечным положительным числом')
        if weight > 1 and name.strip() not in ('рыба', 'птица'):
            sys.exit('группы: вес >1 допустим только для концентрированной рыбы/птицы')
        if row[0] in seen:
            sys.exit(f'группы: повтор группы {name!r}')
        seen.add(row[0])
        groups.append((row[0], weight))
    return groups


def searchable(con):
    """[(name, [name and aliases])] for catalog_match, straight from SQL."""
    strings = {}
    for name, alias in con.execute(
            """SELECT p.name, a.text FROM product p
               LEFT JOIN alias a ON a.product_id = p.id"""):
        bucket = strings.setdefault(name, [name])
        if alias:
            bucket.append(alias)
    return list(strings.items())


def search_priorities(con):
    return dict(con.execute('SELECT name, search_priority FROM product'))


def cmd_find(con, args):
    priorities = search_priorities(con)
    exact_name, exact_ties = catalog_match.exact_words(args.substr, searchable(con), priorities)
    if exact_ties:
        print('неоднозначно: ' + ', '.join(exact_ties))
        return
    if exact_name:
        rows = con.execute(
            """SELECT p.name, p.portion_raw, p.serving_g, p.k, p.b, p.zh, p.u, p.fiber,
                      p.fat_quality, p.avail, p.search_priority FROM product p WHERE p.name = ?""",
            (exact_name,)).fetchall()
    else:
        rows = []
    like = f'%{args.substr.lower()}%'
    if not rows:
        rows = con.execute(
            """SELECT DISTINCT p.name, p.portion_raw, p.serving_g, p.k, p.b, p.zh, p.u, p.fiber,
                      p.fat_quality, p.avail, p.search_priority
               FROM product p LEFT JOIN alias a ON a.product_id = p.id
               WHERE ulower(p.name) LIKE ? OR ulower(a.text) LIKE ?
               ORDER BY p.search_priority DESC, p.name""", (like, like)).fetchall()
    if not rows:
        # Substring missed — retry through the token-fuzzy fallback, which
        # survives typos and word order. Browsing lists every hit; picking
        # one is resolve_product's job, not this one's.
        names = [t[-1] for t in catalog_match.rank(args.substr, searchable(con), priorities)]
        if names:
            rows = con.execute(
                """SELECT p.name, p.portion_raw, p.serving_g, p.k, p.b, p.zh, p.u, p.fiber,
                          p.fat_quality, p.avail, p.search_priority FROM product p
                   WHERE p.name IN (%s)""" % ','.join('?' * len(names)),
                names).fetchall()
            rows.sort(key=lambda r: names.index(r[0]))
    if not rows:
        print('— ничего —')
        return
    for name, port, serving, k, b, zh, u, fib, fq, av, search_priority in rows:
        if serving:
            port = f'{port} · порция {serving:g}г'
        tag = '' if fq == 'neutral' else f' жир:{fq}'
        tag += f' · {av}' if av else ''
        if search_priority:
            tag += f' · приоритет поиска:{search_priority}'
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
    groups = parse_groups(con, args.pairs)
    for gid, weight in groups:
        con.execute(
            """INSERT INTO product_group(product_id, group_id, weight) VALUES (?,?,?)
               ON CONFLICT(product_id, group_id) DO UPDATE SET weight=excluded.weight""",
            (pid, gid, weight))
    con.execute('UPDATE product SET review = 0 WHERE id = ?', (pid,))
    # tag augments existing weights: it cannot make their estimates exact.
    if not con.execute("SELECT 1 FROM product_review WHERE product_id=? "
                       "AND field='groups' AND status='estimated'", (pid,)).fetchone():
        set_decision(con, pid, 'groups')
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


def cmd_search_priority(con, args):
    cur = con.execute('UPDATE product SET search_priority = ? WHERE name = ?',
                      (args.level, args.name))
    if cur.rowcount == 0:
        sys.exit(f'search-priority: продукт не найден: {args.name}')
    con.commit()
    print(f'{args.name}: приоритет поиска={args.level}')


def parse_avail(v):
    """always|stock|none|resto:<имя> -> stored value (None clears)."""
    if v == 'none':
        return None
    if v in ('always', 'stock') or (v.startswith('resto:') and len(v) > 6):
        return v
    sys.exit(f'avail: ожидается always|stock|none|resto:<имя>, получено {v!r}')


def resolve_product(con, name_q):
    """Exact name match, else unique substring over name+alias."""
    row = con.execute('SELECT id, name FROM product WHERE name = ?',
                      (name_q,)).fetchone()
    if row:
        return row
    priorities = search_priorities(con)
    name, ties = catalog_match.exact_words(name_q, searchable(con), priorities)
    if ties:
        sys.exit('неоднозначно: ' + ', '.join(ties))
    if name:
        return con.execute('SELECT id, name FROM product WHERE name = ?',
                           (name,)).fetchone()
    like = f'%{name_q.lower()}%'
    rows = con.execute(
        """SELECT DISTINCT p.id, p.name FROM product p
           LEFT JOIN alias a ON a.product_id = p.id
           WHERE ulower(p.name) LIKE ? OR ulower(a.text) LIKE ?""",
        (like, like)).fetchall()
    if len(rows) > 1:
        sys.exit('неоднозначно: ' + ', '.join(
            catalog_match.order_names((n for _, n in rows), priorities)))
    if not rows:
        name, ties = catalog_match.best(name_q, searchable(con), priorities)
        if ties:
            sys.exit('неоднозначно: ' + ', '.join(ties))
        if name is None:
            sys.exit(f'продукт не найден: {name_q}')
        return con.execute('SELECT id, name FROM product WHERE name = ?',
                           (name,)).fetchone()
    return rows[0]


def cmd_avail(con, args):
    pid, name = resolve_product(con, args.name)
    val = parse_avail(args.value)
    con.execute('UPDATE product SET avail = ? WHERE id = ?', (val, pid))
    con.commit()
    print(f'{name}: avail={val or "—"}')


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
    pending = pending_products(con)
    rows = product_warnings(con)
    if args.all:
        rows += [f'{name}: {field} / {status} — {reason}'
                 for name, field, status, reason in con.execute(
                     "SELECT p.name,r.field,r.status,r.reason FROM product_review r "
                     "JOIN product p ON p.id=r.product_id WHERE r.status!='pending' ORDER BY p.name,r.field")]
    total = con.execute('SELECT COUNT(*) FROM product').fetchone()[0]
    print(f'требуют проверки: {len(pending)}/{total}')
    for line in rows[:args.limit]:
        print(line)
    if len(rows) > args.limit:
        print(f'  … ещё {len(rows) - args.limit}')


def cmd_choices(con, args):
    print('Группы (--group ГРУППА=ВЕС; повторять для нескольких):')
    print(', '.join(row[0] for row in con.execute('SELECT name FROM food_group ORDER BY id')))
    print('Качество жира (--fat-quality): good, neutral, bad')
    print('Правила: docs/agent/catalog.md')


def main(argv=None):
    p = argparse.ArgumentParser(prog='db')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init')

    m = sub.add_parser('migrate')
    m.add_argument('source', nargs='?', default=str(ROOT / 'products.md'))

    a = sub.add_parser('add')
    a.add_argument('name')
    a.add_argument('--port', default='')
    a.add_argument('--serving', help='grams of one serving, logged when no weight is named')
    a.add_argument('--k', type=float)
    a.add_argument('--b', type=float)
    a.add_argument('--zh', type=float)
    a.add_argument('--u', type=float)
    a.add_argument('--fiber', type=float)
    a.add_argument('--alias', default='')
    a.add_argument('--prep', choices=['low', 'med', 'high'])
    estimates = a.add_mutually_exclusive_group()
    estimates.add_argument('--estimate', action='store_const', const=1, default=None)
    estimates.add_argument('--no-estimate', dest='estimate', action='store_const', const=0,
                           help='КБЖУ из карточки/от пользователя, а не оценка агента')
    a.add_argument('--groups-estimate', help='основание приблизительных весов --group; не меняет КБЖУ')
    a.add_argument('--fat-estimate', help='основание приблизительной категории --fat-quality')
    fat = a.add_mutually_exclusive_group()
    fat.add_argument('--fat-quality', dest='fat_quality', choices=['good', 'neutral', 'bad'])
    fat.add_argument('--fat-unknown', help='причина неизвестного качества жира')
    groups = a.add_mutually_exclusive_group()
    groups.add_argument('--group', action='append', help='группа=вес; повторять; заменяет весь набор групп')
    groups.add_argument('--no-groups', help='почему продукт не относится ни к одной группе')
    groups.add_argument('--groups-unknown', '--review-groups', dest='review_groups',
                        help='состав неизвестен; завершённое решение без предупреждений')
    a.add_argument('--priority', type=int,
                   help='planner priority (0 default, <0 demote, >0 prefer)')
    a.add_argument('--search-priority', type=int,
                   help='search display priority (0 default, higher appears first; no automatic selection)')
    a.add_argument('--avail', help='always|stock|none|resto:<имя>')

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

    spr = sub.add_parser('search-priority')
    spr.add_argument('name', help='exact catalog name')
    spr.add_argument('level', type=int, help='0 default; higher appears first')

    av = sub.add_parser('avail')
    av.add_argument('name')
    av.add_argument('value', help='always|stock|none|resto:<имя>')

    rn = sub.add_parser('rename')
    rn.add_argument('old')
    rn.add_argument('new')
    rn.add_argument('--keep-alias', action='store_true',
                    help='keep the old name as a searchable alias')

    r = sub.add_parser('review')
    r.add_argument('--limit', type=int, default=40)
    r.add_argument('--all', action='store_true', help='также показать принятые допущения и их основания')
    sub.add_parser('choices')

    args = p.parse_args(argv)
    con = connect()
    try:
        with con:
            {'init': cmd_init, 'migrate': cmd_migrate, 'add': cmd_add, 'find': cmd_find,
             'q': cmd_q, 'tag': cmd_tag, 'priority': cmd_priority,
             'search-priority': cmd_search_priority, 'avail': cmd_avail,
             'rename': cmd_rename, 'review': cmd_review, 'choices': cmd_choices}[args.cmd](con, args)
    except sqlite3.Error as error:
        sys.exit(f'{args.cmd}: изменения не сохранены: {error}')
    finally:
        con.close()


if __name__ == '__main__':
    main()
