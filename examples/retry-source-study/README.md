# Реальные объявления Retry и граница структурного покрытия

Сорок третий этап agentwiki: [подробный анализ](../../docs/development/retry-source-study.md), [новый корпус](../../corpora/urllib3-retry-source-study-v2.5.0/README.md).

~~~sh
python3 -m agentwiki retry-source-study-demo --workspace /tmp/agentwiki-retry-source-study-01
~~~

Сценарий использует четыре реальных объявления закреплённого urllib3 Retry 2.5.0 и прежние анализаторы без расширения грамматики. Явный авторский регистр показывает ещё четыре формы, исключённые до анализа. Старые корпуса и сохранённые примеры не изменяются.

[Отчёт выполненного запуска](results/report.md), [машинная сводка](results/report.json), полные source/coverage reports, новые inventories и captured inputs сохранены: 29 файлов. Запуск 2026-10-23T17:38:41.838149+02:00 (Europe/Berlin) завершился с exit 0; 18 CLI дали 17 success и один ожидаемый отказ перезаписи. Три read-only аудита прошли.

В шести авторских случаях 25 promise rows, из них 22 covered и matching; три случая current_for_human_review, три blocked. Намеренная ошибка единицы total при том же JSON10 действительно получила 4/4 coverage с prose_semantics_verified=false. Структурное покрытие не устанавливает правильность этой фразы или полноту документации. Исторический show прошёл без каталогов inputs и source-reports с последующим восстановлением обоих; baseline report не изменился.

Human review обязателен; внешние LLM, Store writes и права принятия не добавляются. Число последовательных CLI не измеряет ёмкость либо независимость агентов. Реальные исходники не исполняются.
