# Выполненный пример частного снимка Store и восстановления для осмотра

Время отчёта: 2026-10-21T13:59:39.074500+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную
Python-конфигурацию. A/B одобрили обе записи, C запросил изменения. Предлагаемый код не исполнялся.

## Последовательность

| Этап | Наблюдение | Ревизия источника |
|---|---|---:|
| [authenticated-candidate](stage-01-authenticated-candidate.json) | Three author jobs and one composition prepare the fictional A document/code candidate; no proposed code is executed. | 1 |
| [quorum-veto-and-three-catalog-edges](stage-02-quorum-veto-and-three-catalog-edges.json) | Original A/B approvals and C's objection survive three unrelated D updates; the active objection still blocks acceptance. | 1 |
| [partial-audit-and-active-author-lease](stage-03-partial-audit-and-active-author-lease.json) | The audit is pending after one of three edges. A separate D author lease has token 1 and remains unsubmitted at capture. | 1 |
| [committed-WAL-state-captured](stage-04-committed-WAL-state-captured.json) | A SELECT-only reader remains at revision 1 while ordinary APIs accept revision 2 into WAL; the backup captures revision 2 without changing source events. | 2 |
| [source-continues-after-capture](stage-05-source-continues-after-capture.json) | The live source revokes B, completes the audit, submits the original D lease and changes D policy. Historical acceptance replay still returns the same receipt. | 2 |
| [restored-historical-state-is-readable](stage-06-restored-historical-state-is-readable.json) | The restored copy preserves receipt, packets, partial checkpoint and the old leased task; B remains historically unrevoked there while the live source has revoked B. | 2 |
| [restored-writes-and-publish-rejected](stage-07-restored-writes-and-publish-rejected.json) | The restored copy rejects acceptance replay, B's old decision, the old lease submit, enqueue and publish before domain changes; publish creates no output directory. | 2 |
| [quarantine-persists-in-the-database](stage-08-quarantine-persists-in-the-database.json) | Opening the backup DB directly and copying only that marked DB without sidecars both retain the inspection boundary; enqueue is rejected in each copy. | 2 |
| [safe-operator-rejections](stage-09-safe-operator-rejections.json) | Missing authority, an existing backup destination, an incomplete empty directory and an existing restore destination are rejected without SQL corruption or overwrites. | 2 |

## Что попало в снимок

После трёх несвязанных правок D аудит C прошёл один из трёх переходов. Отдельное авторское задание D
оставалось с живой арендой token 1. Затем SQLite читатель mode=ro открыл транзакцию и прочёл ревизию 1.
Обычные API записали явный abstain C и приняли составное предложение: ревизия 2, receipt schema 4.

Старый читатель продолжал видеть ревизию 1; файл WAL существовал и был непустым. Отдельный CLI процесс
создал согласованный снимок уже принятой ревизии 2. Статус и события источника при этом не изменились.
Inspect проверил частные контрольные суммы, схему, SQLite integrity и foreign keys. В отчёт вошли только
безопасные результаты проверки, случайный backup ID и счётчики. Частные manifest и хеш базы не раскрываются.

## Источник продолжил работу

После снимка источник отозвал B, завершил аудит, отправил предложение по прежней живой аренде D и
выполнил отдельную четвёртую правку D. Прежняя квитанция принятия продолжила возвращаться историческим replay.
Эти более поздние изменения отсутствуют в восстановленной копии: там B исторически не отозван, аудит
остаётся pending после одного перехода, задание D остаётся leased, а квитанция принятия совпадает точно.

## Восстановленная копия доступна для чтения

Restore создал новый частный каталог с inspection-only базой. Через обычные API прочитаны исходные IDs,
полные source/target packets, квитанция, каталог аудита и точные результаты recover по operation ID.
Попытки accept replay, прежнего решения B, submit старой аренды, enqueue и publish были отклонены
с store_snapshot_quarantined. Каталог публикации не появился, статус и события копии не изменились.

Прямое открытие DB из backup и копирование только уже карантинной DB без intent/manifest sidecars также
сохранили запрет записи. Исторические допуски и аренды в снимке не активируются. Действия активации нет.

## Публичные основания

- [Машинный отчёт](report.json), [безопасные сводки backup/inspect/restore](backup-summaries.json), [исходы CLI](cli-outcomes.json).
- [WAL и старый читатель](wal-evidence.json), [статусы](statuses.json), [границы доступа](snapshot-access.json).
- [Состояние при захвате](captured-state.json), [восстановленное](restored-state.json), [поздний источник](final-state.json).
- [Сравнение допуска B](grant-comparison.json), [задачи и аренда](tasks.json), [операции и отказы](operations.json).
- [Квитанции](receipts.json), [принятие](acceptance.json), [исторический replay](replays.json).
- [Пакеты](packets.json), [решения](decisions.json), [наблюдения](observations.json), [предложения](proposals.json).
- [Аудит](audits.json), [checkpoints](checkpoints.json), [результаты и recover](audit-results.json), [каталоги](catalogue-pages.json).
- [Content history](content-policy-history.json), [governance history](governance-policy-history.json), [переходы](transitions.json).
- [События снимка](captured-events.json), [события восстановления](restored-events.json), [поздние события источника](events.json).

Полные public Store bodies сохранены без вырезания полей, изменяющего их адреса. Файлы DB, intent,
manifest и credentials находятся только в private/ и не копируются в публичные JSON/MD или Git.

## Границы выполненного

Авторский пул: 3 Python workers; наблюдавшихся потоков: 3; заданий до снимка: 3.
Отдельных CLI процессов: 4; решений: 6; checkpoints: 3.
Правок D до снимка: 3; после снимка: 1; отказы записи в копии: 7.
Размер пула и число ролей не являются числом независимых моделей или измеренной ёмкостью сервиса.
Контрольные суммы и integrity/fk устанавливают внутреннюю согласованность при доверии к реализации,
SQLite и владельцу среды. Они не доказывают внешнюю подлинность, истинность прозы или поведение кода.
Сбой сети, авария процесса и повреждение SQL не воспроизводились. Внешние LLM и человеческая экспертиза отсутствуют.

Частные каталоги имеют права 0700, DB/manifest/intent/credentials — 0600. Bundle содержит частные хеши
допусков внутри DB и должен оставаться частным. Файлы bearer credentials хранятся отдельно от bundle.
Создание снимка и локальных отчётов не является публикацией Git, выпуском или запуском восстановленной копии.
