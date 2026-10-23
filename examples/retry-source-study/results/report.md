# Четыре объявления Retry, явные исключения и граница проверки прозы

Время отчёта: 2026-10-23T17:38:41.838149+02:00 (Europe/Berlin). Полные source/coverage reports используют UTC.

[Происхождение и ответы source analyze/show](source-import.json), [закреплённый исходник](inputs/source.py), [MIT license](inputs/LICENSE.txt). [Selection register](inputs/selection-register.json) явно содержит четыре selected и четыре excluded_before_analysis; восемь записей не являются полной описью источника или знаменателем coverage.

[Профиль](inputs/profile.json), [новые авторские promise specs](inputs/promise-specs.json), [baseline document](inputs/document.md), [заведомо ошибочная авторская проза](inputs/document-misstated-total-unit.md), [baseline inventory](inputs/inventory-baseline.json). Полные source reports: [baseline](source-reports/baseline.json), [misstated total unit](source-reports/misstated-total-unit.json).

## Шесть обычных случаев

| Случай | Covered / total | Unique covered / total | Matching | State |
|---|---:|---:|---:|---|
| [baseline](coverage-reports/baseline.json) | 4 / 4 | 4 / 4 | 4 | current_for_human_review |
| [uncovered-headers](coverage-reports/uncovered-headers.json) | 3 / 4 | 3 / 4 | 3 | blocked |
| [invalid-headers-scope](coverage-reports/invalid-headers-scope.json) | 3 / 4 | 3 / 4 | 3 | blocked |
| [missing-headers-quote](coverage-reports/missing-headers-quote.json) | 3 / 4 | 3 / 4 | 3 | blocked |
| [shared-headers](coverage-reports/shared-headers.json) | 5 / 5 | 4 / 4 | 5 | current_for_human_review |
| [misstated-total-unit](coverage-reports/misstated-total-unit.json) | 4 / 4 | 4 / 4 | 4 | current_for_human_review |

## Что наблюдалось

Два новых полных source reports сравнили по четыре объявления: методы, total=10, исходный набор имён удаляемых заголовков и AST default backoff_factor=0. Все восемь mapping comparisons, 24 pair comparisons и 24 endpoints разрешились с совпавшими значениями. Float annotation не превращает выбранный integer AST literal в утверждение о разрешённых runtime values.
В шести авторских иллюстрациях 25 promise rows: 22 covered и matching; отдельно одна uncovered, одна invalid_mapping и одна unresolved_anchor. Три случая current_for_human_review, три blocked. Известная неверная scope сохранила фактический requirement digest и полный Comparison4. Повтор headers promise дал 5/5 covered при 4/4 distinct requirements; shared mapping не является независимым основанием.
Ключевой содержательный случай: заведомо неверная авторская фраза подменяет retries общим числом попыток. JSON total=10, селекторы и уникальная цитата остаются согласованы, поэтому существующие анализаторы возвращают 4/4 coverage и current_for_human_review с prose_semantics_verified=false. Ошибку смысла они не обнаруживают; правильная единица не подтверждена этим результатом. Upstream source не изменялся. Это авторская иллюстрация границы, а не ошибочный текст официальной документации, независимый эталон или измеренная semantic accuracy.
Четыре иных объявления сохранены в register до анализа: integer frozenset, class integer, Name default и float default. Они не поданы под неверными типами и не выдаются за analyzer unresolved results. Четыре source-backed caveats сохраняют точные цитаты, позиции и commit-pinned ссылки для рассмотрения человеком.
Выполнены две source analyze/show пары и шесть coverage пар. Затем show прошёл при отсутствии inputs и source-reports целиком; finally вернул оба каталога. Existing output получил безопасный отказ без изменения baseline report bytes/metadata.

## Публичные основания

[Все случаи и ответы](case-observations.json), [18 CLI outcomes](cli-outcomes.json), [исторический show](offline-observations.json), [отказ перезаписи](output-collision.json), [машинная сводка](report.json).

## Границы

Выполнены 18 последовательных CLI: 17 successful и один ожидаемый refusal, 18 различных PID; concurrency и предельное число агентов не измерялись. Публичное дерево — 29 файлов: 23 JSON, три Markdown, два Python DATA и одна лицензия. Python inputs не исполнялись и не импортировались.
Четыре выбранных declarations и восемь register entries составлены авторами проекта. Суммы повторяющихся случаев не являются долей полноты документации. Raw header names не заменяют анализа последующего lowercase преобразования и всех redirect веток. Total integer не устанавливает смысл попыток; source literal не устанавливает runtime binding. Прежние 11 corpus/26 source-adapter/31 source-promise public files и новый десятифайловый corpus остались byte-identical. Новых DB, credentials, Store writes, external LLM или worker pool нет. Текущая свежесть upstream, атомарный capture, человеческая экспертиза и независимость участников не установлены. Human review обязателен; auto_accept=false.
