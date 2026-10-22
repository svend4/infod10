# Sealed review write job: прикладной сценарий

Сороковой этап agentwiki. [Протокол, анализ и границы](../../docs/development/workspace-restore-case-retention-review-write-sealed.md).

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-sealed-demo --workspace /tmp/agentwiki-review-write-sealed-01 --workers 3
~~~

Сценарий продолжает прежний managed issuer demo в новом private workspace. Полный логический Guard11 и свидетельство выдачи входят в одну Seal14; standalone native guard и отдельная issuance directory не создаются. Новый step требует этот seal, holder credential и отсутствие отзыва. Прежние writer/guarded APIs новый job не принимают.

[Отчёт обычного запуска](results/restore-case-retention-review-write-sealed-report.md) создан 2026-10-22T22:38:30.645362+02:00, Europe/Berlin. Первый запуск: exit 0, stderr пуст; 92 новых CLI — 52 успешных и 40 ожидаемых отказов. Сохранены 503 публичных файла: 481 JSON и 22 Markdown. Все 18 read-only аудитов прошли с первого исполнения; полные результаты и границы приведены в разделе 16 руководства.

Все три holder-фазы выполнены после parent revoke. Отброшенный целый успешный ответ восстановлен после completed с plan/review offline. Own receipt/show/cached steps работали также без issuer; последующий holder revoke заблокировал и новые, и cached steps. Все три временно скрытых пути возвращены. Credentials, full Seal14/Guard11, private controls, базы, pins и digests остаются вне Git. Driver использует только публичные ответы, credential markers и lstat metadata.

Новый путь требует issuance evidence непосредственно для поддержанного step. Ошибка после полного commit может оставить действующий seal; общего отката и доказательства всех отказов питания нет. Прежние форматы и возможность выдачи другого native job по той же issuer policy сохраняются. Own receipt lookup требует доступного собственного job; referenced issuer/plan/review могут отсутствовать. OS isolation, внешняя identity и смысловое принятие текста не добавляются.
