# Учёт блокирующих наблюдений и связь с отдельными попытками

Время отчёта: 2026-10-21T22:54:50.048611+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий подготовки](restore-prepare-report.md); его файлы сохранены.
Три блокирующие операции зарегистрированы отдельно. Их plan, owner и назначения не исправляются.
Три новые попытки создаются обычным prepare с явно переданным R4 bundle и новыми именами.

## Этапы

| Этап | Наблюдение |
|---|---|
| [blocked-observations-before-registration](restore-cases-stage-01-blocked-observations-before-registration.json) | Three blocked jobs remain historical evidence; a case is recorded separately and a new attempt will use an explicitly supplied source. |
| [plan-invalid](restore-cases-stage-02-plan-invalid.json) | The immutable blocked observation is linked to a separately prepared attempt; replay preserves the historical receipt while show reports its completed current status. |
| [ownership-unproven](restore-cases-stage-03-ownership-unproven.json) | The immutable blocked observation is linked to a separately prepared attempt; replay preserves the historical receipt while show reports its completed current status. |
| [owner-invalid](restore-cases-stage-04-owner-invalid.json) | The immutable blocked observation is linked to a separately prepared attempt; replay preserves the historical receipt while show reports its completed current status. |
| [historical-evidence-and-completed-reads-preserved](restore-cases-stage-05-historical-evidence-and-completed-reads-preserved.json) | The original three jobs still refuse; completed case/attempt observations work with the source bundle unavailable and all original paths are restored. |

## Что означает связь

Case сохраняет историческое наблюдение и безопасную причину отказа. При недействительном
plan исходная идентичность остаётся неизвестной. Link фиксирует операторскую ассоциацию
с уже существующей новой попыткой. Эта функция не создаёт попытку, не устанавливает
эквивалентность исходного запроса и не восстанавливает прежние полномочия.

Настоящий ответ link проверен и отброшен, затем точный replay восстанавливает ту же квитанцию.
После завершения новой попытки исторический link остаётся прежним; show отдельно показывает
её текущий completed status. Конкурирующие job и operation ID отказывают.

Все новые назначения сохраняют R4, 36 событий и два архива. Карантин блокирует запись и
публикацию. Прежние три jobs остаются неизменными и блокирующими. Completed show/status,
полный inspect и Store читаются при временно недоступном source bundle, возвращённом в finally.

## Публичные основания

- [Cases и ссылки](restore-cases-cases.json), [CLI](restore-cases-cli-outcomes.json), [отказы](restore-cases-rejections.json), [восстановление ответа](restore-cases-reply-recovery.json).
- [Исходная сводка](restore-cases-source-summary.json), [источник](restore-cases-source-observations.json), [живая база](restore-cases-live-observations.json), [старые jobs](restore-cases-original-observations.json).
- [Новые назначения](restore-cases-target-observations.json), [архивы](restore-cases-archive-observations.json), [полные сводки](restore-cases-completed-summaries.json), [машинный отчёт](restore-cases-report.json).

## Границы результата

Этапов: 5; cases: 3; новых обычных CLI: 50; отказов: 17.
Новых worker-прерываний нет. Частичные записи case/link, изменённые или недоступные старые
jobs, аппаратные отказы, все расписания и предельная нагрузка не воспроизводились.
Частные контрольные файлы и staging не разбираются; публикуются только проверенные API/Store
ответы и ограниченные булевы наблюдения. Сравнение исключает лишь верхний governance.observed_at.
Адресованные доказательства, timestamps и expired-признаки сохраняются. Внешние LLM,
независимая экспертиза, истинность содержания и внешний fencing не установлены.
