# Явная политика локальной выдачи permits

[Протокол и анализ](../../docs/development/workspace-restore-case-retention-review-write-issuer.md) · [читаемый отчёт](results/restore-case-retention-review-write-issuer-report.md) · [машинный отчёт](results/restore-case-retention-review-write-issuer-report.json).

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer-demo \
  --workspace /tmp/agentwiki-review-write-issuer-01 --workers 3
~~~

Издатель хранит явные правила principal/worker/ledger/index/phase. Отдельный admin credential допускает новую выдачу; holder получает самостоятельный nativepermit. Каждая поддержанная выдача сохраняет immutable receipt. Отзыв issuer останавливает новые выдачи, сохраняя прежние permits и исторический replay.

Nativegrant и issuance receipt записываются последовательно под локальными locks, без общей транзакции. Частичные остатки не ремонтируются и существующие permits не усыновляются. Метки участников не удостоверяют человека либо независимую модель; старая API-граница и файловый владелец сохраняются. Secret credentials, hashes, pins, управляющие records и SQLite находятся вне публикации.


## Фактический запуск

Полный development-01 завершён с первой попытки 22 октября 2026 года в 19:10:45 Europe/Berlin. Сохранены 449 публичных файлов: 430 JSON и 19 Markdown; все побайтно равны результатам запуска. Шесть новых этапов, десять data JSON и два отчёта добавили 18 файлов к прежним 431.

Новая часть выполнила 89 CLI-команд: 69 успешных и 20 ожидаемых отказов. Среди них 50 issuer-команд: 33 успешных и 17 отказов. Полная заново выполненная цепочка содержит 761 ordinary CLI и 14 прежних interrupt workers, 775 различных дочерних PID. Это учёт конкретного сценария; предельная ёмкость агентов и пропускная способность не измерялись.

Два issuer с четырьмя точными правилами и отдельными admin credentials выдали три native permits и три issuance receipts. После отзыва обоих issuer новая выдача блокируется. Исторические квитанции повторяются при правильном исходном запросе, а ранее выданный finish permit со своим holder credential завершает работу. Все три native permits остались active по собственным правилам.

Настоящий полный ответ выдачи link отброшен и восстановлен точно без новых controls. Новая выдача не усыновляет существующий native permit. С десятью отсутствующими bindings exact replay требует доступного issuer и ключа; с одиннадцатью own receipt lookup читает только собственные controls. Все пути возвращены в finally. Прежние source R4/36 и live R5/59, карантин и blocked jobs сохранились.

Новых fault workers, injected partial writes, expiry waits, внешних LLM и исполнения предложенного кода нет. Общая транзакция native grant и issuance receipt не реализована; частичные остатки не ремонтируются.


После завершения выполнены 12 read-only аудитов; все прошли при первом исполнении. Подтверждены схемы, anchors, ключи и правила выдачи, неизменность private bytes/metadata при чтении, сохранность 5627 прежних защищённых файлов и отсутствие проверенных секретов/хешей в 6244 файлах публикации. Предыдущие 40 модулей и десять SCHEMA сохранены. Подробности и границы проверок приведены в [руководстве](../../docs/development/workspace-restore-case-retention-review-write-issuer.md) и [журнале разработки](../../docs/development/activity.md).
