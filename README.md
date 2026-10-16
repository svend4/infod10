# infod10

Архив беседы и работающий локальный прототип совместного написания документов и кода. Python workers готовят предложения; SQLite-координатор проверяет прочитанные зависимости, результаты запросов и общие ограничения. Документы, код и поиск экспортируются из одной ревизии знаний.

## Запустить проект

Python 3.10+, стандартная библиотека; без установки пакетов и ключей API. Из корня репозитория:

~~~sh
python3 -m agentwiki demo --workspace /tmp/agentwiki-start-01 --workers 6
python3 -m agentwiki status --workspace /tmp/agentwiki-start-01
python3 -m agentwiki search --release examples/multi-agent/results/release/r9-a450bcf40a40dc20 --query timeout
~~~

Нужен новый пустой workspace. Готовый demo повторно читается без изменений. Это управляемые Python исполнители на вымышленных данных; LLM не подключена. При запуске предложенный код не исполняется.

- [Подробное руководство, устройство и API](docs/development/README.md).
- [Теория: конфликты, семантика, число агентов и стоимость](docs/development/theory.md).
- [Пошаговое развитие и исследовательские гипотезы](docs/development/roadmap.md).
- [Выполненный пример: отчёт](examples/multi-agent/results/report.md), [JSON](examples/multi-agent/results/report.json), [созданный код](examples/multi-agent/results/release/r9-a450bcf40a40dc20/code/client_config.py).

В сохранённом запуске: 6 потоков, 11 предложений, 8 принятых, 3 конфликта, запрет запоздавшей отправки и повтор принятия без новой ревизии. Это демонстрация механизмов, не нагрузочное испытание или промышленная вики.

## Следующий реализованный этап: граф влияния

Добавлены команды impact, graph и compare. Сохраняется история зависимостей принятых артефактов; экспорт останавливается, если существенный запрос или неподставляемое основание устарели уже после принятия документа.

- [Руководство по анализу влияния](docs/development/impact-analysis.md).
- [Пример запуска и команды](examples/impact-analysis/README.md).
- [Фактические результаты](examples/impact-analysis/results/report.md): три затронутых файла из шести при изменении timeout; блокировка устаревшего списка платформ; выпуск после его исправления.

~~~sh
python3 -m agentwiki impact-demo --workspace /tmp/agentwiki-impact-start-01 --workers 6
python3 -m agentwiki impact --release examples/impact-analysis/results/release/r9-58b57cc435622fed --changed api.timeout_seconds
~~~

Новые выпуски schema 2 содержат dependencies.json. Прежние сохранённые выпуски schema 1 поддерживаются с предупреждением об ограниченном покрытии зависимостей. Граф отражает объявленные связи; понимание свободного текста и ускорение на нагрузке пока не установлены.

## Начать чтение

- [Каталог всех частей переписки и форматов](docs/sessions/README.md).
- [Часть 1: настройка, новости ИИ, Scion и многоагентные вики — 34 сообщения](docs/sessions/session-001/transcript.md).
- [Часть 2: сохранение, публикация и проверка GitHub — 10 сообщений](docs/sessions/session-002/transcript.md).
- [Часть 3: продолжение архива и поручение реализации — 5 сообщений](docs/sessions/session-003/transcript.md).
- [Часть 4: реализация первого этапа и запрос продолжения — 7 сообщений](docs/sessions/session-004/transcript.md).
- [Границы первого снимка](docs/sessions/session-001/README.md) и [продолжения](docs/sessions/session-002/README.md).
- [Тематическая база знаний](docs/knowledge/README.md).
- [Источники и ограничения утверждений](docs/knowledge/sources.md).
- [Решения, предложения и открытые вопросы](docs/knowledge/decisions.md).

Исходные сообщения сохранены отдельно от конспекта: 56 сообщений в четырёх частях, до запроса продолжения после первого этапа реализации включительно. Текущая разработка описана в docs/development; прежняя архитектура шире возможностей прототипа. Результаты проверок среды относятся к моменту соответствующего сообщения.

## Содержимое

| Путь | Назначение |
|---|---|
| agentwiki/ | SQLite-хранилище, предложения, координация, workers и CLI |
| examples/impact-analysis/ | Граф зависимостей, сравнение выпусков и результаты второго этапа |
| examples/multi-agent/ | Запуск и фактические результаты с экспортом документов и кода |
| docs/development/ | Практическое руководство, теория и план следующих этапов |
| docs/sessions/ | Архивы переписки, метаданные и контрольные суммы |
| docs/knowledge/ | Тематические материалы со статусами утверждений и источниками |
| scripts/build_session_archive.py | Воспроизводимая сборка Markdown и манифеста из JSONL |
| AGENTS.md | Правила дальнейшего ведения репозитория |

## Проверить архив

Из корня репозитория:

~~~sh
python3 scripts/build_session_archive.py --all --check
~~~

Для повторной сборки производных файлов конкретной части после намеренного изменения JSONL:

~~~sh
python3 scripts/build_session_archive.py --session docs/sessions/session-002
~~~

Скрипт использует только стандартную библиотеку Python и не обращается в сеть. Без --session или --all сохраняется прежний выбор session-001; --all обрабатывает все части.
