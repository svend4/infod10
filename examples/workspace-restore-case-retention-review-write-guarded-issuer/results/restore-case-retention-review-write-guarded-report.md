# Отдельное семейство защищённой записи review

Время отчёта: 2026-10-22T21:58:07.490242+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [предыдущий сценарий issuer](restore-case-retention-review-write-issuer-report.md); его 449 публичных файлов сохранены.
Добавлены один новый план, один guarded job, девятый review и отдельный локальный ключ. Три защищённые фазы завершают запись; затем доступ отзывается.

## Этапы

| Этап | Наблюдение |
|---|---|
| [distinct-family-and-local-credential](restore-case-retention-review-write-guarded-stage-01-distinct-family-and-local-credential.json) | A fifth retention plan copies only the original minimum plan's public parameters. One distinct guarded job captures its own review and one local credential. Exact prepare and own show retain the same guard; status is observation rather than permission. |
| [legacy-refusals-before-effects](restore-case-retention-review-write-guarded-stage-02-legacy-refusals-before-effects.json) | Legacy step, status, prepare and catalog selection refuse the new family because its legacy coordination control is absent. New prepare refuses an existing legacy job. Wrong step/revoke credentials and changed immutable prepare requests leave metadata unchanged. |
| [three-protected-phases-and-discarded-reply](restore-case-retention-review-write-guarded-stage-03-three-protected-phases-and-discarded-reply.json) | Three authenticated steps perform prepared to ready, ready to linked and linked to completed. The actual successful complete prepared response is discarded. Changed IDs in sealed slots and reusing one operation in another phase conflict. The old review reader accepts the completed historical review. |
| [completed-reply-recovery-without-live-targets](restore-case-retention-review-write-guarded-stage-04-completed-reply-recovery-without-live-targets.json) | After completion, the new plan and review target are temporarily absent. The real discarded prepared response is recovered exactly; all three stable saved phase responses remain historical observations. Own show and exact prepare need neither live target. Authentication and operation conflicts are checked without effects. |
| [irreversible-holder-revocation](restore-case-retention-review-write-guarded-stage-05-irreversible-holder-revocation.json) | With both live targets still absent, the holder revokes its own local capability. Own show, exact prepare and exact revoke preserve history and never reactivate it. Revoked access blocks new and cached steps; wrong credentials fail authentication before the missing target is observed. |
| [preserved-history-and-explicit-limits](restore-case-retention-review-write-guarded-stage-06-preserved-history-and-explicit-limits.json) | Both temporary paths are restored. The old review reader still returns the completed record. Six earlier write statuses, two released lease pages, three blocked restore jobs and original lstat control metadata remain unchanged. Source and live public histories retain their original observations; the new job remains completed and revoked. |

## Наблюдаемый результат

Старые публичные step, status, prepare и catalog отказывают при выборе нового семейства до эффекта. Новое prepare отказывает для существующего legacy job. Новые controls не создают старые intent/lock; ключ обязателен для поддерживаемого guarded step.
Настоящий успешный полный ответ prepared отброшен. После ready→linked→completed он точно восстановлен при временно отсутствующих новом плане и review. Три cached ответа сохраняют историческую целевую фазу. Изменённый ID занятого слота и повтор ID в другой фазе конфликтуют.
При тех же отсутствующих двух путях holder отзывает свой локальный доступ. Отозванный ключ блокирует и новый step, и cached protected replay; чужой ключ возвращает credential_invalid. Own show, exact prepare и exact revoke сохраняют историю и не возобновляют доступ. Оба пути восстановлены в finally; старый reader читает завершённый review.

## Публичные основания

- [Новый план](restore-case-retention-review-write-guarded-plan-observations.json), [guard](restore-case-retention-review-write-guarded-guard-observations.json), [три результата и статусы](restore-case-retention-review-write-guarded-execution-observations.json).
- [Отзыв](restore-case-retention-review-write-guarded-revocation-observations.json), [совместимость](restore-case-retention-review-write-guarded-legacy-compatibility.json), [восстановление ответа](restore-case-retention-review-write-guarded-reply-recovery.json).
- [CLI](restore-case-retention-review-write-guarded-cli-outcomes.json), [отказы](restore-case-retention-review-write-guarded-rejections.json), [источник](restore-case-retention-review-write-guarded-source-observations.json), [живая среда](restore-case-retention-review-write-guarded-live-observations.json), [машинный отчёт](restore-case-retention-review-write-guarded-report.json).

## Границы

Шесть этапов, 60 новых ordinary CLI: 36 успешных и 24 ожидаемых отказа. Из них 40 guarded-команд: 23 успешных и 17 отказов. Всего 821 CLI и 14 прежних interrupt workers, 835 дочерних процессов. Новых fault workers или injected partial writes нет.
В runtime: шесть legacy write jobs и один guarded job, девять reviews, пять retention plans, пять write catalogs, пять lease DB и 20 прежних lease событий. Двенадцать прежних action intents сохраняют десять результатов и два pending; семь native permits сохраняют три отзыва; два issuer сохраняют два отзыва и три issuance receipts. Три новых saved step results и один guarded revoke относятся только к новому job.
Bearer подтверждает локальное владение ключом. Владелец файловой системы, private helpers, прямые syscalls и другие копии находятся за пределами gate. Actor/principal не удостоверяют человека или модель; leases и issuer policy для нового семейства не применяются. ОС изоляция и внешнее fencing не установлены.
Effect и запись результата не атомарны. Ветка exact POST без receipt поддерживается исходным кодом, но в данном обычном сценарии не демонстрируется. Revocation не отменяет уже выполненный эффект; исторический review не подтверждает семантическую достоверность содержания или право архивировать/удалять.
Private credentials/hashes, управляющие тела, pins и digests не экспортируются. Драйвер читает только публичные ответы, маркеры ключей и lstat. Ёмкость агентов, одновременность процессов и качество содержания не измерялись.
