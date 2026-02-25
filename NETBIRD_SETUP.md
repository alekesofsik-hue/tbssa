# NETBIRD_SETUP — Развёртывание NetBird Self-hosted и подключение Windows-клиентов

Данный документ описывает полный цикл развёртывания NetBird в режиме Self-hosted:
от подготовки Ubuntu-сервера до подключения Windows-машин к частной сети.

Цель в контексте проекта: организовать защищённую сеть, по которой бот (`tbssa`)
сможет подключаться к Windows-серверам через SSH без необходимости держать
SSH-порт открытым в публичном интернете.

Важно: подставляйте свои значения вместо PLACEHOLDERS
(YOUR_DOMAIN, YOUR_SERVER_IP, YOUR_SETUP_KEY и т.п.).

--------------------------------------------------------------------------------

Содержание
- Архитектура решения
- Требования к инфраструктуре
- Шаг 1 — Подготовка Ubuntu-сервера
- Шаг 2 — Установка Docker и Docker Compose
- Шаг 3 — Настройка DNS (A-запись домена)
- Шаг 4 — Открытие портов в Firewall
- Шаг 5 — Запуск установщика NetBird (Self-hosted)
- Шаг 6 — Первый вход в Dashboard и создание Setup Key
- Шаг 7 — Установка и подключение клиента NetBird на Windows
- Шаг 8 — Проверка сети и тест SSH-соединения через NetBird
- Чек-лист финальной проверки
- Обслуживание и полезные команды

--------------------------------------------------------------------------------

Архитектура решения
-------------------
```
[Бот tbssa / Linux-клиент]
        |
        |  NetBird VPN (100.x.x.x)
        |
[NetBird Management Server]  ←  Ubuntu VPS (публичный IP)
        |
[Windows-сервер]  ←  NetBird-клиент установлен, порт 22 закрыт снаружи
```

- NetBird Management Server — центральный узел на Ubuntu VPS.  
- Каждый участник (Linux-клиент бота, Windows-серверы) устанавливает клиент NetBird
  и получает IP из диапазона `100.x.x.x`.
- SSH работает поверх этой сети: бот ходит к Windows-серверу по его NetBird-IP,
  а порт 22 в публичный интернет вообще не открывается.

--------------------------------------------------------------------------------

Требования к инфраструктуре
---------------------------
| Компонент          | Требования                                         |
|--------------------|----------------------------------------------------|
| Ubuntu VPS         | Ubuntu 22.04 / 24.04 LTS, 1 CPU, 2 ГБ RAM         |
| Публичный IP       | Статический, белый IP (у VPS-провайдера)           |
| Доменное имя       | Любой домен, которым вы владеете (A-запись → IP)   |
| Docker             | 20.x и выше                                        |
| Docker Compose     | v2 (встроен в Docker Desktop или отдельно)         |
| Windows-клиенты    | Windows 10 / 11 / Windows Server 2016+             |

