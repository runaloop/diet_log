# TODO

## Сделано / снято
- [x] База продуктов вынесена из md в SQLite (`data/diet.db`, STRATEGY.md §11, фаза 0).
- [x] Итоги дня / недели / месяца — `scripts/summary.py day|week|month|weektrend`.
- [x] Открывать `today.md` при открытии папки (`.nvim.lua`).
- ~~Миграция на excel~~ — снято решением по SQLite (STRATEGY.md §11).

## Скрипты — баги и дрейф (найдено при ревизии 2026-09-21)
- [ ] `db init` создаёт базу без колонки `avail`, а `db add` / `db find` её требуют (`scripts/db.py:52–82, 108–115, 172, 201`); живая база работает только потому, что колонку добавили вручную — добавить `avail` в `SCHEMA`/`connect()`.
- [ ] Дрейф групп: `db.py:45–46` сеет `сладкое` и `оливковое`, `summary.GROUP_QUOTA` (`scripts/summary.py:25–31`) знает `обработка` и не знает `оливковое`; `seed_med_tags.py:277` падает с `KeyError: 'сладкое'`; `оливковое` без квоты молча выпадает из отчётов по группам.
- [ ] `plan_ration.py` берёт режим из двух источников: `config/cycle.md` для слоя углей (`:732`) и H1 дневника для окна дефицита (`parse_plan`, `:94–96`); при расхождении бюджет и угли считаются в разных режимах — оставить один источник (`global_mode()`).
- [ ] `ration_totals.num()` (`scripts/ration_totals.py:33`) без try/except — нечисловая ячейка роняет скрипт.
- [ ] `summary.py day --любой-флаг` падает: `:851` читает `argv[1]` без фильтра флагов.
- [ ] Мёртвый код: `load_goals()['carbs']` (`summary.py:73`), `load_cycle()` anchor/cycle_len/maintenance_idx (`:83, :93`), `status(invert=True)` (`:270`), `paths.TRAINING_TYPES` (`scripts/paths.py:22`).
- [ ] Квота «добавки»: STRATEGY.md §15 фиксирует решение **7**, в коде `GROUP_QUOTA` стоит `20` — решить, что верно, и синхронизировать.

## Скрипты — следующие шаги
- [ ] `scripts/new_day.py`: «новый день» одним вызовом — `pills.py sync` → weektrend `--no-groups` в файл → `recalc_plan.py --write` → `ln -sf today.md` → `pills.py build` → `pills.py sync` (Garmin-шаги остаются агенту: там нужны решения).
- [ ] `log.py`: «как вчера» / «завтрак как вчера» — флаг копирования строк из другого дневника, чтобы агент не собирал их руками.

## Инструкция и доки
- [ ] `docs/CRINGE.md` и критик-контур STRATEGY.md §12/§16 не подключены к `AGENTS.md` — решить, нужен ли контур, и либо подключить (`docs/agent/ration.md`), либо снять из спека.
- [ ] `.claude/settings.json` проекта: `"effortLevel": "low"` перекрывает пользовательский `/effort` (project > user) — оставить осознанно или убрать.
- [ ] После сокращения `AGENTS.md` (2026-09-21, тег `agents-md-80kb` = версия до): замерить `/context` до/после и прогнать сценарии из плана на новой сессии; если качество упало — бисектить по коммитам «AGENTS.md trim, step 1/2/3».
