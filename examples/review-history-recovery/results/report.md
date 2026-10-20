# Выполненный пример восстановления ответов и каталога аудитов

Время отчёта: 2026-10-20T12:33:55.534422+02:00 (Europe/Berlin). Технические отметки — UTC/Unix epoch.

Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную
Python-конфигурацию. A/B одобрили обе записи, C записал request_changes. Предлагаемый код не исполнялся.

## Последовательность

| Этап | Наблюдение | Ревизия |
|---|---|---:|
| [authenticated-candidate](stage-01-authenticated-candidate.json) | Three local author jobs and one composition prepare the candidate; the initial catalogue pins an empty ceiling of zero. | 1 |
| [initial-quorum-and-veto](stage-02-initial-quorum-and-veto.json) | A/B approve both writes, while C requests changes; the catalogue and recovery do not replace review authority. | 1 |
| [three-unrelated-catalog-transitions](stage-03-three-unrelated-catalog-transitions.json) | Three different D metadata updates preserve the full selected A basis, including C's active veto. | 1 |
| [completed-start-response-discarded](stage-04-completed-start-response-discarded.json) | An independent CLI process commits start and exits zero; this client deliberately discards stdout and has no audit ID from that response. | 1 |
| [start-recovered-by-operation](stage-05-start-recovered-by-operation.json) | A new CLI process uses only the known start operation ID to recover the immutable initial checkpoint without new events. | 1 |
| [completed-advance-response-discarded](stage-06-completed-advance-response-discarded.json) | A separate CLI process commits one transition and exits zero; its checkpoint response is also discarded. | 1 |
| [advance-recovered-by-operation](stage-07-advance-recovered-by-operation.json) | Recovery retrieves the exact stored first chunk, rather than repeating its write or requiring the lost checkpoint ID. | 1 |
| [three-original-audits](stage-08-three-original-audits.json) | There are three start events: main is pending after one edge, another audit is complete, and the third remains pending. | 1 |
| [catalogue-membership-pinned](stage-09-catalogue-membership-pinned.json) | The first public CLI list page fixes snapshot_max_seq for the three existing roots and observes main's pending head. | 1 |
| [later-root-and-current-head-change](stage-10-later-root-and-current-head-change.json) | A fourth root starts outside the saved ceiling. Main advances the remaining two edges and reaches history_complete. | 1 |
| [same-membership-new-head-observation](stage-11-same-membership-new-head-observation.json) | Repeating the same bounded page returns the same root ID and a newer head; snapshot_max_seq fixes membership rather than all heads together. | 1 |
| [pinned-pagination-exhausted](stage-12-pinned-pagination-exhausted.json) | Three one-entry pages enumerate each original root exactly once, exclude the late root and terminate without a cursor. | 1 |
| [new-scan-and-empty-snapshot](stage-13-new-scan-and-empty-snapshot.json) | A new catalogue scan sees all four roots. The earlier ceiling zero still describes an empty set after new starts. | 1 |
| [rejected-recovery-and-cursor-inputs](stage-14-rejected-recovery-and-cursor-inputs.json) | Unknown operations, missing administrative authority and three invalid catalogue arguments are rejected without events. | 1 |
| [advisory-results-preserve-veto](stage-15-advisory-results-preserve-veto.json) | Completed audits and recovered responses leave C's request_changes active; ordinary Store.accept is blocked. | 1 |
| [acceptance-retains-original-approvals](stage-16-acceptance-retains-original-approvals.json) | Only C's explicit current abstain removes the veto. One receipt schema 4 retains the four original approval IDs through three verified catalog transitions. | 2 |
| [later-live-policy-stays-outside-history](stage-17-later-live-policy-stays-outside-history.json) | After acceptance a fourth D edit changes live policy; it does not alter the earlier frozen audit target or accepted receipt. | 2 |
| [immutable-recovery-after-live-changes](stage-18-immutable-recovery-after-live-changes.json) | Recovery returns the initial and first historical checkpoints with main's current completed head, while accepted receipt replay and pending audit states stay historical. | 2 |