Пояснение: NetBird Management сервер работает в Docker-контейнерах.
Доменное имя нужно для SSL-сертификата (Let's Encrypt) — без него панель
управления не запустится в режиме HTTPS.

--------------------------------------------------------------------------------

Шаг 1 — Подготовка Ubuntu-сервера
-----------------------------------
Цель: привести систему в актуальное состояние и установить необходимые утилиты.

1. Подключитесь к серверу по SSH:
```bash
ssh root@YOUR_SERVER_IP
```

2. Обновите пакеты и систему:
```bash
apt update && apt upgrade -y
```

3. Убедитесь, что установлены нужные утилиты:
```bash
apt install -y curl git jq
```
- `curl` — загрузка файлов и обращение к API
- `git` — клонирование репозиториев
- `jq` — работа с JSON (нужен установщику NetBird)

4. Установите правильный часовой пояс (важно для сертификатов и логов):
```bash
timedatectl set-timezone Europe/Moscow   # или ваш регион
timedatectl status                       # проверка
```

--------------------------------------------------------------------------------

Шаг 2 — Установка Docker и Docker Compose
------------------------------------------
Цель: подготовить среду выполнения для NetBird-контейнеров.

1. Установите Docker официальным скриптом (самый надёжный вариант):
```bash
curl -fsSL https://get.docker.com | sh
```

2. Добавьте текущего пользователя в группу docker (чтобы не писать sudo каждый раз):
```bash
usermod -aG docker $USER
newgrp docker
```

3. Убедитесь, что Docker запускается автоматически:
```bash
systemctl enable docker
systemctl start docker
```

4. Проверьте установку:
```bash
docker --version         # ожидаем: Docker version 24.x или выше
docker compose version   # ожидаем: Docker Compose version v2.x
```

Пояснение: NetBird использует Docker Compose v2 (команда `docker compose`,
а не устаревшую `docker-compose`). Убедитесь, что у вас именно v2.

--------------------------------------------------------------------------------

Шаг 3 — Настройка DNS (A-запись домена)
-----------------------------------------
Цель: направить ваш домен на IP-адрес Ubuntu-сервера, чтобы Let's Encrypt
мог выпустить SSL-сертификат.

1. Войдите в панель управления вашим доменом (регистратор: Cloudflare, REG.RU и т.п.).

2. Создайте A-запись:
   - Тип: `A`
   - Имя (Host): `netbird` (тогда получится `netbird.yourdomain.com`)
     или любое другое имя (например, `vpn`).
   - Значение (Value): `YOUR_SERVER_IP`
   - TTL: 300 (5 минут, чтобы быстрее подхватилось)

3. Проверьте, что DNS-запись распространилась (может занять до 30 минут):
```bash
# На сервере или любой другой машине:
nslookup netbird.yourdomain.com
# или:
dig +short netbird.yourdomain.com
```
Вывод должен показать ваш IP сервера.

Важно: не запускайте установщик NetBird, пока DNS не начал резолвить
правильный IP. Let's Encrypt проверяет DNS при выпуске сертификата.

--------------------------------------------------------------------------------

Шаг 4 — Открытие портов в Firewall
-------------------------------------
Цель: разрешить входящий трафик, необходимый для работы NetBird.

Если используете `ufw` (Ubuntu Firewall):
```bash
ufw allow 22/tcp     # SSH — чтобы не потерять доступ к серверу
ufw allow 80/tcp     # HTTP — для получения Let's Encrypt-сертификата
ufw allow 443/tcp    # HTTPS — веб-панель управления NetBird
ufw allow 33073/tcp  # NetBird Management — подключение клиентов
ufw allow 10000/tcp  # NetBird Signal — сигнальный сервер (WebRTC)
ufw allow 3478/udp   # STUN — определение внешнего адреса (обход NAT)
ufw allow 49152:65535/udp  # TURN — ретрансляция трафика если P2P недоступен

ufw enable
ufw status verbose   # проверка
```

Если используете облачный фаервол (AWS Security Groups, Hetzner Firewall и т.п.):
откройте те же порты в панели провайдера — настройки аналогичные.

Пояснение:
- Порты 80/443 — для HTTPS и автообновления сертификатов.
- Порт 33073 — клиенты подключаются к management-серверу по этому порту.
- Порт 10000 — сигнальный сервер помогает двум клиентам «найти» друг друга.
- UDP 3478 — STUN (Session Traversal Utilities for NAT): сервер сообщает клиенту
  его внешний IP и порт, что необходимо для P2P-соединений.
- UDP 49152–65535 — TURN-диапазон: если прямое P2P невозможно (строгий NAT),
  трафик идёт через сервер-ретранслятор.

--------------------------------------------------------------------------------

Шаг 5 — Запуск установщика NetBird (Self-hosted)
--------------------------------------------------
Цель: развернуть все компоненты NetBird (Management, Signal, Relay, Zitadel IdP)
одной командой с помощью официального скрипта.

Компоненты, которые поднимет скрипт:
- `netbird-management` — основной управляющий сервер
- `netbird-signal` — сигнальный сервер (WebRTC)
- `netbird-relay` — TURN-ретрансляция трафика
- `zitadel` — встроенная система идентификации (логин/пароль/2FA)
- `cockroachdb` — база данных для Zitadel

1. Скачайте и запустите официальный установщик:
```bash
export NETBIRD_DOMAIN="netbird.yourdomain.com"   # ваш домен

curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started-with-zitadel.sh \
  -o getting-started-with-zitadel.sh

chmod +x getting-started-with-zitadel.sh
./getting-started-with-zitadel.sh
```

2. В процессе выполнения скрипт:
   - Скачает docker-compose файлы для всех компонентов
   - Запросит Let's Encrypt-сертификат для вашего домена
   - Настроит Zitadel (система идентификации/авторизации)
   - Выведет на экран учётные данные первого администратора

3. КРИТИЧЕСКИ ВАЖНО: в конце скрипт выведет что-то вроде:
```
Zitadel Username: admin@netbird.yourdomain.com
Zitadel Password: SomeRandomGeneratedPassword
NetBird Management URL: https://netbird.yourdomain.com
```
Сохраните логин и пароль немедленно в защищённое место (менеджер паролей).
Они нигде больше не хранятся в открытом виде.

4. Проверьте, что все контейнеры запустились:
```bash
docker compose -f /etc/netbird/docker-compose.yml ps
```
Все сервисы должны быть в статусе `Up` или `healthy`.

5. Проверьте доступность Dashboard:
```bash
curl -I https://netbird.yourdomain.com
```
Ожидаем: `HTTP/2 200` или редирект.

Если контейнеры не поднялись:
```bash
# Посмотреть логи конкретного сервиса:
docker compose -f /etc/netbird/docker-compose.yml logs netbird-management
docker compose -f /etc/netbird/docker-compose.yml logs zitadel
```

--------------------------------------------------------------------------------

Шаг 6 — Первый вход в Dashboard и создание Setup Key
------------------------------------------------------
Цель: войти в веб-панель и создать Setup Key — ключ для подключения новых
клиентов к сети без ввода пароля на каждой машине.

1. Откройте в браузере: `https://netbird.yourdomain.com`

2. Войдите с учётными данными из Шага 5.

3. При первом входе Zitadel предложит сменить временный пароль.
   Смените его и сохраните в надёжном месте.

4. Создайте Setup Key:
   - Перейдите в меню: `Setup Keys` (левая панель).
   - Нажмите `Create Setup Key`.
   - Укажите:
     - Name: любое понятное имя (например, `windows-servers`)
     - Type: `Reusable` (многоразовый) — удобно при подключении нескольких серверов
     - Expiration: по вашему усмотрению (можно поставить без ограничения)
   - Нажмите `Create`.
   - Скопируйте и сохраните сгенерированный ключ вида `A1B2C3-D4E5F6-...`.
     После закрытия окна он больше не отображается.

Пояснение: Setup Key — это одноразовый или многоразовый токен, который позволяет
новому клиенту зарегистрироваться в вашей NetBird-сети без интерактивного входа.
Это именно то, что нужно для бот-подключений.

5. (Рекомендуется) Создайте отдельные группы для удобства управления:
   - В меню `Groups` создайте группы, например: `bots`, `windows-servers`.
   - При создании Setup Key привяжите его к нужной группе.

--------------------------------------------------------------------------------

Шаг 7 — Установка и подключение клиента NetBird на Windows
------------------------------------------------------------
Цель: установить NetBird-клиент на Windows-серверы и подключить их к вашей сети.
Выполняется НА КАЖДОМ Windows-сервере.

7.1. Установка клиента

1. Скачайте официальный установщик:
   https://github.com/netbirdio/netbird/releases/latest
   Файл: `netbird_installer_X.X.X_windows_amd64.exe`

2. Запустите установщик от имени Администратора.
   Установка создаёт:
   - Службу Windows `NetBird`
   - Системный трей-агент

3. Убедитесь, что служба запустилась:
```powershell
Get-Service -Name "NetBird"
# Ожидаем: Status = Running
```

7.2. Подключение к вашему Self-hosted серверу

Важно: по умолчанию клиент настроен на облачный сервис NetBird (app.netbird.io).
Нужно указать адрес вашего Management Server.

Вариант A — через GUI (трей):
1. Найдите иконку NetBird в системном трее.
2. Нажмите правой кнопкой → `Settings`.
3. В поле `Management URL` введите: `https://netbird.yourdomain.com:33073`
4. Нажмите `Connect`.

Вариант B — через командную строку (PowerShell от Администратора):
```powershell
# Указываем Management URL и Setup Key:
netbird up --management-url https://netbird.yourdomain.com:33073 --setup-key YOUR_SETUP_KEY
```

Или через Windows service (запускается при старте):
```powershell
# Установить Management URL в конфиге (только один раз):
netbird service install --management-url https://netbird.yourdomain.com:33073

# Запустить и подключить с Setup Key:
netbird up --setup-key YOUR_SETUP_KEY
```

7.3. Проверка подключения на Windows

```powershell
# Статус клиента:
netbird status

# Ожидаемый вывод:
# OS: windows/amd64
# Daemon version: X.X.X
# CLI version: X.X.X
# Management: Connected
# Signal: Connected
# Relays: ...
# Peers count: X/X Connected
# NetBird IP: 100.X.X.X/16
```

Запомните NetBird IP сервера (100.X.X.X) — он понадобится для настройки SSH в боте.

7.3.1. (Рекомендуется) Посмотреть “ID” сети NetBird и сменить тип сети на Private

Иногда Windows назначает VPN-интерфейсу профиль **Public**, из‑за чего могут быть
ограничены входящие правила Firewall (например, ICMP/ping или SMB). Для серверов,
которые администрируются через VPN, обычно удобнее профиль **Private**.

1) Посмотреть, как Windows видит интерфейс NetBird (и его “ID”):

