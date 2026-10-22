# Локальная политика выдачи и неизменяемые квитанции permit

Время отчёта: 2026-10-22T22:37:34.600279+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий bearer permits](restore-case-retention-review-write-permit-report.md); его 431 публичный файл сохранён.
Добавлены шестой write job, восьмой review, пятые каталог и ledger. Три точных action выполняют prepared→ready→linked→completed через новые native permits.

## Этапы

| Этап | Наблюдение |
|---|---|
| [exact-issuer-policy-and-separate-keys](restore-case-retention-review-write-issuer-stage-01-exact-issuer-policy-and-separate-keys.json) | A sixth write job has an explicit catalog and one claimed lease. Two owner-created local issuers have separate administrator credentials and immutable exact rules; the main policy covers three phases, while the other names a different worker. No person or model identity is established. |
| [credential-and-policy-gated-fill](restore-case-retention-review-write-issuer-stage-02-credential-and-policy-gated-fill.json) | Wrong administrator credentials, an unlisted principal and a nonmatching worker policy refuse before new child directories are reserved. The holder credential does not administer issuance. A permitted fill creates a fresh native permit and a separate durable issuance receipt, then native protected execute reaches only ready. |
| [durable-issuance-reply-recovery-and-no-adoption](restore-case-retention-review-write-issuer-stage-03-durable-issuance-reply-recovery-and-no-adoption.json) | The actual complete link issuance response is discarded after success and recovered exactly from its completed receipt. Changed holder credentials, principal, operation, action, permit or issuer conflict. A fresh issuance cannot adopt an already existing native permit. Native protected execution then performs only ready-to-linked. |
| [revocation-stops-new-issuance-preserves-native-permits](restore-case-retention-review-write-issuer-stage-04-revocation-stops-new-issuance-preserves-native-permits.json) | Both issuers are irreversibly revoked. New issuance refuses, while exact historical issuance replay remains fixed after current administrator authentication. Exact create/revoke replay never reactivates the issuer. The previously issued finish permit still performs linked-to-completed with its own holder credential. |
| [historical-receipts-with-eleven-bindings-absent](restore-case-retention-review-write-issuer-stage-05-historical-receipts-with-eleven-bindings-absent.json) | After lease release, an existing native permit still returns its cached protected finish result while four live bindings are absent. With three native permits and three actions also absent, exact completed issuance replay uses the live revoked issuer and administrator credential. Hiding the issuer makes eleven absent paths; own receipt lookup still reads only its controls. Every path returns in finally. |
| [exact-inputs-and-unchanged-domain-history](restore-case-retention-review-write-issuer-stage-06-exact-inputs-and-unchanged-domain-history.json) | Duplicate rule IDs and duplicate exact scopes refuse without reserving an issuer. Another revocation operation conflicts. Five earlier write jobs, released lease heads and three blocked restore jobs preserve their observations. Two issuers retain revocation, three durable issuance receipts and three active native permits retain history, and the new write remains completed. |

## Наблюдаемый результат

Два локальных издателя имеют отдельные ключи администратора и четыре точных policy rules. Чужой credential, незаявленный principal и несовпадающий worker блокируют новую выдачу без резервирования child directories. Holder credential не является ключом издателя.
Три разрешённые выдачи сохраняют три новых native permits и три отдельные issuance receipts. Настоящий полный ответ выдачи link отброшен и точно восстановлен. Изменённые точные запросы конфликтуют; существующий native permit не усыновляется новой выдачей.
Оба издателя отозваны. Новые выдачи блокируются; точный исторический replay после проверки исходного ключа остаётся доступен и не оживляет издателя. Уже выданный finish permit после отзыва издателя завершает фазу linked→completed со своим holder credential.
После release и скрытия четырёх live bindings cached native finish replay сохранился. С десятью отсутствующими bindings точные issue replays читают исторические receipts при живом отозванном issuer. Скрытие issuer доводит число путей до одиннадцати; own-only receipt lookup работает без issuer/native/action. Все пути возвращены.
Публичная issuance receipt фиксирует локальное разрешение выдачи по исторической политике. Она не подтверждает текущее право новой выдачи, действующий child permit, личность человека, автора эффекта либо смысловую достоверность документа.

## Публичные основания

- [Издатели, политики и отзывы](restore-case-retention-review-write-issuer-issuers.json), [три квитанции выдачи](restore-case-retention-review-write-issuer-issuances.json).
- [Три действия и результата](restore-case-retention-review-write-issuer-action-observations.json), [исполнение и offline наблюдения](restore-case-retention-review-write-issuer-execution-observations.json), [аренда](restore-case-retention-review-write-issuer-lease-observations.json).
- [CLI](restore-case-retention-review-write-issuer-cli-outcomes.json), [отказы](restore-case-retention-review-write-issuer-rejections.json), [восстановление ответа](restore-case-retention-review-write-issuer-reply-recovery.json).
- [Источник](restore-case-retention-review-write-issuer-source-observations.json), [живая среда](restore-case-retention-review-write-issuer-live-observations.json), [машинный отчёт](restore-case-retention-review-write-issuer-report.json).

## Границы

Шесть этапов, 89 новых CLI: 69 успешных и 20 ожидаемых отказов. Из них 50 issuer-команд: 33 успешных и 17 отказов. С основанием 761 ordinary CLI и 14 прежних interrupt workers, всего 775 дочерних процессов.
Всего двенадцать action intents, десять saved results и два прежних pending intents; семь native permits с тремя прежними отзывами; шесть write jobs, восемь reviews, пять каталогов, пять lease DB и 20 lease событий. Выдача не исполняет action и не продлевает claim.
Native grant и отдельная issuance receipt не образуют атомарную транзакцию каталогов. Неполные controls не ремонтируются; окно до receipt не выдаётся за проверенное восстановление после прерывания. Старые APIs и файловый владелец остаются за новой политикой.
Private credentials/hashes, управляющие записи, pins и digests не экспортируются. Новых fault workers, injected partial writes, ожидания expiry, LLM либо исполнения предложенного кода нет. Ёмкость агентов и качество содержания не измерялись.
