# Выполненный пример пошагового аудита истории

Время отчёта: 2026-10-20T11:54:46.371739+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную
Python-конфигурацию. Два проверяющих одобрили обе записи; C записал request_changes. Код не исполнялся.

## Последовательность

| Состояние | Наблюдение | Ревизия |
|---|---|---:|
| [authenticated-candidate](stage-01-authenticated-candidate.json) | Three local author jobs and one composition prepare a fictional candidate; formal review is recorded. | 1 |
| [initial-quorum-and-objection](stage-02-initial-quorum-and-objection.json) | Two reviewers approve both writes; C explicitly requests changes. The audit does not grant review authority. | 1 |
| [long-history-keeps-acceptance-bound](stage-03-long-history-keeps-acceptance-bound.json) | 130 unrelated catalog updates exceed the unchanged 64-edge acceptance reader; current heads require reconsideration. | 1 |
| [audit-freezes-an-exact-target](stage-04-audit-freezes-an-exact-target.json) | The administrator starts an advisory audit of C's exact source decision and the current target packet. | 1 |
| [live-catalog-does-not-move-target](stage-05-live-catalog-does-not-move-target.json) | A later D edit changes live state, while the audit header and its initial checkpoint remain unchanged. | 1 |
| [checkpoint-CAS-selects-one-writer](stage-06-checkpoint-CAS-selects-one-writer.json) | Two operations propose the same checkpoint; one advances 64 edges and the other receives a CAS conflict. | 1 |
| [second-bounded-chunk](stage-07-second-bounded-chunk.json) | A second transaction checks the next 64 transitions and links its immutable checkpoint to the previous segment. | 1 |
| [frozen-history-completed](stage-08-frozen-history-completed.json) | The final two transitions complete exactly the frozen target, excluding the later live D edit. Completion stays advisory. | 1 |
| [old-chunk-replay-is-historical](stage-09-old-chunk-replay-is-historical.json) | The first operation is replayed after later checkpoints; its old body is returned without new events or head movement. | 1 |
| [completed-audit-does-not-authorize](stage-10-completed-audit-does-not-authorize.json) | Store.accept retains the MAX64 contract after the advisory audit has completed; no vote or grant was upgraded. | 1 |
| [fresh-quorum-does-not-clear-old-head](stage-11-fresh-quorum-does-not-clear-old-head.json) | Fresh A/B approvals supply a quorum, but C's unresolved old head remains a proposal-level blocker. | 1 |
| [fresh-objection-remains-a-veto](stage-12-fresh-objection-remains-a-veto.json) | C explicitly records a current request_changes. Historical uncertainty is replaced by an active objection, not an approval. | 1 |
| [selected-change-return-audit-started](stage-13-selected-change-return-audit-started.json) | A's selected policy changes and returns; a separate audit targets both recorded transitions from a fresh source. | 1 |
| [selected-policy-barrier-is-explained](stage-14-selected-policy-barrier-is-explained.json) | The audit stops at the selected-context change even though the final selected policy has returned to its former body. | 1 |
| [direct-reset-audit-started](stage-15-direct-reset-audit-started.json) | A fresh source precedes an identical direct governance policy update; this is a reset, not a content-only edge. | 1 |
| [governance-reset-is-explained](stage-16-governance-reset-is-explained.json) | The separate audit terminates at a known direct policy reset. It does not manufacture a successful continuity edge. | 1 |
| [fresh-consideration-under-current-policy](stage-17-fresh-consideration-under-current-policy.json) | Current A/B decisions and C's explicit abstain satisfy the existing policy; no advisory checkpoint is used as a vote. | 1 |
| [acceptance-uses-existing-authority](stage-18-acceptance-uses-existing-authority.json) | One acceptance advances revision 1 to 2 with receipt schema 4 and fresh decisions; the audit remains separate advisory material. | 2 |
| [historical-audits-and-replays-stay-frozen](stage-19-historical-audits-and-replays-stay-frozen.json) | After acceptance, old checkpoint replays, original decisions and both terminal barrier explanations remain exact historical objects. | 2 |

## Практический результат

После 130 несвязанных обновлений D текущие головы решений превысили предел 64 перехода принятия.
Администратор начал отдельный аудит C с точным целевым пакетом. Следующая живая правка D не передвинула
цель. Три порции 64 + 64 + 2 завершили ровно замороженные 130 переходов.

Две операции пытались продвинуть один checkpoint: CAS разрешил одну, другая получила конфликт. Повтор
первой операции после более новых checkpoints вернул прежний сегмент без событий и изменения головы.
Изменение параметра прежнего operation ID было отклонено как idempotency conflict.

history_complete остаётся совещательным выводом с acceptance_authority=false и authority_evaluated=false.
Он не снял MAX64 blocker. Свежий кворум A/B также не обошёл старую неопределённую голову C. Явное новое
request_changes C заменило неопределённость действующим возражением и продолжило блокировать принятие.

Два отдельных аудита рассмотрели свежие источники возле реальных барьеров. Изменение выбранного A с
последующим возвратом завершилось scope_changed; прямая идентичная governance-политика дала
governance_reset. Повреждение SQL не имитировалось, history_invalid не выдаётся за выполненный случай.

Только текущие новые решения A/B и явный abstain C разрешили одно принятие: ревизия 1 → 2, receipt
schema 4. Аудит не стал голосом, полномочием или заменой принятия. Исторические чтения после приёмки
сохранили checkpoints, прежнее решение и квитанцию без новых событий.

## Материалы для проверки ссылок и хешей

- [Полный отчёт](report.json), [авторы](authors.json), [композиция](composition.json), [предложения](proposals.json).
- [Пакеты с метками](packets.json), [решения](decisions.json), [наблюдения](observations.json).
- [Заголовки аудитов](audits.json), [все checkpoints](checkpoints.json), [ответы шагов](audit-steps.json).
- [Запросы операций](audit-requests.json), [финальные аудиты](audit-final.json), [CAS-гонка](audit-writer-race.json).
- [История content-политик](content-policy-history.json), [история governance](governance-policy-history.json), [полные переходы](transitions.json).
- [Краткие обновления каталога](catalog-updates.json), [активация](activation.json), [операции и отказы](operations.json).
- [Принятие](acceptance.json), [повторы](replays.json), [события](events.json), [конечное состояние](final-state.json).

Полные политики и история сохранены один раз в отдельных файлах; файлы этапов не повторяют весь каталог.
Привязки позволяют проверить внутреннюю согласованность сохранённых данных, без внешней подписи.

## Границы

Проверяется история явно объявленной области и сохранённых ссылок. Истинность прозы, полнота описи,
поведение программы, человеческое понимание и независимость участников не установлены. Все роли
запрограммированы; внешние LLM и человеческая экспертиза отсутствуют. Предлагаемый код не исполняется.

Шаги используют разные SQL-снимки; текущий Store.accept остаётся отдельной транзакцией и сохраняет
свой предел 64. Хеши checkpoints и цепочек не заменяют доверие к реализации, SQLite и локальным часам.
130 обновлений и две операции записи являются примером договора, без измерения пропускной способности.

private/ имеет права 0700, credentials — 0600. Секреты, их хеши и SQLite не публикуются. Отдельные JSON
наблюдения последовательны; внешняя публикация и выпуск выполняются отдельными действиями.

Пул авторских заданий: 3 Python workers; пул записи checkpoints: 2; конкурирующих операций CAS: 2.
Решений: 16; аудитов: 3; checkpoints: 8; событий: 461.
Размер пула не является измеренной ёмкостью сервиса или числом независимых моделей.