```powershell
# Показывает все сетевые профили
Get-NetConnectionProfile

# Удобная фильтрация по NetBird (если InterfaceAlias содержит NetBird)
Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -like "*NetBird*" } |
  Select-Object Name, InterfaceAlias, InterfaceIndex, NetworkCategory
```

Обратите внимание на поле:
- **InterfaceIndex** — это и есть удобный “ID” интерфейса (например 15).  
- **NetworkCategory** — текущий тип сети (`Public` / `Private` / `DomainAuthenticated`).

2) Сменить тип сети на Private (рекомендуется делать по InterfaceIndex):

```powershell
# Замените XX на ваш InterfaceIndex
Set-NetConnectionProfile -InterfaceIndex XX -NetworkCategory Private
```

3) Проверить результат:

```powershell
Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -like "*NetBird*" } |
  Select-Object Name, InterfaceAlias, InterfaceIndex, NetworkCategory
```

Если нужно вернуть обратно (редко):

```powershell
Set-NetConnectionProfile -InterfaceIndex XX -NetworkCategory Public
```

7.3.2. (Альтернатива) Смена типа сети через реестр, если PowerShell не работает

Иногда `Set-NetConnectionProfile` не применяется (например, из‑за проблем WMI/NLA).
В этом случае можно сменить категорию сети напрямую в реестре Windows.

