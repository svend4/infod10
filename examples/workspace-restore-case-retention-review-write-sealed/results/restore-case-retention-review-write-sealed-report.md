# Одна sealed запись для review write и основания выдачи

Время отчёта: 2026-10-22T22:38:30.645362+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [предыдущий сценарий managed issuer](restore-case-retention-review-write-guarded-issuer-report.md); его 485 публичных файлов сохранены.
Добавлены один родительский issuer с точным правилом, новый retention plan и sealed job/review, отдельные admin и holder credentials. Полный логический Guard11 и основания выдачи находятся в одной immutable Seal14; отдельные native guard и issuance directory не создаются.

## Этапы

| Этап | Наблюдение |
|---|---|
| [exact-parent-policy](restore-case-retention-review-write-sealed-stage-01-exact-parent-policy.json) | A fresh retention plan supplies one exact rule for the reused parent issuer. Wrong administrator and four mismatching scopes refuse before any sealed job or review reservation. Parent policy remains local and owner-trusted. |
| [sealed-family-and-legacy-refusals](restore-case-retention-review-write-sealed-stage-02-sealed-family-and-legacy-refusals.json) | The complete successful sealed issue response is discarded. Own show exposes one receipt and its captured guard. Legacy write and native guarded APIs refuse this family; old managed issue cannot adopt it and sealed issue cannot adopt an old native job. Administrator and unrelated keys cannot step. |
| [parent-revoked-sealed-holder-completes](restore-case-retention-review-write-sealed-stage-03-parent-revoked-sealed-holder-completes.json) | Parent administrator revocation blocks new sealed issuance before missing target reads. The independent holder completes all three sealed phases after that revocation. Sealed result slots reject changed IDs and cross-phase reuse, and the old review reader accepts completed history. |
| [own-seal-recovery-without-live-targets](restore-case-retention-review-write-sealed-stage-04-own-seal-recovery-without-live-targets.json) | After completion and parent revocation, exact issue recovers the complete discarded response with plan and review offline. The parent is then also absent; own receipt, own show and all three cached steps work from the still present sealed job. Wrong holder keys fail before live reads. |
| [holder-revocation-with-offline-issuer](restore-case-retention-review-write-sealed-stage-05-holder-revocation-with-offline-issuer.json) | With the parent, plan and review absent, the holder separately revokes this sealed job. New and cached protected steps refuse, wrong keys fail authentication, and own historical receipt stays unchanged. Exact revoke does not rewrite either record. |
| [restored-history-and-one-record-boundary](restore-case-retention-review-write-sealed-stage-06-restored-history-and-one-record-boundary.json) | All three temporary paths return. Six legacy write statuses, both old native guarded jobs, released lease pages, blocked restore jobs and original lstat metadata remain unchanged. The new sealed review is completed, parent and holder are separately revoked, and every own receipt remains the same historical publication. |

## Наблюдаемый результат

Неверный admin и четыре несовпадающих scope отклонены до резервирования job/review. Прежние legacy и native guarded prepare/status/step отвергают sealed family. Старый managed issue не усыновляет sealed job и не резервирует отдельную issuance directory; sealed issue не усыновляет прежний native guarded job.
Полный успешный issue-response действительно отброшен. Родительский issuer отозван до всех трёх новых шагов; holder своим отдельным ключом доводит sealed review до completed. Отзыв родителя закрывает будущую выдачу, сохраняя уже выданное право holder.
При недоступных plan и review exact admin issue replay восстанавливает целый прежний ответ после completed и parent revoke. Затем скрывается также parent: own receipt, own show и три cached step возвращают прежнюю историю из доступного собственного sealed job. Неверные holder keys отвергаются.
Пока все три referenced пути отсутствуют, holder отзывает sealed доступ: новые и cached steps отказывают, exact revoke повторяет прежний ответ, own receipt сохраняет исторические основания. Все три пути восстанавливаются в finally. Exact issue после holder revoke не возобновляет доступ.

## Публичные основания

- [План](restore-case-retention-review-write-sealed-plan-observations.json), [родитель и policy](restore-case-retention-review-write-sealed-issuer-observations.json), [seal и восстановление](restore-case-retention-review-write-sealed-seal-observations.json).
- [Исполнение и прежние состояния](restore-case-retention-review-write-sealed-execution-observations.json), [два отзыва](restore-case-retention-review-write-sealed-revocation-observations.json), [восстановление целого ответа](restore-case-retention-review-write-sealed-reply-recovery.json).
- [CLI](restore-case-retention-review-write-sealed-cli-outcomes.json), [отказы](restore-case-retention-review-write-sealed-rejections.json), [источник](restore-case-retention-review-write-sealed-source-observations.json), [живая среда](restore-case-retention-review-write-sealed-live-observations.json), [машинный отчёт](restore-case-retention-review-write-sealed-report.json).

## Границы

Шесть этапов, 92 новых ordinary CLI: 52 успешных и 40 ожидаемых отказов. Среди них 57 sealed команд (29/28) и 11 команд прежнего parent issuer (8/3). Итого 993 CLI и 14 прежних interrupt workers, 1007 различных PID за последовательность запусков; одновременность и ёмкость не измерены. Публичный набор: 503 файла, 481 JSON и 22 Markdown.
Runtime содержит 6 legacy write jobs, 2 прежних native guarded jobs и 1 sealed job; всего 9 логических write jobs, 11 reviews, 7 retention plans. Прежние 5 write catalogs, 5 lease DB/20 операций, 12 actions/10 results/2 pending, 7 native permits/3 revokes и 2 old issuers/2 revokes/3 issuances сохранены. Managed parent issuers теперь 2 с 2 отзывами; отдельная прежняя issuance остаётся одна. Новый seal содержит третий логический guard, 3 новых phase results и 1 holder revoke; прежние native guards сохраняют 6 results и 2 revokes. Credential-файлов 37, в экспорт они не включены.
В новом семействе отсутствует промежуточный отдельно опубликованный native guard без issuance evidence. Partial/absent seal закрыт для supported step; этот путь проверен по исходникам и искусственно не воспроизводился. Ошибка после полного commit может оставить действующий sealed job с основаниями выдачи. O_EXCL и fsync не доказывают rollback, междиректорную атомарность и все power-loss сценарии. Прежние форматы и их известный промежуток не обновлены.
Exact issue replay требует доступного parent и admin key; own receipt/show/cached steps требуют доступного собственного job. Parent, plan и review могут отсутствовать в проверенных own-only операциях. FS owner, private helpers и прямые syscalls остаются вне gate; внешняя identity, смысловое принятие, OS isolation, lease enforcement и fencing отсутствуют.
Driver читает публичные ответы, credential markers и lstat; private controls, lease SQL и staging bodies не читает. Credentials, pins и digests не экспортируются. Новые fault workers, partial writes, expiry waits, pending lifecycle, внешние LLM и исполнение предложенного кода не добавлялись.
