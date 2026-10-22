# Долговечное действие одной фазы с проверкой локальной аренды

Время отчёта: 2026-10-22T17:19:06.796838+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий локальных аренд](restore-case-retention-review-write-leases-report.md); его 395 публичных файлов сохранены.
Подготовлен один новый write job с точным историческим minimum plan и verdict defer. Actor review и worker исполнения остаются разными самозаявленными метками.

## Этапы

| Этап | Наблюдение |
|---|---|
| [explicit-claim-and-single-phase-intent](restore-case-retention-review-write-action-stage-01-explicit-claim-and-single-phase-intent.json) | One fresh write job, its one-member explicit catalog and a new lease ledger are prepared. A generation-1 claim is captured in an immutable prepared-to-ready action; show remains a read and exact prepare replay returns its fixed receipt. |
| [same-action-callers-share-one-recorded-effect](restore-case-retention-review-write-action-stage-02-same-action-callers-share-one-recorded-effect.json) | Two ordinary execute processes are launched before collection for the same action. Both return the exact same saved result and the write reaches only ready. Separate show and execute replays preserve that result; lease history remains at one event. |
| [new-generation-rejects-the-old-pending-action](restore-case-retention-review-write-action-stage-03-new-generation-rejects-the-old-pending-action.json) | A ready-to-linked action captures generation 1 before release. A fresh claim receives generation 2. Executing the old pending action refuses lease_changed before any phase effect; its own show remains prepared and the job stays ready. |
| [performed-phase-and-separate-post-observation](restore-case-retention-review-write-action-stage-04-performed-phase-and-separate-post-observation.json) | Three generation-2 actions capture the same ready phase. One performs ready-to-linked. A second observes that exact post phase and saves expected_phase_observed with effect_performed=false; the job remains linked. The third stays pending for the later beyond-post refusal. |
| [finish-reply-recovery-and-own-only-history](restore-case-retention-review-write-action-stage-05-finish-reply-recovery-and-own-only-history.json) | With the captured write catalog absent, the final action completes the review. Its actual reply is discarded and recovered exactly. A pending action beyond its target refuses phase_changed. After lease release, all four catalog/ledger/job/target paths are hidden: recorded action show/execute replay and pending show still read only own controls. Every path returns in finally. |
| [exact-inputs-and-domain-boundaries-remain-explicit](restore-case-retention-review-write-action-stage-06-exact-inputs-and-domain-boundaries-remain-explicit.json) | Changed exact requests, malformed scalar inputs and a released claim refuse. Original write jobs and lease heads remain unchanged; blocked restore jobs stay blocked. Six action intents contain four saved observations and two pending records, while only three phase effects completed the one new review. |

## Наблюдаемый результат

Два обычных execute callers запущены до сбора ответов для одного action. Они вернули одну и ту же сохранённую квитанцию, а job достиг только ready. Перекрытие исполнения и пропускная способность не измерялись.
Action поколения 1, подготовленный для ready→linked, отказал после release и нового claim поколения 2. Новое действие выполнило ready→linked; соседнее действие лишь зафиксировало уже наблюдаемый linked с effect_performed=false.
Последнее действие выполнило linked→completed при недоступном исходном write catalog. Его настоящий ответ отброшен и восстановлен точным replay. Другое pending действие, оставшееся позади target phase, отказало phase_changed без нового шага.
После освобождения аренды четыре catalog/ledger/job/target пути временно скрыты. Исторические show/execute replay записанных действий и show двух pending intents продолжили чтение только собственных controls. Все пути возвращены.
Шесть action intents сохранили четыре наблюдения: три phase_performed и одно expected_phase_observed. Два pending intents остались без result. В новом lease ledger четыре события; проверки действий не продлили аренды и не изменили эту историю.

## Публичные основания

- [Шесть действий](restore-case-retention-review-write-action-actions.json), [четыре записанных результата](restore-case-retention-review-write-action-recorded.json), [два pending intents](restore-case-retention-review-write-action-pending.json).
- [Фазы, повтор и controls](restore-case-retention-review-write-action-execution-observations.json), [аренды](restore-case-retention-review-write-action-lease-observations.json).
- [CLI](restore-case-retention-review-write-action-cli-outcomes.json), [отказы](restore-case-retention-review-write-action-rejections.json), [восстановление ответа](restore-case-retention-review-write-action-reply-recovery.json).
- [Источник](restore-case-retention-review-write-action-source-observations.json), [живая среда](restore-case-retention-review-write-action-live-observations.json), [машинный отчёт](restore-case-retention-review-write-action-report.json).

## Границы

Шесть этапов, 74 новых CLI: 55 успешных и 19 ожидаемых отказов. Из них 45 action-команд: 29 успешных и 16 отказов. С основанием 587 ordinary CLI и 14 прежних interrupt workers, всего 601 дочерний процесс.
Добавлен один write job и один review; всего четыре write jobs и шесть reviews. Исходные Store-наблюдения сохранили R4/36 и R5/59 событий, прежние write/lease controls и blocked restore состояния прежние.
Нет новых fault workers, injected partial writes или ожидания expiry. Восстановление после реального прерывания, частичный result, регресс часов, отказ хранилища и потеря питания данным запуском не проверялись.
Новый wrapper требует текущую captured аренду перед новой записью результата и локальным эффектом. Прежний direct step её не требует; других ledgers, прямого SQL и файлового владельца этот протокол не ограничивает. Общей SQL/filesystem транзакции, внешнего fencing, удостоверения worker либо смыслового одобрения текста нет.