## Восстановление известной операции

После трёх различных несвязанных обновлений D отдельный процесс обычной CLI выполнил start и завершился
с кодом 0. Его stdout был направлен в DEVNULL. Клиент не получил audit ID из отброшенного ответа.
Следующий отдельный процесс recover по известному operation ID вернул исходный checkpoint и audit ID.
Затем таким же способом был отброшен ответ advance на один переход; recover вернул уже сохранённый шаг.

Это модель потерянного ответа через намеренное отбрасывание stdout завершившейся локальной команды.
Сетевой сбой и авария процесса во время записи не воспроизводились. Восстановление не добавило событий.

## Каталог с фиксированным составом

Три исходных аудита имели разные головы: основной pending после одного перехода, второй history_complete,
третий pending с начальным checkpoint. Первая страница limit=1 зафиксировала snapshot_max_seq.
Затем появился четвёртый аудит вне границы, а основной завершил оставшиеся два перехода.

Повтор той же страницы сохранил audit ID и показал новую текущую голову. Три страницы по одной записи
перечислили каждый исходный аудит ровно один раз и исключили поздний. Новый scan увидел все четыре.
Исходный пустой snapshot с границей 0 остался пустым после всех новых start.

Граница фиксирует состав по start events. Головы наблюдаются заново на каждой странице; каталог не
предоставляет один общий атомарный снимок голов для всей последовательности страниц.

## Принятие и дальнейшая история

Ни завершённый аудит, ни recover, ни каталог не сняли действующее возражение C: Store.accept остался
заблокирован. Только явный новый abstain C разрешил одно принятие: ревизия 1 → 2, receipt schema 4.
Четыре исходных одобрения A/B сохранили ID и доказательства preserved_catalog через три перехода.

Отдельная четвёртая правка D произошла уже после принятия. Восстановление старых start и advance после
этой правки вернуло неизменяемые прежние checkpoints и более новую completed голову. Историческая
квитанция принятия осталась прежней. Два других аудита сохранили pending даже после accepted proposal.

## Сохранённые основания

- [Машинный отчёт](report.json), [авторы](authors.json), [предложения](proposals.json), [композиция](composition.json).
- [Исходы CLI процессов](cli-outcomes.json), [восстановления](recoveries.json), [наблюдения без записи](read-only-observations.json).
- [Страницы каталога](catalogue-pages.json), [состав сканов](catalogue-membership.json), [финальные аудиты](audit-final.json).
- [Заголовки](audits.json), [checkpoints](checkpoints.json), [ответы аудитов](audit-results.json), [запросы](audit-requests.json).
- [Полные пакеты](packets.json), [решения](decisions.json), [наблюдения governance](observations.json).
- [История content policy](content-policy-history.json), [история governance](governance-policy-history.json), [переходы](transitions.json).
- [Краткие правки D](catalog-updates.json), [операции и отказы](operations.json), [события](events.json).
- [Принятие](acceptance.json), [исторические повторы](replays.json), [финальное состояние](final-state.json).

Полные тела политик и переходов сохранены в общих файлах; этапы не повторяют всю историю каталога.
Source/target packets перечитаны по известным ID через публичный Store.content_packet.

## Границы выполненного примера

Пул авторских заданий: 3 Python workers; наблюдавшихся потоков: 3; авторских заданий: 3.
Отдельных CLI процессов: 15; отброшенных ответов: 2; аудитов: 4; checkpoints: 7.
Правок до целевого пакета: 3; отдельных правок после принятия: 1; решений: 6.
Размер пула и число локальных ролей не задают число независимых моделей или измеренную ёмкость сервиса.
Внешние LLM и человеческая экспертиза отсутствуют. Смысловая истинность прозы, полнота описи и поведение
кода не установлены. Хеши обеспечивают внутренние привязки при доверии к SQLite, приложению и часам.

private/ имеет права 0700, credentials и state.sqlite3 — 0600. Секреты, их хеши и SQLite не входят в
публичные JSON/MD. Создание выпуска и публикация в Git являются отдельными действиями.
