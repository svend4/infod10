# Пошаговое восстановление полного исторического комплекта

Время отчёта: 2026-10-21T20:51:54.778497+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий полного backup](workspace-report.md); его публичные основания
сохранены без перезаписи. Новый процесс восстанавливает тот же снимок R4 отдельными файловыми
шагами. Действующий источник остаётся на R5; новые credentials или роли не выдаются.

## Последовательность

| Этап | Наблюдение |
|---|---|
| [complete-source-and-current-live-state](restore-job-stage-01-complete-source-and-current-live-state.json) | A complete historical R4 bundle with two archives is available; the live source has continued to R5. |
| [prepared-job-blocks-uninitialized-store](restore-job-stage-02-prepared-job-blocks-uninitialized-store.json) | Prepare records one operation; no current DB is copied yet, and Store cannot initialize an active database at that path. |
| [exact-request-binding-and-bounds](restore-job-stage-03-exact-request-binding-and-bounds.json) | Another request or preexisting unrelated destination is rejected; step limits remain within the explicit file bound. |
| [first-file-is-readable-quarantined-history](restore-job-stage-04-first-file-is-readable-quarantined-history.json) | One CLI process copies only the current DB. A new Store reads R4 in quarantine, while full history inspection remains incomplete. |
| [separate-process-resumes-next-file](restore-job-stage-05-separate-process-resumes-next-file.json) | A later CLI process resumes from the copied DB and adds one older archive; it does not recreate the restore identity. |
| [lost-business-reply-recovered-without-new-restore](restore-job-stage-06-lost-business-reply-recovered-without-new-restore.json) | The final step completes the full bundle; its business reply is discarded, and a new status process recovers the exact same public result. |
| [completed-operation-needs-no-original-source-path](restore-job-stage-07-completed-operation-needs-no-original-source-path.json) | The source bundle and all three former workspace directories are temporarily unavailable. Completed status, replay and all target history still read successfully. |
| [concurrent-callers-share-one-file-progress](restore-job-stage-08-concurrent-callers-share-one-file-progress.json) | Two separate CLI processes are launched before waiting. Their one-file steps return successive checkpoints, followed by one complete quarantined restore. |
| [completed-history-stays-inspection-only](restore-job-stage-09-completed-history-stays-inspection-only.json) | Repeated recovery and refused writes preserve the completed target, the source bundle, the live R5 workspace and the earlier restore. |

## Подготовка и продолжение

Prepare закрепляет один запрос, назначение и постоянную идентичность результата. Три отдельных
шага limit=1 дают счётчики 1, 2 и 3: текущая база, старший архив и ближайший архив. До копии DB
Store не может создать активную базу; после первого шага читает R4 в карантине. Полный inspect
остаётся незавершённым до последнего файла и общего manifest.

Это обычные успешно завершённые команды и паузы между ними. Падение процесса внутри копирования,
частичный staging-файл, временная hardlink-пара и повреждение SQL в сценарии не создавались.

## Восстановление ответа и самодостаточность

Ответ последнего CLI-шагa проверен и отброшен как бизнес-результат. Новый status возвращает тот же
полный публичный ответ; повтор completed step также сохраняет его. Внутренний адрес сравнения
публичного ответа не публикуется. Это модель потери ответа после завершённого локального вызова.

Затем source bundle и три прежних рабочих каталога временно переименованы. Completed status,
повтор step, полный inspect, Store и оба архива читаются без этих путей. Имена восстановлены в
finally; исходные наблюдения совпали. Вторая операция показывает два независимо запущенных
CLI-процесса с последовательными файловыми checkpoints; порядок захвата блокировки не задавался.

## Публичные основания

- [Статусы](restore-job-statuses.json), [CLI](restore-job-cli-outcomes.json), [отказы](restore-job-rejections.json).
- [Восстановление ответа](restore-job-reply-recovery.json), [полная сводка](restore-job-completed-summary.json).
- [Источник снимка](restore-job-source-observations.json), [живая база](restore-job-live-observations.json), [исходная сводка](restore-job-source-summary.json).
- [Первый checkpoint](restore-job-partial-observations.json), [полный target](restore-job-target-observations.json), [архивы](restore-job-archive-observations.json).
- [Недоступность путей](restore-job-detached-path-observations.json), [два CLI caller](restore-job-concurrency.json), [машинный отчёт](restore-job-report.json).

## Границы выполненного

Новых этапов: 9; новых CLI-процессов: 18; ожидаемых отказов: 16.
Файлов данных: 3; наблюдавшийся прогресс: 0 → 1 → 2 → 3.
Восстановленная ревизия: 4; события: 36; архивов: 2.
Поздняя действующая база: ревизия 5, событий 59.
Прежние допуски и решения остаются историей. Новые записи и публикация из восстановленной базы
блокируются. Внешние LLM, человеческая экспертиза, аппаратные отказы и предельная нагрузка не моделировались.

Сверка публичных наблюдений исключает только вычисляемый верхний governance.observed_at. Полные
адресованные доказательства, timestamps и expired-признаки сохраняются. Частные планы, staging,
контрольные суммы, manifests, базы и credentials остаются вне публикации.
