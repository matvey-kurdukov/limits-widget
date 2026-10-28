# Лимиты Claude и ChatGPT

Строка поверх всех окон внизу экрана: сколько процентов лимита подписки Claude уже потрачено за 5 часов и за неделю и когда сброс. Если на компьютере стоит Codex, рядом лимиты ChatGPT. Только Windows 10 и 11.

```
Claude  5ч ▬ 42% 3ч18м   нед ▬ 27% 01.11  -
```

## Как поставить

Скинь ссылку на этот репозиторий своему Claude Code и напиши «установи». Он сам скачает виджет, поставит Python, если его нет, и запустит. От тебя только подтверждать его действия.

## Для Claude Code: установка

1. Скачай репозиторий в `%LOCALAPPDATA%\limits-widget`.
   - Есть git: `git clone https://github.com/matvey-kurdukov/limits-widget "$env:LOCALAPPDATA\limits-widget"`
   - Нет git, в PowerShell:
     ```powershell
     $z = "$env:TEMP\limits-widget.zip"
     Invoke-WebRequest https://github.com/matvey-kurdukov/limits-widget/archive/refs/heads/main.zip -OutFile $z -UseBasicParsing
     Expand-Archive $z "$env:TEMP\limits-widget-zip" -Force
     Copy-Item "$env:TEMP\limits-widget-zip\limits-widget-main\*" "$env:LOCALAPPDATA\limits-widget" -Recurse -Force
     ```
     Перед `Copy-Item` создай папку `%LOCALAPPDATA%\limits-widget`, если её нет.
   - Папка уже есть - это обновление: `git pull` в ней или тот же zip поверх.
2. Запусти установщик. Из PowerShell:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\limits-widget\install.ps1"
   ```
   Из Git Bash: `powershell -NoProfile -ExecutionPolicy Bypass -File "$LOCALAPPDATA/limits-widget/install.ps1"`
   Права администратора не нужны. Установщик сам ставит Python 3.12 для текущего пользователя (winget, запасной путь python.org), делает `.venv` с Pillow, ярлык автозапуска, ярлык «Лимиты» на рабочем столе, проверяет лимиты и запускает виджет. Занимает 1-3 минуты.
3. Сверь вывод:
   - `Claude: OK 12% / 30%` - цифры пришли, последняя строка `DONE` - готово.
   - `Claude: FAIL no login or it expired` - пусть пользователь один раз запустит `claude` в терминале и войдёт через `/login` подпиской Pro или Max. Виджет подхватит вход сам, переустанавливать не надо.
   - `FAIL` в другом месте - прочитай ошибку выше этой строки и почини причину, потом запусти шаг 2 снова.
4. Скажи пользователю, что виджет висит внизу экрана, и коротко расскажи, как им пользоваться (раздел ниже).

Код под пользователя не правь, токены у него не спрашивай: виджет читает вход Claude Code на этом компьютере (`%USERPROFILE%\.claude\.credentials.json`).

## Как пользоваться

- Таскать мышью за любое место.
- Двойной клик или чёрточка справа - свернуть в квадратик с кольцом в углу экрана, в нём самый загруженный лимит. Клик по квадратику - развернуть.
- Правый клик - меню: вид (полоски, цифры, столбики), тема, прозрачность, «Обновить», «Закрыть».
- Колесо мыши - прозрачность фона.
- Цвет: зелёный до 60%, жёлтый до 85%, дальше красный.
- Название стало серым - цифры старше 20 минут. Обычно это значит, что Claude Code давно не открывали и вход истёк: открой Claude Code, цифры вернутся сами.
- На скриншотах и записи экрана виджета не видно, так задумано: фон строки размыт по тому, что под ней.
- Пропал или завис - ярлык «Лимиты» на рабочем столе перезапускает его. После перезагрузки компьютера стартует сам.
- Убрать: `powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\limits-widget\uninstall.ps1"`, потом удалить папку.

## Откуда цифры

Claude: виджет шлёт от твоего входа Claude Code крошечный запрос к модели Haiku (1 токен ответа) и читает проценты лимита из заголовков ответа. Пока ты работаешь в Claude Code - раз в минуту, в простое - раз в 10 минут. На лимит это почти не влияет.

ChatGPT: тот же запрос, что делает `/status` в Codex, вход из `%USERPROFILE%\.codex\auth.json`.

Токены остаются на твоём компьютере и уходят только в `api.anthropic.com` и `chatgpt.com`. Настройки и последние цифры лежат рядом с кодом в `settings.json` и `cache.json`.