Внимание: правка реестра влияет на сетевой профиль Windows. Делайте аккуратно.

1) Откройте редактор реестра:
- Win + R → `regedit`

2) Перейдите в раздел профилей сетей:

```
HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles
```

3) Внутри будет несколько подразделов вида `{...}`. Откройте их по очереди и найдите нужный профиль по параметру:
- **ProfileName** (часто содержит `NetBird`)

4) Когда найдёте профиль NetBird, измените параметр (обычно тип DWORD):
- **Category**:
  - `0` — Public (Общедоступная)
  - `1` — Private (Частная)
  - `2` — Domain (Доменная)

Для VPN-интерфейса обычно выбирают `1` (Private).

5) Применение изменений:
- Самый надёжный способ — **перезагрузить Windows**.
- Если перезагрузка нежелательна, попробуйте:
  - Перезапустить службу NetBird:

```powershell
Restart-Service -Name "NetBird"
```

  - или «отключить/включить» NetBird (например, `netbird down` → `netbird up`).

7.4. Проверка в Dashboard

- Откройте `https://netbird.yourdomain.com`.
- Перейдите в `Peers`.
- Ваш Windows-сервер должен появиться в списке со статусом `Connected`.

7.5. Настройка автозапуска клиента

NetBird устанавливает Windows-службу, которая стартует автоматически.
Проверьте и при необходимости исправьте тип запуска:

```powershell
Set-Service -Name "NetBird" -StartupType Automatic
```

--------------------------------------------------------------------------------

Шаг 8 — Проверка сети и тест SSH-соединения через NetBird
---------------------------------------------------------
Цель: убедиться, что бот может подключаться к Windows-серверам по SSH
используя NetBird IP (100.x.x.x), а не публичный IP.

8.1. Подключение Linux-клиента (машина с ботом) к NetBird

