# tgtracker

Снимает публичные страницы и сборки Telegram в папку `data/`. Изменения видны как обычные коммиты.

| Режим | Что снимает | Папка |
|---|---|---|
| `web` | telegram.org, core, desktop, instantview, promote, contest.com, веб-клиенты и их статика | `data/web`, `data/web_static` |
| `translations` | английские тексты translations.telegram.org по всем платформам | `data/translations` |
| `tl` | TL-схемы TDLib и tdesktop | `data/tl` |
| `clients` | Android beta и stable, macOS: TL-классы, ключи, тексты, манифест, файлы | `data/client` |

Запускается через GitHub Actions примерно раз в 10 минут (`.github/workflows/track.yml`).

Локально: `pip install -r requirements.txt`, затем `python track.py web --limit 50`. Папку данных задаёт `TRACK_DATA`.
Тесты: `python test_track.py`.
