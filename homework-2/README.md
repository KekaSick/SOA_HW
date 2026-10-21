# ДЗ №2. Marketplace: OpenAPI + CRUD

API маркетплейса на Python 3.11: FastAPI обрабатывает HTTP-запросы, psycopg выполняет SQL-запросы к PostgreSQL 16, Flyway применяет миграции. OpenAPI Generator создаёт маршруты, базовые классы обработчиков и Pydantic-модели запросов и ответов по контракту [marketplace.yaml](openapi/marketplace.yaml).

## Запуск

В каталоге `homework-2` выполните:

```sh
docker compose up --build
```

API доступен по адресу `http://localhost:8082`, Swagger UI открывается на [http://localhost:8082/swagger-ui/index.html](http://localhost:8082/swagger-ui/index.html). Swagger читает исходный YAML-контракт.

При сборке генератор сохраняет Python-код в `generated/src/openapi_server/`. Папка `generated/` включена в `.gitignore`. Отдельная команда генерации из каталога `homework-2`:

```sh
docker compose -f codegen/compose.yaml run --build --rm generate
```

Для запуска и генерации нужен Docker. Шаблоны в `codegen/templates/` задают обработку Decimal, строгую валидацию типов JSON и преобразование строковых параметров URL. Поля моделей, ограничения и маршруты берутся из OpenAPI.

Перед стартом приложения отдельный контейнер Flyway применяет [миграции](migrations/), которые создают таблицы и заполняют их учебными данными. Приложение запускается после успешного завершения миграций. PostgreSQL хранит данные в Docker volume. Интервал `N` между однотипными операциями с заказами по умолчанию составляет 1 минуту. Чтобы задать другой интервал, укажите его при запуске:

```sh
ORDER_INTERVAL_MINUTES=2 docker compose up --build
```

Миграция создаёт пользователей `user` с ролью USER, `seller` и `seller2` с ролью SELLER, а также `admin` с ролью ADMIN. У всех пароль `demo-password`. При регистрации через API пользователь получает роль USER; выбрать SELLER или ADMIN нельзя.

В базе также есть два активных товара и один неактивный. Промокоды `DEMO20` и `FIXED500` действуют 30 дней после первого запуска миграции.

## Демонстрация запросов и БД

Получите токены продавца и покупателя:

```sh
SELLER_TOKEN=$(curl -s http://localhost:8082/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"seller","password":"demo-password"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
USER_TOKEN=$(curl -s http://localhost:8082/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"user","password":"demo-password"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

В новой базе товар №1, клавиатура, стоит 2500, на складе 10 штук. Запросите список товаров и оформите заказ:

```sh
curl -i 'http://localhost:8082/products?page=0&size=20&status=ACTIVE&category=electronics' \
  -H "Authorization: Bearer $USER_TOKEN"

curl -i http://localhost:8082/orders \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":2}],"promo_code":"DEMO20"}'
```

Сервер вернёт `201`. Сумма заказа составит 4000, скидка 1000, на складе останется 8 штук. Сохраните `id` заказа из ответа. Посмотрите записи в базе:

```sh
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT * FROM products ORDER BY id;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT * FROM orders ORDER BY id;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT * FROM order_items ORDER BY id;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT * FROM promo_codes ORDER BY id;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT * FROM user_operations ORDER BY id;'
```

Для заказа №1 в новой базе обновление и отмена выглядят так:

```sh
curl -i -X PUT http://localhost:8082/orders/1 \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":1}]}'

curl -i -X POST http://localhost:8082/orders/1/cancel \
  -H "Authorization: Bearer $USER_TOKEN"
```

После обновления сумма заказа составит 2000, скидка 500, остаток 9. Отмена переведёт заказ в CANCELED, вернёт остаток к 10 и уменьшит `current_uses` промокода до 0.

При повторной отмене сервер вернёт `409 INVALID_STATE_TRANSITION`. Если повторить создание или обновление раньше N минут после операции того же типа, ответом будет `429 ORDER_LIMIT_EXCEEDED`. Когда N минут пройдут, попытка создать заказ при уже имеющемся активном заказе вернёт `409 ORDER_HAS_ACTIVE`.

В Swagger UI можно выполнить CRUD-запросы к товарам, получить заказ, создать промокод, зарегистрироваться, войти и обновить токены. Там же описаны тела запросов и ошибки.

## Проверки

Запустите стек, затем выполните тесты. Для них нужны Python 3 и Docker CLI:

```sh
python3 tests/e2e.py
```

Тесты проверяют CRUD товаров, фильтры, пагинацию, мягкое удаление и валидацию по контракту. Отдельные сценарии проверяют JWT и refresh-токены, доступ по ролям и владельцам, а также лимиты операций с заказами. Для заказов проверяются сохранение цены, резервирование и возврат остатков, промокоды, откат неуспешного обновления и одновременная покупка последнего товара.

Проверка внутренней ошибки, JSON-лога и заголовка запроса запускается отдельно:

```sh
docker compose exec -T app python - < tests/test_middleware.py
```

Тесты создают записи в учебной базе. Статусы PAID, SHIPPED и COMPLETED они подготавливают через SQL, чтобы проверить запрет обновления и отмены. Эндпоинты оплаты и отгрузки в задании не предусмотрены.

Посмотреть логи запросов:

```sh
docker compose logs -f app
```

Для каждого API-запроса приложение пишет JSON с полями `request_id`, `method`, `endpoint`, `status_code`, `duration_ms`, `user_id`, `timestamp`. Для POST, PUT и DELETE в лог попадает и тело запроса. Пароли и токены скрываются маской. UUID запроса возвращается в заголовке `X-Request-Id`.

Access token действует 15 минут, refresh-токен действует 7 дней. Роль пользователя хранится в claim `role`.

## Реализация правил задания

[Обработчик товаров](app/products.py) реализует сгенерированный базовый класс CRUD. При DELETE товар получает статус ARCHIVED. Миграция создаёт индекс `products(status)` и задаёт автоматическое заполнение времени: значения по умолчанию при создании и триггеры `updated_at` при обновлении.

[Обработчик заказов](app/orders.py) выполняет проверки в порядке, указанном в задании. Создание, обновление и отмена заказа проходят в локальных транзакциях PostgreSQL. Контекст соединения psycopg фиксирует изменения при успехе и откатывает изменения остатков, позиций, промокода и записи операции при исключении. Цены и суммы обрабатываются через `Decimal`.

Блокировки строк защищают от двойного резервирования при параллельных запросах и не позволяют обойти ограничение активного заказа. Если product_id повторяется в запросе, при проверке остатка количества складываются. В каждой позиции сохраняются количество и цена на момент операции.

Заказ проходит состояния CREATED → PAYMENT_PENDING → PAID → SHIPPED → COMPLETED. Отмена переводит CREATED или PAYMENT_PENDING в CANCELED. Обновлять позиции можно только в CREATED. PUT принимает только items, поэтому поменять через него статус нельзя. Из переходов состояний API реализует создание и отмену заказа.

Если процентная скидка превышает 70%, заказ оплачивается полностью, без скидки. При обновлении заказа уже учтённое использование промокода не считается новым. Если сумма станет ниже минимума для промокода, приложение снимет скидку, очистит связь с промокодом и вернёт его использование.
