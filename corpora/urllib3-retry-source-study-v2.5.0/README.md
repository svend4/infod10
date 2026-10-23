# Реальные объявления Retry 2.5.0: четыре выбранные связи и явные исключения

Этот новый корпус расширяет [прежний двухсвязный пример](../urllib3-retry-v2.5.0/README.md)
до четырёх объявлений закреплённого исходника urllib3. Используются существующие
source-adapter и source-promise-coverage без изменения их грамматики.

- [Протокол, анализ и границы исследования](../../docs/development/retry-source-study.md).
- [Запуск и сохранённые результаты](../../examples/retry-source-study/README.md).
- [Регистр выбора и точные свидетельства](selection-register.json).
- [Manifest и контрольные суммы](manifest.json).

## Источник и происхождение

[retry.py](upstream/retry.py) и [MIT license](upstream/LICENSE.txt) — точные
байтовые копии из прежнего корпуса. Исходный коммит:
[aaab4eccc10c965897540b21e15f11859d0b62e7](https://github.com/urllib3/urllib3/tree/aaab4eccc10c965897540b21e15f11859d0b62e7).
Первоначальные загрузки выполнены 16 октября 2026 года; их URL, UTC-время
и hashes сохраняются в [прежнем manifest](../urllib3-retry-v2.5.0/manifest.json).
Новая загрузка upstream, проверка свежести и исполнение исходника не выполнялись.
Manifest нового корпуса закрепляет девять остальных файлов и исходный manifest;
он не закрепляет собственный hash внутри себя.

## Четыре выбранных объявления

| Mapping | Выбранная форма | Значение и граница |
|---|---|---|
| retry-methods | class frozenset strings | HEAD, GET, PUT, DELETE, OPTIONS, TRACE; не универсальный запрет POST |
| retry-total | constructor integer default | 10; единица retries требует отдельного рассмотрения текста |
| retry-remove-headers | class frozenset strings | Cookie, Authorization, Proxy-Authorization до отдельного h.lower() |
| retry-backoff-factor | constructor integer default | int-литерал 0 при аннотации float; не правило типов runtime |

[Авторский документ](authored/contract.md), [литеральный адаптер](authored/retry_config.py),
[точный профиль](authored/profile.json) и [новая спецификация promises](authored/promise-specs.json)
созданы ИИ-авторами проекта. Это не документы upstream и не независимый экспертный эталон.
Promise specs не содержат выдуманных report ID: demo явно создаёт inventory после
получения и валидации полного нового source-adapter report.

## Четыре исключённых кандидата

Регистр сохраняет RETRY_AFTER_STATUS_CODES как integer frozenset,
DEFAULT_BACKOFF_MAX как class integer, backoff_max как default Name и
backoff_jitter как float default 0.0. Эти формы не подменяются неверными типами
и не отправляются анализатору как будто они поддержаны. Исключения отмечены
excluded_before_analysis; это не фактически полученные unresolved результаты.
Восемь вручную выбранных кандидатов не являются полной описью программы
и не задают долю покрытия всей документации.

## Намеренно неверная единица в прозе

[Отдельный авторский вариант](authored/contract-misstated-total-unit.md) отличается
ровно одной заменой предложения о total. В нём намеренно неверно названо общее
число попыток вместо повторов; все четыре JSON-поля сохраняются. Источником
редакторского возражения служит сохранённый docstring, а не результат сравнения чисел.
Формальное совпадение значений и полное выбранное покрытие не доказывают смысл
этой фразы. Сам файл варианта содержит предупреждение об авторской иллюстрации.

Регистр дополнительно сохраняет четыре точных source spans: описание total,
allowed_methods=None, выражение h.lower() и параметр backoff_factor с аннотацией.
Их интерпретация составлена авторами проекта, не удостоверена человеком и не
измеряет смысловую точность, независимость агентов, производительность или ёмкость.

## Состав и ограничения

Всего десять файлов: этот README, manifest, selection register, две точные
upstream копии и пять новых authored файлов. Прежний корпус и примеры не меняются.
Здесь не создаются runtime DB, credentials, Store decisions либо право принятия.
Сценарий и аудиты выполняются отдельно; наличие корпуса само по себе не означает
успешного запуска. Требуется human review; auto_accept=false сохраняется в обоих
анализаторах, prose_semantics_verified=false — в source-promise-coverage.
