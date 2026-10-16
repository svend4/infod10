# Выполненная локальная демонстрация

Источники и API вымышлены. Здесь работали Python workers без LLM и сетевых запросов.
Код из предложений сохранён как текст и не выполнялся.

- Создано: 2026-10-16T09:44:43.697863+00:00
- Запрошено workers: 6
- Начальных заданий: 6
- Наблюдавшийся максимум jobs в worker section: 6
- Использованные потоки: agentwiki_0, agentwiki_1, agentwiki_2, agentwiki_3, agentwiki_4, agentwiki_5
- Итоговая ревизия: 9
- Release: r9-a450bcf40a40dc20

Число workers задаёт размер локального пула. Наблюдаемая конкурентность не равна числу одновременно занятых CPU и не определяет вместимость сервиса.

## Порядок координации

1. Сначала принято добавление Windows.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_29597a30dea53795a84f6d61",
     "revision": 2,
     "replayed": false,
     "reasons": [],
     "review_id": 6,
     "coordinator": "coordinator"
   }
   ~~~
2. Список только Linux отклонён: изменилось множество результатов query.
   ~~~json
   {
     "accepted": false,
     "proposal_id": "p_f2e796ee8ddc6f337cf4ed49",
     "revision": 2,
     "replayed": false,
     "reasons": [
       {
         "code": "query_changed",
         "scope": "platforms:api-v1",
         "expected": "45553b663f8ef4ea7c46c77e5a35b1af92e3778ec808faa277af3be554c8d80a",
         "actual": "87536862a3fdb118eab0d334ea9210a3b9c3a14f69f8664b3400b736430aa23b"
       }
     ]
   }
   ~~~
3. Принято независимое предложение initial-reference.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_ba7e34e189e9b258b9cbbf31",
     "revision": 3,
     "replayed": false,
     "reasons": [],
     "review_id": 1,
     "coordinator": "coordinator"
   }
   ~~~
4. Принято независимое предложение initial-code.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_919d28883a1712baad82eef3",
     "revision": 4,
     "replayed": false,
     "reasons": [],
     "review_id": 2,
     "coordinator": "coordinator"
   }
   ~~~
5. Принято независимое предложение initial-retries.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_2a6e85749d84d58fbc4fa592",
     "revision": 5,
     "replayed": false,
     "reasons": [],
     "review_id": 4,
     "coordinator": "coordinator"
   }
   ~~~
6. Принято независимое предложение initial-linux.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_104dbc3dd39e9ae10312f297",
     "revision": 6,
     "replayed": false,
     "reasons": [],
     "review_id": 3,
     "coordinator": "coordinator"
   }
   ~~~
7. Новый список Linux и Windows принят после чтения свежей ревизии.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_789f9164403d98a9fedca5b3",
     "revision": 7,
     "replayed": false,
     "reasons": [],
     "review_id": 7,
     "coordinator": "coordinator"
   }
   ~~~
8. Принят timeout 40: итоговый бюджет 40 × 3 = 120.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_19eb91bfb0e4651798a70787",
     "revision": 8,
     "replayed": false,
     "reasons": [],
     "review_id": 10,
     "coordinator": "coordinator"
   }
   ~~~
9. Отклонены 4 попытки: совместный бюджет 40 × 4 = 160 превышает 120.
   ~~~json
   {
     "accepted": false,
     "proposal_id": "p_18206740b43a71733d7d4214",
     "revision": 8,
     "replayed": false,
     "reasons": [
       {
         "code": "invariant_violation",
         "rule": "timeout_times_attempts",
         "calculated_seconds": 160,
         "budget_seconds": 120
       }
     ]
   }
   ~~~
