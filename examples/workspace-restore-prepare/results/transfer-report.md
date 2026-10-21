# Выполненный перенос в новую рабочую среду

Время отчёта: 2026-10-21T17:54:53.752730+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [сценарий остановки источника](fence-report.md). Его публичные основания сохранены
без перезаписи. Остановленный источник получает одноразовую привязку к новому назначению; старый
snapshot сохраняет карантин. Содержимое частных origin, manifest и credential denylist не читается демонстратором.

## Последовательность

| Этап | Наблюдение | Событий источника |
|---|---|---:|
| [fenced-source-and-old-copy](transfer-stage-01-fenced-source-and-old-copy.json) | The current source is stopped; its earlier snapshot remains quarantined. A new destination has not been created. | 63 |
| [old-secret-rejected-before-transfer](transfer-stage-02-old-secret-rejected-before-transfer.json) | A credential already issued in the source cannot become the new administrator's credential. | 63 |
| [fresh-active-baseline-and-fenced-history](transfer-stage-03-fresh-active-baseline-and-fenced-history.json) | The new workspace imports accepted content and exact dependency records at revision R, starts with only a fresh administrator, and preserves old history separately. | 64 |
| [completed-replay-and-single-target-binding](transfer-stage-04-completed-replay-and-single-target-binding.json) | The exact request returns its original fact without new events; another destination or operation conflicts. | 64 |
| [old-authority-and-active-copy-rejected](transfer-stage-05-old-authority-and-active-copy-rejected.json) | Old credentials and proposals do not acquire new authority; source and archive stay fenced, and a bare active database copied to another path is rejected. | 64 |
| [fresh-authenticated-authors-and-composition](transfer-stage-06-fresh-authenticated-authors-and-composition.json) | New role grants authorize three new author jobs and one composition. No old lease or opinion is reused. | 64 |
| [fresh-opinions-and-new-acceptance](transfer-stage-07-fresh-opinions-and-new-acceptance.json) | Four new scoped opinions and a new coordinator authorize a new acceptance in the destination, advancing R to R+1. | 64 |
| [updated-release-retains-provenance](transfer-stage-08-updated-release-retains-provenance.json) | The updated destination release loads with complete dependency records; unchanged imported artifacts still use their original baseline. | 64 |
| [new-workspace-backup-and-historical-restore](transfer-stage-09-new-workspace-backup-and-historical-restore.json) | An ordinary backup and restore preserve imported provenance and the new receipt; quarantine takes priority over the live destination's path binding. | 64 |
| [old-source-remains-stopped-and-history-preserved](transfer-stage-10-old-source-remains-stopped-and-history-preserved.json) | New work and its backup leave the old source, full history archive and older snapshot unchanged; the completed transfer remains one historical fact. | 64 |

## Новая база и сохранённая история

Назначение начинает работу с принятым содержанием и исходными пятью полями dependency records.
Импортная ревизия R равна текущей ревизии источника; она не сбрасывается в 1. Обычный publish и
load_release подтверждают совместимость этих оснований с существующим форматом выпуска.

Старые grants, tasks, proposals, decisions и receipts отсутствуют в активных таблицах назначения.
Текущие защитные режимы и политики включены заново с новым администратором. История полностью
сохранена в отдельной fenced SQLite-копии и остаётся доступной для чтения.

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

Этот обычный backup базы назначения не включает отдельный history.sqlite3 со всей предшествующей
историей. Полный архив переноса сохраняется отдельно и должен храниться как отдельный частный артефакт.

## Публичные основания

- [Transfer и status](transfer-results.json), [CLI](transfer-cli-outcomes.json), [отказы](transfer-rejections.json).
- [Источник](transfer-source-observations.json), [назначение](transfer-destination-observations.json), [история](transfer-history-observations.json).
- [Зависимости](transfer-dependencies.json), [проверенные выпуски](transfer-releases.json), [прежняя копия](transfer-old-snapshot-observations.json).
- [Новые grants](transfer-grants.json), [авторы](transfer-authors.json), [композиция](transfer-composition.json).
- [Пакеты](transfer-packets.json), [решения](transfer-decisions.json), [наблюдение](transfer-observation.json), [принятие](transfer-acceptance.json).
- [История content-политики](transfer-content-policy-history.json), [governance](transfer-governance-policy-history.json), [новый backup/restore](transfer-new-backup.json).
- [Машинный отчёт](transfer-report.json).

## Границы выполненного

Новых этапов: 10; новых CLI-процессов: 4; ожидаемых отказов: 11.
События источника: 63 → 64; новая ревизия назначения: 3.
Авторских заданий: 3; наблюдавшихся новых потоков: 3; свежих решений: 4.
Source reservation и path binding относятся к обновлённому локальному Store. Старый код и прямой
SQL владельца остаются вне этой границы. Внешний распределённый fence не установлен; существующие
эффекты публикации не отзываются и не выполняются повторно автоматически.

Сверка публичных наблюдений исключает только верхний governance.observed_at, вычисляемый при
каждом чтении. Raw timestamps, признаки expired и полные адресованные доказательства сохраняются.
Отдельный stale imported read/query случай, авария процесса и SQL-повреждение не воспроизводились.

Выпуски находятся в каталоге запуска и не включены в плоский набор этого отчёта. Частные базы,
планы, manifest, fingerprints и credentials не публикуются. Измерений предельной нагрузки нет.
