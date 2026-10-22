# Выполненный повторный перенос с цепочкой архивов

Время отчёта: 2026-10-22T15:46:28.917427+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [первый перенос](transfer-report.md), включая новое принятие в первой рабочей базе.
Его публичные основания сохранены без перезаписи. Первая рабочая база останавливается текущим
администратором и становится источником второго переноса. Частные origin, manifest, планы и denylist не читаются демонстратором.

## Последовательность

| Этап | Наблюдение | Событий источника |
|---|---|---:|
| [first-imported-workspace-is-active](chain-stage-01-first-imported-workspace-is-active.json) | The previously imported workspace has fresh accepted work at R=3 and one preserved archived hop. | 36 |
| [first-workspace-fenced-by-current-administrator](chain-stage-02-first-workspace-fenced-by-current-administrator.json) | A new private recovery assessment and ordinary seal stop the first workspace before its second transfer. | 37 |
| [fenced-source-and-old-copy](chain-stage-03-fenced-source-and-old-copy.json) | The current source is stopped; its earlier snapshot remains quarantined. A new destination has not been created. | 37 |
| [old-secret-rejected-before-transfer](chain-stage-04-old-secret-rejected-before-transfer.json) | A credential already issued in the source cannot become the new administrator's credential. | 37 |
| [fresh-active-baseline-and-fenced-history](chain-stage-05-fresh-active-baseline-and-fenced-history.json) | The new workspace imports accepted content and exact dependency records at revision R, starts with only a fresh administrator, and preserves old history separately. | 38 |
| [completed-replay-and-single-target-binding](chain-stage-06-completed-replay-and-single-target-binding.json) | The exact request returns its original fact without new events; another destination or operation conflicts. | 38 |
| [old-authority-and-active-copy-rejected](chain-stage-07-old-authority-and-active-copy-rejected.json) | Old credentials and proposals do not acquire new authority; source and archive stay fenced, and a bare active database copied to another path is rejected. | 38 |
| [fresh-authenticated-authors-and-composition](chain-stage-08-fresh-authenticated-authors-and-composition.json) | New role grants authorize three new author jobs and one composition. No old lease or opinion is reused. | 38 |
| [fresh-opinions-and-new-acceptance](chain-stage-09-fresh-opinions-and-new-acceptance.json) | Four new scoped opinions and a new coordinator authorize a new acceptance in the destination, advancing R to R+1. | 38 |
| [updated-release-retains-provenance](chain-stage-10-updated-release-retains-provenance.json) | The updated destination release loads with complete dependency records; unchanged imported artifacts still use their original baseline. | 38 |
| [self-contained-with-previous-paths-unavailable](chain-stage-11-self-contained-with-previous-paths-unavailable.json) | Both previous workspace paths are temporarily renamed; a fresh next-workspace Store, lineage command and release loader still succeed, and the old names are restored in finally. | 38 |
| [new-workspace-backup-and-historical-restore](chain-stage-12-new-workspace-backup-and-historical-restore.json) | An ordinary backup and restore preserve imported provenance and the new receipt; quarantine takes priority over the live destination's path binding. | 38 |
| [old-source-remains-stopped-and-history-preserved](chain-stage-13-old-source-remains-stopped-and-history-preserved.json) | New work and its backup leave the old source, full history archive and older snapshot unchanged; the completed transfer remains one historical fact. | 38 |

## Новая база и сохранённая история

Назначение начинает работу с принятым содержанием и исходными пятью полями dependency records.
Импортная ревизия R равна текущей ревизии источника; она не сбрасывается в 1. Обычный publish и
load_release подтверждают совместимость этих оснований с существующим форматом выпуска.

Старые grants, tasks, proposals, decisions и receipts отсутствуют в активных таблицах назначения.
Текущие защитные режимы и политики включены заново с новым администратором. История полностью
сохранена в двух отдельных fenced SQLite-архивах, расположенных в новом частном каталоге.
Команда lineage проверяет всю цепочку и возвращает только безопасные квитанции двух переносов.

## Самодостаточность цепочки

Каталоги исходной и первой рабочей базы временно переименованы в соседние частные имена.
При недоступных прежних путях свежий Store второй базы, lineage и загрузчик её выпуска
сохраняют тот же результат. Оба имени восстановлены в finally; прежние наблюдения совпали.
Это воспроизведённая недоступность файловых путей, без моделирования аварии процесса.

## Новые полномочия и новая работа

Старые credentials не получают полномочия в назначении; повторная выдача старого секрета также
отклоняется. Новые author/integrator/reviewer/coordinator grants разрешают три авторских задания,
композицию документа и Python-конфигурации, четыре свежих решения и новое принятие R → R+1.
Предлагаемый Python не исполняется; программируемые роли не являются независимой человеческой экспертизой.

## Повторы, копии и последующее восстановление

Точный повтор transfer возвращает первоначальную квитанцию без нового события. Другая операция
или назначение конфликтуют. Копирование активной DB в другой частный путь через SQLite Backup API
не активирует вторую среду: Store отклоняет её из-за привязки origin к первоначальному пути.

После нового принятия выполняются обычный backup назначения и restore. Историческая копия
сохраняет полный snapshot, зависимость импортированных неизменённых файлов и новую квитанцию,
но остаётся в карантине. Quarantine имеет приоритет над привязкой активного пути.

Этот обычный backup базы назначения не включает отдельную полную цепочку архивов.
Все файлы истории переноса сохраняются отдельно как частные артефакты.

## Публичные основания

- [Transfer и status](chain-results.json), [CLI](chain-cli-outcomes.json), [отказы](chain-rejections.json).
- [Источник](chain-source-observations.json), [назначение](chain-destination-observations.json), [история](chain-history-observations.json).
- [Зависимости](chain-dependencies.json), [проверенные выпуски](chain-releases.json), [прежняя копия](chain-old-snapshot-observations.json).
- [Новые grants](chain-grants.json), [авторы](chain-authors.json), [композиция](chain-composition.json).
- [Пакеты](chain-packets.json), [решения](chain-decisions.json), [наблюдение](chain-observation.json), [принятие](chain-acceptance.json).
- [История content-политики](chain-content-policy-history.json), [governance](chain-governance-policy-history.json), [новый backup/restore](chain-new-backup.json).
- [Подготовка и остановка](chain-preparation.json), [квитанции цепочки](chain-lineages.json), [недоступность прежних путей](chain-detached-path-observations.json).
- [Старший архив](chain-ancestor-observations.json), [исходная база](chain-original-source-observations.json).
- [Машинный отчёт](chain-report.json).

## Границы выполненного

Новых этапов: 13; новых CLI-процессов: 11; ожидаемых отказов: 15.
События источника: 37 → 38; новая ревизия назначения: 4.
Архивов: 2; событий в старшем: 64; в непосредственном: 38.
Авторских заданий: 3; наблюдавшихся новых потоков: 3; свежих решений: 4.
Source reservation и path binding относятся к обновлённому локальному Store. Старый код и прямой
SQL владельца остаются вне этой границы. Внешний распределённый fence не установлен; существующие
эффекты публикации не отзываются и не выполняются повторно автоматически.

Сверка публичных наблюдений исключает только верхний governance.observed_at, вычисляемый при
каждом чтении. Raw timestamps, признаки expired и полные адресованные доказательства сохраняются.
Отдельный stale imported read/query случай, авария процесса и SQL-повреждение не воспроизводились.

Выпуски находятся в каталоге запуска и не включены в плоский набор этого отчёта. Частные базы,
планы, manifest, fingerprints и credentials не публикуются. Измерений предельной нагрузки нет.
