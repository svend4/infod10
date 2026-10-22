# Локальный ключ допуска к точному write action

[Протокол и анализ](../../docs/development/workspace-restore-case-retention-review-write-permit.md) · [читаемый отчёт](results/restore-case-retention-review-write-permit-report.md) · [машинный отчёт](results/restore-case-retention-review-write-permit-report.json).

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-permit-demo \
  --workspace /tmp/agentwiki-review-write-permit-01 --workers 3
~~~

Сценарий готовит новый job, каталог, ledger и три однофазных action. Четыре локальных допуска имеют отдельные private credentials. Неверный ключ и отозванный допуск блокируют защищённое выполнение, включая выдачу уже сохранённого результата. Другой permit для того же action действует независимо; прежний revoked permit сохраняет отказ.

Настоящий ответ finish отбрасывается и восстанавливается точно при действующем permit. Старый action show остаётся историческим чтением. После скрытия источников own permit show/issue replay сохраняют выбранную границу, без восстановления исходных данных.

Ключ допуска подтверждает локальное владение capability. Principal и worker самозаявленные, выдача доверяет private FS owner. Старые APIs остаются доступны отдельно; удостоверение человека, независимость экспертов, внешнее fencing и смысловая истинность здесь не устанавливаются. Private secrets, hashes, controls и DB находятся вне публикации.


## Фактический запуск

Полный development-02 завершён 22 октября 2026 года в 18:19:50 Europe/Berlin. Сохранён 431 публичный файл: 413 JSON и 18 Markdown; все равны результатам запуска. Шесть новых этапов добавили 18 файлов к прежним 413.

В новой части выполнены 85 CLI-команд: 66 успешных и 19 ожидаемых отказов. Из них 50 permit-команд: 34 успешных и 16 отказов. Полный сценарий содержит 672 ordinary CLI и 14 унаследованных interrupt workers, 686 различных дочерних PID. Эти числа описывают конкретный запуск, не измеренную ёмкость LLM.

Четыре допуска, четыре частных credential, три отзыва и три сохранённых результата action. Три фазы довели новый write job до completed; одно replacement-link разрешение осталось active над исторической квитанцией. Новый ledger сохранил только claim/release; protected вызовы не продлевали lease. Прежние истории, controls и blocked jobs сохранились.

Первый development-01 остановился после шести этапов при экспорте source-observations: общий запрет имени token ошибочно отклонил прежнее публичное целочисленное поколение runtime. В новом фильтре убран только запрет общего имени; проверки секретов, хешей и bearer-pattern сохранены. Первый каталог не ремонтировался и не экспортировался. Публикуемые результаты относятся к новому полному development-02.
