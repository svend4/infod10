# Историческое рассмотрение плана и отдельная проверка текущего основания

Время отчёта: 2026-10-21T22:55:13.893172+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий retention](restore-case-retention-report.md); его 323 публичных файла сохранены.
Два моделируемых участника записывают самозаявленные verdict для целого исторического плана. Аутентификация и разрешение действий отсутствуют.

## Этапы

| Этап | Наблюдение |
|---|---|
| [review-preserves-history-without-authority](restore-case-retention-review-stage-01-review-preserves-history-without-authority.json) | A simulated self-declared actor records reviewed for the whole minimum plan; exact replay recovers a discarded response, while separate revalidation observes unchanged basis. |
| [restored-path-does-not-rewrite-earlier-basis](restore-case-retention-review-stage-02-restored-path-does-not-rewrite-earlier-basis.json) | A second simulated actor requests a fresh plan: a historically unavailable attempt is currently available and potentially a candidate, but the earlier observed basis remains unverifiable. |
| [current-member-availability-is-separate](restore-case-retention-review-stage-03-current-member-availability-is-separate.json) | Unavailable attempt and unavailable case produce distinct current holds and false comparison booleans; every path is restored and the historical review stays fixed. |
| [binding-checks-survive-cached-member-observation](restore-case-retention-review-stage-04-binding-checks-survive-cached-member-observation.json) | Missing catalog, missing plan and a different valid plan directory give explicit binding failures while cached members remain independently observable; historical show/replay still return the saved review. |
| [historical-review-outlives-all-live-inputs](restore-case-retention-review-stage-05-historical-review-outlives-all-live-inputs.json) | With fourteen live paths temporarily absent, both shows and exact replay remain historical; separate revalidation reports missing bindings, unavailable cases and false comparisons. Every path returns in finally. |
| [reviews-do-not-authorize-domain-actions](restore-case-retention-review-stage-06-reviews-do-not-authorize-domain-actions.json) | Invalid or conflicting requests refuse safely; saved reviews, source plans, catalog pages, case details, blocked jobs and source/live history remain unchanged, with no archive or deletion. |

## История и текущее основание

Show и точный replay возвращают сохранённое рассмотрение, включая весь прежний plan. Настоящий успешный ответ record был проверен, отброшен и восстановлен точным повтором.
Revalidate отдельно получает checked_at, проверяет plan/catalog bindings, наблюдает сохранённый состав случаев и пересчитывает текущие причины по новому времени.
Для minimum исходные строки и текущие observations совпадают; неизвестные исходные identity сохраняют held. Совпадение основания не разрешает действий.
Для исторического missing_attempt восстановленная попытка теперь доступна, а строка становится кандидатом. Прежнее unavailable не становится проверенным задним числом: все три сравнения этой строки ложны.

Отдельно воспроизведены отсутствие attempt, case, catalog и plan, а также подмена привязки plan другим существующим каталогом без редактирования его файлов.
Когда отсутствуют только plan/catalog, сохранённый состав случаев всё ещё наблюдается; общий observed_basis_unchanged остаётся false.
При отсутствии всех 14 live paths исторические show/replay доступны, а отдельный revalidate сообщает unavailable для обеих привязок и всех случаев.
Все пути возвращены в finally. Старые blocked jobs не перемещались; исходные plans, reviews, catalog pages, case details и R4/R5 история сохранены.

## Публичные основания

- [Рассмотрения](restore-case-retention-review-records.json), [15 текущих наблюдений](restore-case-retention-review-revalidations.json), [сравнения](restore-case-retention-review-comparisons.json).
- [CLI](restore-case-retention-review-cli-outcomes.json), [отказы](restore-case-retention-review-rejections.json), [восстановление ответа](restore-case-retention-review-reply-recovery.json).
- [Сохранность controls](restore-case-retention-review-control-observations.json), [case details](restore-case-retention-review-restored-detail-observations.json).
- [Источник](restore-case-retention-review-source-observations.json), [живая среда](restore-case-retention-review-live-observations.json), [машинный отчёт](restore-case-retention-review-report.json).

## Границы результата

Шесть этапов, 45 новых CLI: 34 успешных и 11 ожидаемых отказов. Всего с повторённым baseline: 313 CLI и 14 прежних interrupt workers, 327 дочерних процессов.
Новых прерываний, restore attempts, credentials, domain decisions, операций архивации и удаления нет.
Частные control records и staging демонстратор не разбирает. Публикуются безопасные API/Store ответы, счётчики и булевы наблюдения.
Самозаявленные actor/verdict не устанавливают личность, человеческое рассмотрение, независимость участников или смысловую истинность содержания.
Последовательный revalidate не является общей транзакцией и не обещает будущей неизменности. Частичные записи, новые отказы хранения, все расписания и предел нагрузки не воспроизводились.