На Ubuntu/Debian машине, где запущен бот:
```bash
curl -fsSL https://pkgs.netbird.io/install.sh | sh
netbird up --management-url https://netbird.yourdomain.com:33073 --setup-key YOUR_SETUP_KEY

# Проверка:
netbird status
# Должно показать Management: Connected и NetBird IP
```

8.2. Проверка связи между клиентами

На машине бота:
```bash
# Узнаём NetBird IP Windows-сервера (из Dashboard → Peers → копируем IP)
WINDOWS_NETBIRD_IP=100.X.X.X

# Проверяем пинг:
ping $WINDOWS_NETBIRD_IP

# Ожидаем: ответы без потерь
```

8.3. Проверка SSH через NetBird

```bash
# Обычное SSH-подключение, но через NetBird IP (а не публичный IP):
ssh -i ~/.ssh/id_ed25519_bot bot-admin@$WINDOWS_NETBIRD_IP

# Тест команды перезагрузки:
ssh -i ~/.ssh/id_ed25519_bot bot-admin@$WINDOWS_NETBIRD_IP "shutdown /r /t 0"
```

8.4. Обновление конфигурации бота tbssa

В настройках бота поменяйте SSH-хосты серверов с публичных IP на их NetBird IP.
Это можно сделать через панель управления ботом (раздел «Серверы»).

Преимущество: порт 22 на Windows-серверах теперь можно полностью закрыть
от публичного интернета — SSH работает только внутри NetBird-сети.

--------------------------------------------------------------------------------

Чек-лист финальной проверки
----------------------------
- [ ] A-запись DNS правильно указывает на IP Ubuntu-сервера.  
- [ ] Все контейнеры NetBird на Ubuntu запущены (`docker compose ps` → все `Up`).  
- [ ] Dashboard доступен по HTTPS (`https://netbird.yourdomain.com`).  
- [ ] Setup Key создан и сохранён.  
- [ ] NetBird-клиент установлен и подключён на каждом Windows-сервере.  
- [ ] Каждый Windows-сервер виден в Dashboard → Peers со статусом `Connected`.  
- [ ] Linux-клиент (машина бота) подключён к сети NetBird.  
- [ ] Пинг между Linux-клиентом и Windows-сервером по NetBird IP проходит.  
- [ ] SSH-соединение от бота до Windows-сервера по NetBird IP работает.  
- [ ] В настройках бота (tbssa) SSH-хосты обновлены на NetBird IP.  

--------------------------------------------------------------------------------

Обслуживание и полезные команды
---------------------------------

Управление контейнерами на Ubuntu:
```bash
# Перезапуск всех сервисов NetBird:
docker compose -f /etc/netbird/docker-compose.yml restart

# Остановка:
docker compose -f /etc/netbird/docker-compose.yml down

# Запуск:
docker compose -f /etc/netbird/docker-compose.yml up -d

# Логи всех сервисов:
docker compose -f /etc/netbird/docker-compose.yml logs -f

# Логи конкретного сервиса:
docker compose -f /etc/netbird/docker-compose.yml logs -f netbird-management
```

Управление клиентом на Windows (PowerShell):
```powershell
# Статус:
netbird status

# Переподключение:
netbird down
netbird up --setup-key YOUR_SETUP_KEY

# Перезапуск службы:
Restart-Service -Name "NetBird"
```

Управление клиентом на Linux:
```bash
# Статус:
netbird status

# Переподключение:
sudo netbird down
sudo netbird up --management-url https://netbird.yourdomain.com:33073 --setup-key YOUR_SETUP_KEY

# Статус службы:
systemctl status netbird
```

Обновление NetBird на Ubuntu (перезапуск с новым образом):
```bash
docker compose -f /etc/netbird/docker-compose.yml pull
docker compose -f /etc/netbird/docker-compose.yml up -d
```

--------------------------------------------------------------------------------

Замечание по безопасности
--------------------------
После успешного развёртывания NetBird:

- Закройте порт 22 в публичном фаерволе Windows-серверов (Firewall → входящие
  правила) или добавьте правило, разрешающее SSH только с NetBird-диапазона `100.0.0.0/8`.
- SSH-подключения будут работать только внутри NetBird-сети — это значительно
  снижает поверхность атаки.
- Setup Key типа `Reusable` лучше ограничить по времени действия или создать
  отдельный одноразовый ключ для каждого нового сервера, а Reusable держать
  только для активного развёртывания.