10. Отклонён документ с устаревшим readset для timeout 30.
   ~~~json
   {
     "accepted": false,
     "proposal_id": "p_611eb903f8c41b6000349f83",
     "revision": 8,
     "replayed": false,
     "reasons": [
       {
         "code": "stale_read",
         "id": "api.timeout_seconds",
         "expected": "929089e61ffe6e82ac0dde93a1da561e6dfeee166de9d11b8b85b02009299e8d",
         "actual": "4ba78befe8ea90188849fc7c688ab32254f4974e60d33ada63647e8585a2e616"
       }
     ]
   }
   ~~~
11. Старый worker не смог отправить результат с истёкшим token.
   ~~~json
   {
     "error_code": "lease_lost",
     "message": "Expired or superseded attempts cannot submit"
   }
   ~~~
12. Результат нового worker принят.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_6f9424f8b59a172a65d08df2",
     "revision": 9,
     "replayed": false,
     "reasons": [],
     "review_id": 11,
     "coordinator": "coordinator"
   }
   ~~~
13. Повторное принятие того же proposal вернуло прежний результат без новой ревизии.
   ~~~json
   {
     "accepted": true,
     "proposal_id": "p_6f9424f8b59a172a65d08df2",
     "revision": 9,
     "replayed": true,
     "reasons": []
   }
   ~~~
14. Опубликован согласованный локальный release одной ревизии.
   ~~~json
   {
     "release_id": "r9-a450bcf40a40dc20",
     "revision": 9,
     "path": "/tmp/infod10-multiagent-development-01/release/r9-a450bcf40a40dc20",
     "files": {
       "code/client_config.py": {
         "sha256": "b468184c39506d354994abe6d04d95a75f646a7956e02fc31ad367823a77c8f9",
         "bytes": 121
       },
       "docs/lease-recovery.md": {
         "sha256": "3df72c26d3cf65e6f67d7b0ba0bd8f0496f961fbbe323acfea1f633d721ea964",
         "bytes": 54
       },
       "docs/linux.md": {
         "sha256": "ccc2f33b5270ca8a2ffea671041453dfef58a2a3d5a67f8ea5d2fb3887d60204",
         "bytes": 41
       },
       "docs/platforms.md": {
         "sha256": "ff244bbc3c8e25c0eae74fd97e3d737332c429d76e7f87f2a084116aa9d77a81",
         "bytes": 99
       },
       "docs/reference.md": {
         "sha256": "4340c7dadbc0dc06e7867ae9c0acec2af7974cafa5e370a5078c0fdea11e17f0",
         "bytes": 50
       },
       "docs/retries.md": {
         "sha256": "071b4421c7760d06e836e3bd554667351b18fc00cc50cfd16e99a0ca8481045b",
         "bytes": 112
       },
       "knowledge.json": {
         "sha256": "f57d94d484332179521e9954b5d2cb800dc71e5953248158fe2d5e3a188a1f68",
         "bytes": 3126
       },
       "search-index.json": {
         "sha256": "26c7943946c4e23045d399a68fa3bdbeaee0b5f4ff0f132c61c403850ab39e02",
         "bytes": 1502
       },
       "sources.json": {
         "sha256": "b5422368637c7f4c631a5b305d4ad91c3f17b5add55ab629493914c2d1f94670",
         "bytes": 447
       }
     },
     "state_digest": "dd8cf56a98dd2331584ce76fb04ea2a6484fb1a3191c61cf22ba26847ffe6227"
   }
   ~~~

## Семантическое ограничение

Начало: `30 × 3 = 90 ≤ 120`. Каждое предложение отдельно допустимо:
`40 × 3 = 120`, `30 × 4 = 120`. Совместный результат `40 × 4 = 160 > 120` отклонён при принятии.
Проверка цитаты показывает наличие текста в вымышленном источнике; она не доказывает истинность произвольного утверждения.

## Файлы

`report.json` хранит результаты методов хранилища, идентификаторы предложений и задания.
Каталог `release/` содержит согласованный экспорт документов, кода, claims и поиска одной ревизии.
События и состояния задач также записаны в SQLite. Это локальная демонстрация, не распределённый кластер.
