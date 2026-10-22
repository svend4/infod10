# Издатель guarded jobs: прикладной сценарий

Тридцать девятый этап agentwiki. [Протокол, теория и границы](../../docs/development/workspace-restore-case-retention-review-write-guarded-issuer.md).

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded-issuer-demo --workspace /tmp/agentwiki-review-write-guarded-issuer-01 --workers 3
~~~

Сценарий продолжает прежний guarded demo в новом private workspace. Издатель проверяет отдельный admin key и точную policy по principal, actor, verdict и plan_id; создаёт один новый native guarded job с отдельным holder key. Отзыв издателя прекращает будущую выдачу; holder самостоятельно управляет отзывом созданного guard.

[Отчёт обычного запуска](results/restore-case-retention-review-write-guarded-issuer-report.md) и публичные JSON сохранены после завершения. Credentials, issuer/native controls, базы, pins и digests остаются вне Git. Driver использует публичные ответы, credential markers и lstat metadata.

Guard сохраняется перед issuance receipt; ошибка внешнего issue может оставить действующее задание. Единой транзакции, ремонта bootstrap, OS isolation и внешней identity нет. Native issuer_policy_enforced=false сохраняется: историческая выдача не добавляет проверки issuer при каждом step. Допустимость создания review не определяет истинность текста или разрешение публикации документов.


## Выполненный обычный запуск

Завершён guarded-issuer-development-01, 2026-10-22T21:58:23.835268+02:00, Europe/Berlin, Python3.12.14. Экспорт содержит 485 файлов:464 JSON и21 Markdown, из них18 новых. Отчёт содержит123 summary-поля и6 полных этапов.

| Наблюдение | Фактический результат |
|---|---|
| Новые обычные CLI |80:47 успешных,33 ожидаемых отказа |
| Managed issuer CLI |41:17 успешных,24 отказа |
| Native guarded CLI |22:17 успешных,5 отказов |
| Полная последовательность |901 CLI и14 прежних interrupt workers,915 разных PID |
| Новая выдача | Один issuer, одно правило, одна issuance receipt и один native guarded job |
| Отзыв издателя | Запретил новую выдачу; holder выполнил все три фазы после отзыва |
| Потерянный ответ выдачи | Восстановлен после completed и issuer revoke без plan/review/job |
| Собственная квитанция | Прочитана также при недоступном issuer |
| Отзыв holder | Отдельно закрыл новые и cached protected steps завершённого child |
| Новые частные ключи | Два;35 credential-файлов в текущем workspace |

Первый обычный запуск завершился без исправлений продукта или данных. Все четыре временно скрытых новых пути восстановлены в finally. Полный публичный JSON-ответ сравнивался по canonical digest в памяти; отпечаток не экспортирован. Число PID описывает последовательность процессов, измерения одновременности и пропускной способности не выполнялись. Нового fault injection, ожиданий expiry и воспроизведения окна между native guard и receipt не было.


## Завершающие проверки

Все 16 read-only аудитов прошли с первой попытки. Новый private audit сопоставил Issuer12/Rule5/Revoke7/Issuance16/Request7/Anchor4 с полным native Guard11, вложенным intent13, plan14/review14, тремя phase results и отдельным holder revoke. Проверены ключи, полные pins/digests, exact known plan reconstruction и неизменность metadata/bytes; старые intent/lock и staging нового job отсутствуют.

Новый focused public review сверил все 123 поля summary, 80 ordered CLI outcomes и шесть полных этапов. Вместе с пятью прежними scoped reviews выполнены 808273 logical/shape и485 byte comparisons, всего808758. Это структурные проверки данных, их число не обозначает независимые смысловые экспертизы. Full schema audit отдельно сопоставил все485 файлов и915 фактических PID.

Privacy сохранил6557 прежних материалов и46 frozen модулей. Известные561 credential и435 private hashes не обнаружены в7218 файлах публикации. Подробные результаты и границы находятся в [журнале разработки](../../docs/development/activity.md).
