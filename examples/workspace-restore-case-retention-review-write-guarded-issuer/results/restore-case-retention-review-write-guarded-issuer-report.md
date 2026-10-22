# Явная политика выдачи нового guarded review job

Время отчёта: 2026-10-22T21:58:23.835268+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [предыдущий guarded сценарий](restore-case-retention-review-write-guarded-report.md); его 467 публичных файлов сохранены.
Добавлены один issuer с точным правилом, одна квитанция выдачи, новый retention plan и guarded job/review, отдельные admin и holder credentials.

## Этапы

| Этап | Наблюдение |
|---|---|
| [exact-policy-and-local-administrator](restore-case-retention-review-write-guarded-issuer-stage-01-exact-policy-and-local-administrator.json) | A fresh retention plan is named by one exact rule for principal, actor and verdict. One separate administrator key creates the issuer. Duplicate rules, wrong administrator and four mismatching scopes fail before target reservation. |
| [admitted-native-guard-and-discarded-reply](restore-case-retention-review-write-guarded-issuer-stage-02-admitted-native-guard-and-discarded-reply.json) | The complete successful managed-issue response is discarded. Own native show and status identify a fresh guarded job and separate holder key. Exact native prepare keeps its old owner path. Administrator and unrelated keys cannot step, a holder cannot issue, and a new request cannot adopt the existing job. |
| [issuer-revoked-holder-completes](restore-case-retention-review-write-guarded-issuer-stage-03-issuer-revoked-holder-completes.json) | Administrator revocation blocks a new issuance before missing target reads. The native holder remains active and completes all three credential-required phases after that revocation. Exact issuer create and revoke retain historical responses; an issuer key does not revoke its child. |
| [historical-issuance-without-live-targets](restore-case-retention-review-write-guarded-issuer-stage-04-historical-issuance-without-live-targets.json) | After native completion and issuer revocation, the actual complete discarded issue response is recovered exactly with the new plan, review and job offline. Changed requests conflict without live reads. Own receipt lookup also works while the issuer is offline; its historical receipt is unchanged. |
| [separate-holder-revocation](restore-case-retention-review-write-guarded-issuer-stage-05-separate-holder-revocation.json) | After all paths return, the child is still completed and active despite issuer revocation. Its own holder then revokes native access, blocking new and cached protected steps. Exact managed issue, own receipt and exact native prepare remain historical and do not reactivate either authority. |
| [preserved-history-and-explicit-limits](restore-case-retention-review-write-guarded-issuer-stage-06-preserved-history-and-explicit-limits.json) | All four new paths return. Earlier six legacy write statuses, the completed revoked guarded job, released lease heads, blocked restore jobs and original lstat metadata remain unchanged. The new review is completed, its holder and issuer are separately revoked, and the immutable issuance remains historical. |

## Наблюдаемый результат

Точная policy сопоставляет principal, actor, verdict и plan_id. Неверный admin, несовпадающие scopes и повторяющиеся rules отклонены до резервирования новых целей. Новая выдача не усыновляет существующий native guard.
Полный успешный issuance-response действительно отброшен. Issuer отозван, после чего holder своим отдельным ключом выполняет три native фазы до completed. Отзыв издателя закрывает будущую выдачу, сохраняя правила уже созданного guard.
При недоступных новых plan, review и job exact admin issue replay восстанавливает прежний ответ после completed и issuer revoke. Own receipt lookup возвращает ту же историю даже при недоступном issuer. Все четыре пути восстановлены в finally.
Затем holder отдельно отзывает native доступ: новый и cached step отказывают. Exact managed issue, own receipt и exact native prepare сохраняют исторические значения и не возобновляют доступ.

## Публичные основания

- [План](restore-case-retention-review-write-guarded-issuer-plan-observations.json), [issuer и policy](restore-case-retention-review-write-guarded-issuer-issuer-observations.json), [issuance](restore-case-retention-review-write-guarded-issuer-issuance-observations.json).
- [Native исполнение и прежние состояния](restore-case-retention-review-write-guarded-issuer-execution-observations.json), [два отдельных отзыва](restore-case-retention-review-write-guarded-issuer-revocation-observations.json), [восстановление ответа](restore-case-retention-review-write-guarded-issuer-reply-recovery.json).
- [CLI](restore-case-retention-review-write-guarded-issuer-cli-outcomes.json), [отказы](restore-case-retention-review-write-guarded-issuer-rejections.json), [источник](restore-case-retention-review-write-guarded-issuer-source-observations.json), [живая среда](restore-case-retention-review-write-guarded-issuer-live-observations.json), [машинный отчёт](restore-case-retention-review-write-guarded-issuer-report.json).

## Границы

Шесть этапов, 80 новых ordinary CLI:47 успешных и33 ожидаемых отказа. Из них41 managed-команда:17 успешных и24 отказа;22 native guarded-команды:17 успешных и5 отказов. Всего901CLI и14 прежних interrupt workers,915 различных PID за последовательность запусков; одновременность и ёмкость не измерены.
Runtime содержит6legacy write jobs и2guarded jobs,10reviews,6retention plans,5write catalogs,5lease DB/20операций. Прежние12actions/10results/2pending,7native permits/3revokes и2oldissuers/2revokes/3issuances сохранены. Новый managed issuer имеет1policy rule,1revoke и1issuance; nativeguarded jobs вместе имеют6phase results и2собственных отзыва. Credential-файлов35, в экспорт они не включены.
Сохранение native Guard11 и issuance16 не атомарно. При отказе второй записи может остаться действующий guard с ранее сохранённым holder key. Outer issue error не доказывает отсутствие capability или эффекта. Этот промежуток не воспроизводился искусственно, усыновление и ремонт не реализованы.
Issuer policy относится к выбранному пути выдачи. Native step не читает её: issuer_policy_enforced=false и lease_enforced=false сохраняются. Владелец файловой системы, private helpers и прямые syscalls остаются вне gate; OS isolation, внешняя identity и fencing отсутствуют.
Credentials, private controls, pins и digests не экспортируются. Структурная корректность квитанций не подтверждает независимость агентов, истинность документации или право публикации/архивирования/удаления. Новые fault workers, partial writes, expiry waits, внешние LLM и исполнение предложенного кода не добавлялись.
