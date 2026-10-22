# Восстановление после реального SIGKILL на фиксированных границах

Время отчёта: 2026-10-22T16:34:58.003461+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий файлового восстановления](restore-job-report.md).
Его публичные файлы сохранены. Пять независимых операций восстанавливают тот же исторический
комплект R4 с двумя архивами; действующая база остаётся R5. Новые полномочия не выдаются.

## Наблюдавшиеся этапы

| Этап | Наблюдение |
|---|---|
| [historical-source-before-interruption](restore-interrupt-stage-01-historical-source-before-interruption.json) | The unchanged file-sized restore scenario supplies a quarantined R4 snapshot and two archives; the live workspace remains R5. |
| [staging-partial](restore-interrupt-stage-02-staging-partial.json) | A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation. |
| [staging-synced](restore-interrupt-stage-03-staging-synced.json) | A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation. |
| [target-linked](restore-interrupt-stage-04-target-linked.json) | A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation. |
| [data-ready](restore-interrupt-stage-05-data-ready.json) | A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation. |
| [completed](restore-interrupt-stage-06-completed.json) | A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation. |
| [historical-and-live-state-preserved](restore-interrupt-stage-07-historical-and-live-state-preserved.json) | All five recovered bundles remain quarantined; the captured source, both archives, live R5 workspace and preceding restore retain their public records. |

## Что было прервано

1. Непустой собственный staging-префикс после flush и fsync, до завершения файла.
2. Полный проверенный staging после fsync, до установки ссылки назначения.
3. Собственная пара hardlink после link и fsync родителя назначения, до удаления staging.
4. Все три файла данных готовы; общий manifest ещё отсутствует.
5. Завершение проверено; API ещё не вернул бизнес-ответ.

Доверенный отдельный worker выдаёт только фиксированное подтверждение границы. Родитель
проверяет живой процесс, отправляет именно ему SIGKILL и дожидается returncode -9. Только
после reap обычный CLI status читает сохранённый прогресс: 0, 0, 1, 3, 3. Status не меняет
наблюдавшуюся staging-топологию; обычный step чинит собственную пару и завершает комплект.

Для ready и completed source bundle временно недоступен. Status, step, полный inspect, Store
и оба архива работают из назначения. Каталог источника возвращён в finally. Во всех случаях
сохраняются job ID, operation ID, время, workspace ID, R4, 36 событий и оба архива.

В пятом случае ответ до SIGKILL ещё не выдавался. Сравниваются восстановленный факт и
последующие обычные ответы; равенство несуществовавшим потерянным байтам не заявляется.

## Публичные основания

- [Пять случаев](restore-interrupt-cases.json), [процессы SIGKILL](restore-interrupt-worker-outcomes.json), [обычные CLI](restore-interrupt-cli-outcomes.json).
- [Отказы записи и публикации](restore-interrupt-rejections.json), [исходная сводка](restore-interrupt-source-summary.json).
- [Исторический источник](restore-interrupt-source-observations.json), [живая база](restore-interrupt-live-observations.json), [прежнее восстановление](restore-interrupt-previous-observations.json).
- [Восстановленные базы](restore-interrupt-target-observations.json), [архивы](restore-interrupt-archive-observations.json), [машинный отчёт](restore-interrupt-report.json).

## Границы результата

Новых этапов: 7; SIGKILL: 5; обычных CLI: 20; отказов: 10.
Это пять выбранных границ отдельного процесса, а не моделирование отключения питания, отказа
ядра или хранилища. Все возможные точки прерывания, расписания и предельная нагрузка не проверялись.
Worker рассчитан на этот комплект из трёх файлов данных. API восстановления допускает до
17 файлов; текущая демонстрация не расширяет покрытие на максимальный комплект.

Драйвер читает только публичные Store/API-ответы и узкие метаданные фиксированных путей.
Частные планы, записи owner, manifests, контрольные суммы и origin не разбираются и не
публикуются. Сравнение исключает только верхний governance.observed_at. Адресованные
доказательства, timestamps и expired-признаки сохраняются. Карантин блокирует новые записи
и публикацию; внешний fencing, LLM, независимая экспертиза и истинность содержания не установлены.
