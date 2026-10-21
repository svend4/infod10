# Подготовка восстановления без исходного комплекта

Время отчёта: 2026-10-21T17:55:38.114012+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [прежний сценарий прерываний](restore-interrupt-report.md); его файлы
сохранены. Девять новых операций используют исторический R4 с 36 событиями и двумя архивами.
Действующая база остаётся R5 с 59 событиями. Новые credentials и полномочия не выдаются.

## Этапы

| Этап | Наблюдение |
|---|---|
| [historical-source-before-preparation](restore-prepare-stage-01-historical-source-before-preparation.json) | The preceding interruption scenario remains intact; nine fresh jobs use the same quarantined R4 bundle with two archives. |
| [plan-partial](restore-prepare-stage-02-plan-partial.json) | An incomplete immutable control record or missing ownership proof remains refused by all four ordinary commands; no manual repair is attempted. |
| [plan-durable](restore-prepare-stage-03-plan-durable.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [intent-durable](restore-prepare-stage-04-intent-durable.json) | An incomplete immutable control record or missing ownership proof remains refused by all four ordinary commands; no manual repair is attempted. |
| [owner-partial](restore-prepare-stage-05-owner-partial.json) | An incomplete immutable control record or missing ownership proof remains refused by all four ordinary commands; no manual repair is attempted. |
| [owner-durable](restore-prepare-stage-06-owner-durable.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [metadata-partial](restore-prepare-stage-07-metadata-partial.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [metadata-linked](restore-prepare-stage-08-metadata-linked.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [preparation-ready](restore-prepare-stage-09-preparation-ready.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [data-staging-preserved](restore-prepare-stage-10-data-staging-preserved.json) | Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation. |
| [historical-and-live-records-preserved](restore-prepare-stage-11-historical-and-live-records-preserved.json) | Three unproven operations remain refused; six completed targets preserve quarantine, captured history, both archives and the live R5 workspace. |

## Отказ и продолжение

Частичный неизменяемый plan, intent без owner и частичный owner остаются блокирующими.
Обычные prepare, status, step и resume-prepare отказывают, не заменяя записи и не усыновляя
назначение. Эти три операции не исправляются вручную. Store вызывается только для уже
защищённых intent целей и отказывает как incomplete; до первого intent такой probe не делается.

Шесть остальных операций имеют проверяемый plan и допустимую принадлежность назначения.
При временно недоступном source bundle readonly prepare/status сохраняют метаданные, а
resume-prepare завершает только три sidecars и пустой каталог archives. Результат остаётся
prepared с 0/3: ни DB, ни архивы, ни общий завершающий manifest ещё не установлены.

Повтор resume-prepare стабилен. В отдельном случае собственный data staging-префикс сохраняет
inode, размер и timestamps: metadata-only путь его не очищает. Содержимое staging не читается,
поэтому независимая побайтовая идентичность на основании этих метаданных не заявляется.

После возврата source bundle обычный step копирует три файла данных. Полный inspect, Store
и оба архива совпадают с R4. Completed resume-prepare возвращает прежний факт без изменений.
Новые записи и публикация блокируются карантином. Каталог источника возвращается в finally.

## Публичные основания

- [Девять случаев](restore-prepare-cases.json), [workers](restore-prepare-worker-outcomes.json), [CLI](restore-prepare-cli-outcomes.json), [отказы](restore-prepare-rejections.json).
- [Исходная сводка](restore-prepare-source-summary.json), [источник](restore-prepare-source-observations.json), [живая база](restore-prepare-live-observations.json), [прежнее восстановление](restore-prepare-previous-observations.json).
- [Новые назначения](restore-prepare-target-observations.json), [архивы](restore-prepare-archive-observations.json), [машинный отчёт](restore-prepare-report.json).

## Границы результата

Этапов: 11; случаев: 9; SIGKILL: 9; обычных CLI: 60; отказов: 26.
Родитель принимает только фиксированный ACK до 4096 байт за 60 секунд, проверяет живой
процесс, посылает именно ему SIGKILL и дожидается returncode -9. Worker ограничен alarm 90 секунд.
Это выбранные точки локального процесса; потеря питания, сбой ядра или хранилища, все
расписания и предельная нагрузка не моделировались. Независимая экспертиза, внешние LLM,
истинность содержания и внешний fencing не установлены.

Частные plan, owner, manifests, staging, checksums и origin остаются вне публикации.
Публикуются только Store/API-ответы и ограниченные булевы наблюдения топологии. Сравнение
исключает лишь верхний governance.observed_at; timestamps, expired и адресованные доказательства сохраняются.
