# Допуск к точному действию по локальному bearer и необратимый отзыв

Время отчёта: 2026-10-22T18:19:50.566823+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий долговечных действий](restore-case-retention-review-write-action-report.md); его 413 публичных файлов сохранены.
Добавлены пятый write job, седьмой review, четвёртые каталог и ledger. Три новых action проходят prepared→ready→linked→completed с одной captured арендой; direct step для них не используется.

## Этапы

| Этап | Наблюдение |
|---|---|
| [exact-action-and-private-bearer-scope](restore-case-retention-review-write-permit-stage-01-exact-action-and-private-bearer-scope.json) | A fifth write job has an explicit catalog, one lease claim and one prepared-to-ready action. A private bearer permit captures exactly that action; exact issue replay returns its fixed grant and show observes an active permit without authenticating a holder. |
| [authenticated-delivery-and-one-saved-effect](restore-case-retention-review-write-permit-stage-02-authenticated-delivery-and-one-saved-effect.json) | Wrong credentials refuse execute and revoke. Two ordinary protected execute processes are launched before collection for one permit and return the same complete response; the write reaches only ready. The response verifies current local capability delivery and explicitly declines historical effect authentication. |
| [revocation-blocks-protected-historical-replay](restore-case-retention-review-write-permit-stage-03-revocation-blocks-protected-historical-replay.json) | The holder irreversibly revokes the fill permit. Protected replay refuses even though the result exists. Old action show/execute still return historical evidence; exact revoke replay is fixed and exact issue replay does not reactivate the permit. |
| [independent-replacement-does-not-revive-revocation](restore-case-retention-review-write-permit-stage-04-independent-replacement-does-not-revive-revocation.json) | A second permit is revoked before its link action runs, leaving the write ready. A new independent permit for the same exact action performs ready-to-linked. The original permit still refuses; replacement does not renew or reactivate it and its principal remains a self-declared label. |
| [whole-protected-reply-recovery-and-offline-revocation](restore-case-retention-review-write-permit-stage-05-whole-protected-reply-recovery-and-offline-revocation.json) | Finish completes with its captured catalog absent; the actual complete protected reply is discarded and recovered exactly. After lease release and hiding four bindings, active replacement replay checks its bearer and returns saved history. Hiding all three actions raises the total to seven absent paths: own-only show/issue replay and finish revoke still work, while revoked protected execute refuses before missing-action access. All paths return in finally. |
| [exact-inputs-and-unchanged-domain-history](restore-case-retention-review-write-permit-stage-06-exact-inputs-and-unchanged-domain-history.json) | Changed exact requests and malformed identifiers refuse without creating new permits. Four earlier write jobs, earlier released lease heads and three blocked restore jobs preserve their observations. Four grants retain three irreversible revocations; three fresh actions retain exactly three performed phases and the replacement permit stays active over historical evidence. |

## Наблюдаемый результат

Четыре private bearer разрешения привязаны к трём точным действиям. Чужой credential отказывает. Два ordinary protected execute для fill возвращают одинаковый полный ответ, а job достигает только ready. Перекрытие выполнения и ёмкость не измерялись.
Три разрешения отозваны: fill после эффекта, первое link до эффекта, finish после сохранения результата. Второе независимое link разрешение остаётся active над историческим результатом; прежнее отозванное разрешение не оживает.
Protected execute проверяет credential и текущий отзыв до чтения cached action result. Поэтому отозванный permit не возвращает защищённый повтор, хотя прежний action show/execute по-прежнему возвращает историческую квитанцию. Exact issue replay не меняет отзыв.
Настоящий полный ответ finish отброшен и точно восстановлен при недоступном исходном каталоге. После release активный protected replay с четырьмя отсутствующими bindings читает сохранённый результат и заново проверяет bearer. При семи отсутствующих путях own-only permit show, exact issue replay и revoke работают, а revoked execute отказывает до доступа к action. Все пути возвращены.
Поле historical_effect_authenticated=false сохраняется во всех защищённых ответах. Проверка права получить ответ сейчас не устанавливает автора уже совершённого эффекта; principal остаётся самозаявленным.

## Публичные основания

- [Четыре grants](restore-case-retention-review-write-permit-grants.json), [три отзыва](restore-case-retention-review-write-permit-revocations.json), [три действия и результата](restore-case-retention-review-write-permit-action-observations.json).
- [Фазы, разрешения и offline наблюдения](restore-case-retention-review-write-permit-execution-observations.json), [аренда](restore-case-retention-review-write-permit-lease-observations.json).
- [CLI](restore-case-retention-review-write-permit-cli-outcomes.json), [отказы](restore-case-retention-review-write-permit-rejections.json), [восстановление ответа](restore-case-retention-review-write-permit-reply-recovery.json).
- [Источник](restore-case-retention-review-write-permit-source-observations.json), [живая среда](restore-case-retention-review-write-permit-live-observations.json), [машинный отчёт](restore-case-retention-review-write-permit-report.json).

## Границы

Шесть этапов, 85 новых CLI: 66 успешных и 19 ожидаемых отказов. Из них 50 permit команд: 34 успешных и 16 отказов. С основанием 672 ordinary CLI и 14 прежних interrupt workers, всего 686 дочерних процессов.
Всего девять action intents, семь saved results и два прежних pending intents; пять write jobs, семь reviews, четыре каталога, четыре lease DB и 18 lease событий. Новая аренда содержит только claim и release; проверки permit её не продлевают.
Один локальный bearer исполняет и отзывает только своё разрешение; срока истечения нет. Файловый владелец выдаёт разрешения. Старые action API, direct step, прямой SQL и файловый владелец остаются за этой границей; общего SQL/filesystem commit, внешнего fencing или проверки личности нет.
Private credentials и их hashes, управляющие записи, pins и digests не экспортируются. Новых fault workers, injected partial writes, ожидания expiry, внешних LLM либо запуска предложенного кода нет; смысловая достоверность документации этой демонстрацией не устанавливается.
