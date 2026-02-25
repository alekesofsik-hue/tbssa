# Как участвовать в разработке

## Разработка

1. Клонируйте репозиторий.
2. Установите зависимости:
   ```bash
   pip install -e ".[dev]"
   ```
3. Проверьте код:
   ```bash
   ruff check src tests
   ruff format --check src tests
   pytest
   ```

## Pre-commit

Установите pre-commit:

```bash
pip install pre-commit
pre-commit install
```

## Коммиты

- Пишите понятные сообщения на русском или английском.
- Не коммитьте секреты и `.env`.
