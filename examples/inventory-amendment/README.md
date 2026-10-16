# Явный пересмотр описи на адаптере urllib3

Прикладной пример использует [закреплённый корпус urllib3 2.5.0](../../corpora/urllib3-retry-v2.5.0/README.md) и ручной литеральный адаптер. Реальная библиотека сохраняется как источник происхождения; изменение текста относится к авторской странице проекта.

## Запуск

Из корня существующего checkout в новом пустом каталоге:

~~~sh
python3 -m agentwiki inventory-amendment-demo \
  --workspace /tmp/agentwiki-amendment-my-run-01 --workers 3
~~~

Для публикации сохранён запуск `/tmp/infod10-amendment-development-03` с окончательной формулировкой границ. Предварительные запуски `-01` и `-02` остаются вне Git; после первого уточнена вложенность копий входов, после второго — описание того, что upstream только хешируется, а адаптер разбирается через AST. Каталог `results` содержит 40 прикладных файлов и 9 дополнительных файлов пяти CLI-вызовов. SQLite-базы остаются вне Git.

## Результат

- [Человекочитаемый отчёт](results/report.md), [полный JSON](results/report.json).
- [Запись импорта и контрольные суммы](results/source-import.json).
- [Исходное покрытие](results/base-coverage.json), [перенос после изменения цитаты](results/reworded-rebase.json).
- [Явная новая цитата](results/revised-quote-changes.json), [отчёт пересмотра](results/revised-quote-amendment.json), [новое покрытие](results/revised-quote-coverage.json).
- [Незавершённая работа](results/pending-amendment.json), [попытка оставить прежнюю цитату](results/reaffirm-old-quote-amendment.json), [неверная единица в области](results/wrong-unit-amendment.json).

Два программных автора подготовили три вклада. Интегратор создал две композиции; два программных редакторских ID записали шесть решений по трём пакетам, журнал содержит 13 событий. Все пять предложений файлов остались `proposed`.

Во всех четырёх планах сохранены два исходных ID и их порядок. Новая цитата с областью `retries` дала `current_for_human_review`; остальные планы сохранили `blocked`. Значение в документе и коде равно 10 даже в варианте с ошибочной областью `attempts`: причина препятствия — неверные метаданные требования.

## Входы и воспроизводимость

Семь файлов корпуса скопированы побайтно в `results/source-inputs/` с прежней вложенностью. Три upstream-снимка проверены по локальному manifest; они остаются в [исходном корпусе](../../corpora/urllib3-retry-v2.5.0/README.md), повторно в `results` не копируются. Относительные `upstream/...` пути manifest и source-facts относятся к корню исходного корпуса. Сетевое обновление upstream во время прикладного запуска не выполняется.

Авторские литералы, опись и привязки подготовлены вручную. Сверка байтов источников не доказывает достаточности их интерпретации. Структурированные редакторские пары описывают выбранный набор методов; предметная проверка новой формулировки `total` ими не выполняется.

## Отдельно выполненные CLI-команды

~~~sh
python3 -m agentwiki inventory-amendment plan \
  --rebase examples/inventory-amendment/results/reworded-rebase.json \
  --changes examples/inventory-amendment/results/wrong-unit-changes.json \
  --output /tmp/agentwiki-amendment-wrong-unit-01.json

python3 -m agentwiki inventory-amendment show \
  --input examples/inventory-amendment/results/revised-quote-amendment.json

python3 -m agentwiki inventory-amendment export-inventory \
  --input examples/inventory-amendment/results/revised-quote-amendment.json \
  --output /tmp/agentwiki-amended-inventory-01.json

python3 -m agentwiki inventory-amendment export-coverage \
  --input examples/inventory-amendment/results/revised-quote-amendment.json \
  --output /tmp/agentwiki-amended-coverage-01.json
~~~

Для сохранённых CLI-вызовов выходы создавались под новыми именами `results/cli-*`; команды выше используют отдельные пути для повторения. [План неверной единицы](results/cli-plan-wrong-unit.json) сохранил `blocked`. Экспорт покрытия [неверной единицы](results/cli-export-blocked-coverage.json) также заблокирован, экспорт [новой цитаты](results/cli-export-revised-coverage.json) готов к рассмотрению. [Показ отчёта](results/cli-show-revised.json) и [экспорт описи](results/cli-export-inventory.json) сохранены отдельно.

Экспортированное стандартное покрытие не содержит историю пересмотра; сохраняйте рядом amendment-отчёт и ответ CLI с его ID и digest. Подробности контракта — в [анализе десятого этапа](../../docs/development/inventory-amendment.md).

## Границы

Внешние LLM, аутентификация участников и независимая разметка человеком отсутствуют. Исходник библиотеки и адаптер не исполняются. Пересмотр требует рассмотрения человеком; одинаковая последовательность ID не устанавливает смысловую эквивалентность старой и новой описи. Измерения полезности, предельной ёмкости и выигрыша времени не проводились.
