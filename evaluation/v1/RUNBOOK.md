# Повторение замеров

Команды выполняются из корня исследовательского комплекта. Нужны окружение приложения с requirements.txt и доступ к разрешённому провайдеру. На этой машине Python приложения — po_delu/.venv/bin/python. Результаты каждого вызова сохраняются в отдельный каталог evaluation/v1/runs с исходными хешами, записями предсказаний и телеметрией. При новом методе или настройках задайте новое имя опыта.

## Без сети

```bash
python3 tools/validate_release.py
python3 -m unittest discover -s tools/tests -v
python3 tools/score_benchmark.py --out evaluation/v1/scores
python3 tools/score_rewrites.py
python3 tools/browse_eval.py
```

В каталоге po_delu выполняется python -m unittest discover -s tests -v. Проверка в Docker гарантирует наличие LibreOffice и системного шрифта для тестовой генерации PDF. Полный сценарий реального браузера находится в po_delu/tests/browser_formats.cjs. Для него нужен установленный playwright-core, Chromium и запущенный экземпляр. Задайте EDITOR_TEST_URL, CHROMIUM_PATH и TEST_PYTHON (абсолютный путь к Python приложения); если модуль Playwright не в обычном пути Node, задайте PLAYWRIGHT_MODULE. Запускайте node po_delu/tests/browser_formats.cjs. Для экспорта PPTX и PDF в среде без системных шрифтов задайте EDITOR_PDF_FONT и используйте доступный LibreOffice.

## Живые прогоны

При backend=router разрешены только вымышленные наборы. Ключ должен быть в переменной процесса LLM_API_KEY. Не передавайте его как аргумент команды. Сначала выполните сетевой опыт на одном примере с --limit 1. Параметр --parallel задаёт число одновременно обрабатываемых документов. Все платные прогоны используют общий журнал бюджетных резервов.

```bash
po_delu/.venv/bin/python tools/run_benchmark.py repeat-fixed lockbox router fixed --panel large
po_delu/.venv/bin/python tools/run_benchmark.py repeat-cascade lockbox router union --panel large
po_delu/.venv/bin/python tools/run_benchmark.py repeat-baseline lockbox router baseline --panel large
po_delu/.venv/bin/python tools/run_benchmark.py repeat-fidelity fidelity router fidelity --panel large
po_delu/.venv/bin/python tools/run_benchmark.py repeat-rewriter rewrite router rewrite --panel large
```

В отдельном процессе можно задать EDITOR_REWRITER=slopotron для режима Fix. Состав модели-предложителя и судей фиксируется в config.json. Два прогона одной задачи могут отличаться при temperature=0. Для повторения версии до последней правки редактора зафиксированы файлы, перечисленные в PROSPECTIVE.json; снимок prospective_engine.zip соответствует указанным хешам. Исходные исторические эксперименты из других ZIP относятся к другим версиям.

Полные русские диалоги и пары публичной документации воспроизводите только в локальном контуре: задайте LLM_BASE_URL для одобренного loopback endpoint, LLM_API_KEY и точный LLM_MODEL из его /models. Поддерживаются режимы semantic, slopotron и skill. В skill локальному предложителю доступна полная SKILL.md версии 0.4.2; в этих опытах измеряется генерация кандидатов до проверки. Профиль не превращает повторные обращения к одной модели в независимые голоса.

```bash
po_delu/.venv/bin/python tools/run_benchmark.py internal-repeat genre local skill
po_delu/.venv/bin/python tools/run_benchmark.py offline-repeat genre offline offline
```

Для проверки двухсемейного допуска замен задайте EDITOR_FIDELITY_CONSENSUS=1. Набор rewrite-lockbox содержит 16 новых пар. Например:

```bash
EDITOR_FIDELITY_CONSENSUS=1 po_delu/.venv/bin/python tools/run_benchmark.py repeat-double rewrite-lockbox router fidelity --panel large
```

## Технические отказы

В исходных check-fixed и check-cascade OpenRouter вернул 404 для несовместимого набора параметров. Ответы сохранены для диагностики; повторно запускавшиеся routed-* содержат корректную маршрутизацию. В prospective-fixed программа не передавала явное вхождение при повторе одной и той же цитаты в группе G5. Полный опыт с проверенными координатами — prospective-anchored. При интерпретации результатов пользуйтесь именно обозначенными итоговыми прогонами.

Второй повтор перспективного поиска прервался после 20 документов. Последний вход повторён отдельно. Представление prospective-repeat-complete объединяет 20 первичных записей и один повтор по ID, хешам текста, запроса и подсказок этапов. Его создаёт tools/complete_repeat.py. В себестоимости учтены исходные вызовы, включая незавершённый.
