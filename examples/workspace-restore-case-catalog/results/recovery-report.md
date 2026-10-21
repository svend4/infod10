# Выполненная сверка снимка с действующим источником

Время отчёта: 2026-10-21T19:55:30.747284+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала обычный [сценарий backup/restore](report.md) создаёт вымышленный документ и код,
принимает ревизию 2 и сохраняет частный карантинный снимок с 52 событиями. Источник продолжает
работу до 59 событий. Существующие публичные результаты этого запуска сохранены без перезаписи.

## Последовательность

| Этап | Наблюдение | Событий источника |
|---|---|---:|
| [continuing-source-and-historical-copy](recovery-stage-01-continuing-source-and-historical-copy.json) | The live source has continued after capture; its old snapshot stays quarantined and readable. | 59 |
| [fresh-live-author-lease](recovery-stage-02-fresh-live-author-lease.json) | A new authenticated task and lease exist only in the continuing source, not in the historical copy. | 61 |
| [initial-plan-observed-current](recovery-stage-03-initial-plan-observed-current.json) | Live administrative authority creates a private plan; a separate process confirms unchanged rows at observation. | 61 |
| [eventless-heartbeat-invalidates-plan](recovery-stage-04-eventless-heartbeat-invalidates-plan.json) | An ordinary heartbeat changes lease_until without changing runtime revision or events; full-row revalidation rejects the old plan. | 61 |
| [fresh-plan-after-heartbeat](recovery-stage-05-fresh-plan-after-heartbeat.json) | A new private plan describes the updated lease and can be revalidated in a later process. | 61 |
| [later-event-invalidates-plan](recovery-stage-06-later-event-invalidates-plan.json) | An ordinary task enqueue adds an event without an accepted revision; the previous plan is stale again. | 62 |
| [final-current-observation](recovery-stage-07-final-current-observation.json) | A third private plan includes the later event; revalidation remains advisory and does not activate the copy. | 62 |
| [safe-operator-rejections](recovery-stage-08-safe-operator-rejections.json) | Revoked authority, a quarantined live source, an existing plan and an unrelated authorized source are rejected without raw SQL corruption. | 62 |

## Полномочия и происхождение

Assess проверяет готовый bundle и действующий источник с текущим административным credential.
Исходный административный grant должен сохраниться в снимке. Старый префикс событий и строки
неизменяемых таблиц сравниваются точно. Изменяемые таблицы получают безопасные сводки различий.
Частный план содержит внутренние отпечатки строк; публичный результат показывает случайный plan ID,
счётчики и различия. План не является разрешением записи, активации или внешним заверением истории.

## Почему числа событий недостаточно

В живом источнике создаётся новая авторская аренда. Первый assess и отдельный revalidate завершаются.
Обычный heartbeat затем меняет lease_until: runtime revision, число событий и все их тела остаются
прежними. Повторная проверка первого плана возвращает recovery_plan_stale. Новый план снова проходит
проверку. Позднее обычное enqueue добавляет одно событие при той же ревизии и инвалидирует второй план.
Третий план учитывает оба изменения. Эти действия выполняются без сырого изменения SQL и без аварии.

## Сохранённые основания

- [Три оценки](recovery-assessments.json), [три успешных повторных проверки](recovery-revalidations.json).
- [Heartbeat до и после](recovery-heartbeat.json), [исходы CLI](recovery-cli-outcomes.json), [отказы](recovery-rejections.json).
- [Наблюдения до и после read API](recovery-read-observations.json), [начальные состояния](recovery-baseline-observations.json).
- [Конечные состояния](recovery-final-observations.json), [события](recovery-events.json), [задачи](recovery-tasks.json).
- [Неизменившаяся историческая копия](recovery-snapshot-unchanged.json), [машинный отчёт](recovery-report.json).

## Граница результата

Новых этапов: 8; новых CLI-процессов: 8; планов: 3.
Событий копии: 52; событий источника в конце: 62.
Отозванный reviewer, карантинный источник, существующий файл плана и отдельный источник с иным
административным grant получили ожидаемые отказы. Существующий план не был заменён.

Копия остаётся inspection-only. Assess и revalidate не устанавливают запрет записи в живом источнике,
не возвращают старые полномочия и не создают активный клон. После наблюдения источник может снова
измениться; время действия grant или lease может истечь без изменения SQL. Полное переключение
требует отдельного протокола fencing и актуальных полномочий.

Сравнение публичных наблюдений исключает только верхнее поле governance.observed_at, которое
заново вычисляется при каждом чтении. Исходные временные отметки сохранены; адресованные тела
пакетов, решений и квитанций не изменяются. Вычисляемые признаки expired остаются в сравнении.
Это сверка публичных наблюдений, без утверждения о побайтной неизменности всех частных SQL-строк.

Private plans имеют права 0600, их каталог — 0700. Демонстратор не читает их содержимое и не
публикует отпечатки, credentials, manifest или SQLite. Все роли локальные; внешних LLM, человеческой
экспертизы и измерения предельной нагрузки нет. Предложенный код не исполняется.
