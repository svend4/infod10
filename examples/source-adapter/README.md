# Происхождение выбранных значений адаптера

Сорок первый этап agentwiki: [подробный протокол и анализ](../../docs/development/source-adapter.md).

~~~sh
python3 -m agentwiki source-adapter-demo --workspace /tmp/agentwiki-source-adapter-01
~~~

Материал — [закреплённый urllib3 Retry 2.5.0](../../corpora/urllib3-retry-v2.5.0/README.md). Исходник, авторский адаптер и документ берутся из сохранённого корпуса; прежние файлы не изменяются. Копия [MIT license](../../corpora/urllib3-retry-v2.5.0/upstream/LICENSE.txt) сопровождает новые исходные материалы.

Новый AST verifier извлекает только явно выбранные class constant `Retry.DEFAULT_ALLOWED_METHODS` и parameter default `Retry.__init__.total`. Сравнение с адаптером и JSON fence документа даёт отдельные парные результаты. Исторический `show` пересчитывает отчёт из captured inputs без живых файлов.

[Отчёт обычного сценария](results/report.md) создан 2026-10-23T13:45:32.645060+02:00, Europe/Berlin. Первый запуск: exit 0, stderr пуст; 18 CLI — 17 успешных и один ожидаемый отказ перезаписи. Сохранены 26 публичных файлов: 15 JSON, 3 Markdown, 7 Python data files и MIT license. Все три read-only аудита прошли с первого исполнения; подробности приведены в разделе 12 руководства.

Baseline дал два совпадения. В авторских вариантах сохранены mismatch и unresolved, включая mismatch adapter↔document при неразрешимом source. Show работал при отсутствии inputs, затем каталог восстановлен; существующий report не перезаписан. Изменённые fixtures относятся к авторам проекта, а не к новой версии upstream. Исходный и предложенный Python не исполнялись.

Совпадение синтаксических значений не устанавливает Python runtime binding, поведение сети, смысл единиц или всей прозы, полноту выбранных обязательств и независимость проверяющих. `total=10` описывается авторами как число retries; отсутствие POST в выбранном наборе не является универсальным запретом его повтора. Human review остаётся обязательным, `auto_accept=false`.
