# Ограниченная локальная аренда позиций каталога заданий

Время отчёта: 2026-10-22T18:19:11.770414+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий write-job catalog](restore-case-retention-review-write-catalog-report.md); его 377 публичных файлов сохранены.
Два отдельных ledger закрепляют один явный каталог из трёх уже завершённых write jobs. Назначение позиции не устанавливает наличие невыполненной работы.

## Этапы

| Этап | Наблюдение |
|---|---|
| [explicit-ledgers-retain-historical-membership](restore-case-retention-review-write-leases-stage-01-explicit-ledgers-retain-historical-membership.json) | Two independent private ledgers capture the same explicit three-position catalog. Exact create replay and show preserve each identity; bounded pages start unclaimed and retain cursor arithmetic. |
| [ordinary-workers-compete-within-one-ledger](restore-case-retention-review-write-leases-stage-02-ordinary-workers-compete-within-one-ledger.json) | Three ordinary CLI processes are launched before responses are collected. Two compete for position 1: exactly one succeeds and one receives busy. Position 2 succeeds independently; process overlap, throughput and maximum capacity are not measured. |
| [generation-cas-and-historical-replies](restore-case-retention-review-write-leases-stage-03-generation-cas-and-historical-replies.json) | Releasing position 1 permits a fresh lease ID and generation 2. A stale generation cannot release it; exact old claim/release replies remain historical. A genuinely discarded position-3 claim reply is recovered without extending its expiry, then released. |
| [independent-ledgers-and-source-free-coordination](restore-case-retention-review-write-leases-stage-04-independent-ledgers-and-source-free-coordination.json) | A second ledger can claim the same catalog position while the first ledger's generation 2 remains active: exclusivity is scoped to one ledger. With both catalogs and six job/target directories absent, ledger show/page/replay and a new claim/release still work. Every path returns in finally. |
| [invalid-requests-and-stale-cas-refuse](restore-case-retention-review-write-leases-stage-05-invalid-requests-and-stale-cas-refuse.json) | Malformed bounds, identities and cursors refuse. Reusing operation IDs with changed parameters, stale release identities, already released new operations and an active claim refuse without advancing captured jobs or changing accepted history. |
| [released-heads-preserve-jobs-and-authority-boundaries](restore-case-retention-review-write-leases-stage-06-released-heads-preserve-jobs-and-authority-boundaries.json) | All five previously claimed ledger positions end released; the secondary ledger's third position remains unclaimed. Twelve unique immutable operations, completed write jobs, historical catalogs, blocked restore jobs and source/live Store observations remain distinct. No lease API advances a job or grants domain authority. |

## Конкуренция, поколения и исторические ответы

Три обычных CLI запущены до сбора ответов. Два претендента на позицию 1 дали один claim и один busy; позиция 2 назначена отдельно. Перекрытие выполнения процессов и пропускная способность не измерялись.
Release первой позиции позволил claim поколения 2 с новым lease ID. Устаревший release отвергнут. Точные повторы прежних claim/release вернули исторические квитанции без смены текущей головы и продления срока.
Настоящий ответ claim позиции 3 отброшен, затем восстановлен точным повтором. Сравнительный digest остался только в памяти демонстратора.
Во втором ledger та же позиция 1 назначается независимо от действующего claim первого ledger. Единственность ограничена одним ledger; внешнего fencing нет.
При отсутствии восьми catalog/job/target каталогов show/page/replay и новый claim/release продолжили работу по собственным controls. Все пути возвращены в finally; прежние blocked restore jobs никогда не перемещались.

## Публичные основания

- [Ledgers](restore-case-retention-review-write-leases-ledgers.json), [12 операций](restore-case-retention-review-write-leases-operations.json), [12 страниц](restore-case-retention-review-write-leases-pages.json).
- [Конкуренция](restore-case-retention-review-write-leases-competition.json), [CLI](restore-case-retention-review-write-leases-cli-outcomes.json), [отказы](restore-case-retention-review-write-leases-rejections.json).
- [Восстановление ответа](restore-case-retention-review-write-leases-reply-recovery.json), [controls](restore-case-retention-review-write-leases-control-observations.json).
- [Источник](restore-case-retention-review-write-leases-source-observations.json), [живая среда](restore-case-retention-review-write-leases-live-observations.json), [машинный отчёт](restore-case-retention-review-write-leases-report.json).

## Границы результата

Шесть этапов, 76 новых CLI: 47 успешных и 29 ожидаемых отказов. Из них 66 lease-команд: 40 успешных и 26 отказов. С прежним основанием это 513 обычных CLI и 14 прежних interrupt workers, всего 527 различных дочерних процессов.
Два ledger сохраняют 12 различных операций: шесть claim и шесть release. Пять когда-либо назначенных позиций завершили сценарий released; одна позиция второго ledger осталась unclaimed. Все три write jobs и пять review records сохранены.
Source/живая история сохранили R4/36 событий и R5/59. Управляющие записи прежних plans, cases, catalogs, jobs и reviews не изменились.
Демонстратор не читает private control bodies, содержимое ledger SQLite либо staging. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.
Нет новых fault workers, SIGKILL, injected partial writes либо ожидания expiry. Истечение срока, регресс часов, исчерпание лимита операций и отказ хранения этим запуском не воспроизводились.
Lease API не проверяет фазы jobs и не вызывает step. Прежний прямой step не требует этого lease. Worker самозаявлен; назначение не удостоверяет личность, независимость экспертизы или истинность текста, не разрешает доменные изменения, архивирование, удаление либо активацию прежних полномочий.
