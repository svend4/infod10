# Обещания документа и проверяемое происхождение связи

Сорок второй этап agentwiki: [подробный протокол и анализ](../../docs/development/source-promise-coverage.md).

~~~sh
python3 -m agentwiki source-promise-coverage-demo --workspace /tmp/agentwiki-source-promise-coverage-01
~~~

Сценарий использует три сохранённых публичных отчёта [source adapter](../source-adapter/README.md) и новые явно составленные inventories. Прежние отчёты и [корпус urllib3 Retry 2.5.0](../../corpora/urllib3-retry-v2.5.0/README.md) не изменяются. Копия MIT license сопровождает новые captured материалы.

Обещание связывается с точной уникальной цитатой вне fenced blocks, выбранным полем и полными ожидаемыми scope/type/selectors. Покрытие и удовлетворённость сравнениями учитываются отдельно. Все parent comparisons и blockers сохраняются, включая неиспользуемые проблемные mappings. Новая inventory не является автоматическим преобразованием прежнего coverage-1.

[Отчёт выполненного сценария](results/report.md) сохранён вместе с [машинной сводкой](results/report.json), десятью полными отчётами и inputs: всего 31 файл. Запуск 2026-10-23T17:10:42.446627+02:00 (Europe/Berlin) завершился с exit 0. Выполнены 22 CLI: 21 успешный вызов и один ожидаемый отказ перезаписи. Исторический show пересчитал собственный captured report при целиком отсутствующей inputs directory; затем inputs были возвращены.

В десяти авторских случаях 18 строк обещаний: 12 covered, из них 11 matching и одна unresolved. Два случая получили current_for_human_review, восемь — blocked. При исключении проблемного total из описи оставшийся methods имеет 1/1 coverage, но upstream mismatch сохраняет blocked. Повтор требования даёт 3/3 promises и 2/2 distinct requirements; пустая опись — null fractions. Суммы по повторяющимся примерам не измеряют полноту документации.

Human review остаётся обязательным. Расположение цитаты, совпадение метаданных и доля покрытия не устанавливают смысл прозы, полноту описи, независимость проверяющих и право принятия. Fenced-block scanner не распознаёт весь Markdown/HTML или inline/indented code; `prose_semantics_verified=false`.
