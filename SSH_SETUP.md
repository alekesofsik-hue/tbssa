# SSH_SETUP — Полная инструкция по очистке, установке и настройке OpenSSH на Windows

Данный документ содержит пошаговую, самодостаточную и исчерпывающую инструкцию — от полной зачистки следов прошлых установок OpenSSH до ручной установки, настройки сервера и клиента под пользователя `bot-admin`. Все команды — для PowerShell (Windows) или bash (клиент). Выполняйте команды в PowerShell от имени администратора (Run as Administrator).

Важно: подставляйте свои значения вместо PLACEHOLDERS (IP_СЕРВЕРА, путь к приватному ключу, пароли и т.п.).

Содержание
- Цель и краткий план
- Подготовка и предостережения
- Шаг 1 — Полная зачистка (Deep clean)
- Шаг 2 — Создание и подготовка пользователя bot-admin
- Шаг 3 — Установка OpenSSH вручную (Win32-OpenSSH)
- Шаг 4 — Регистрация служб и генерация host-ключей
- Шаг 5 — Подготовка профиля bot-admin и папки .ssh
- Шаг 6 — Импорт публичного ключа в authorized_keys (без BOM)
- Шаг 7 — Жёсткая настройка прав (ACL) на папку и файл authorized_keys
- Шаг 8 — Редактирование sshd_config (обязательные параметры)
- Шаг 9 — Запуск служб и настройка Firewall
- Шаг 10 — Тестирование подключения и отладка
- Частые ошибки и их исправление
- Чек-лист и финальная проверка
- Полезные команды для диагностики

--------------------------------------------------------------------------------

Цель и краткий план
--------------------
Цель: развернуть чистый экземпляр OpenSSH на Windows-сервере и настроить доступ по ключу для ограниченного пользователя `bot-admin`, которому делегированы только права на выключение/перезагрузку сервера.

Краткий план:
1. Удалить старые службы/файлы (устранить ошибки 1072/1073).  
2. Создать `bot-admin` и дать ему право на Shutdown через локальные политики.  
3. Установить OpenSSH вручную (распаковка архива Win32-OpenSSH).  
4. Зарегистрировать службы sshd/ssh-agent и сгенерировать host-ключи.  
5. Подготовить профиль `bot-admin` (.ssh) и создать `authorized_keys`.  
6. Импортировать публичный ключ в `authorized_keys` (ASCII без BOM).  
7. Ужесточить ACL на `.ssh/authorized_keys` (и при желании убрать Administrators после успешной проверки).  
8. Настроить `sshd_config`: запрет паролей, разрешение только pubkey, AllowUsers bot-admin и закомментировать блок админов.  
9. Запустить службу и открыть порт 22 в Firewall.  
10. Проверить подключение с клиента с явным указанием приватного ключа (-i).  

Подготовка и предостережения
----------------------------
- Все команды PowerShell выполнять в сессии с правами администратора.  
- Перед удалением файлов и перезагрузкой убедитесь, что у вас есть доступ (консоль/другая сессия) для отката.  
- Работая с ACL (icacls), тщательно проверьте пути и имена пользователей — ошибки в ACL могут привести к недоступности файлов.  

Шаг 1 — Полная зачистка (Deep clean)
------------------------------------
Цель: удалить все следы старых установок OpenSSH (службы, каталоги, ключи) и убедиться, что нет «призраков» служб.

1. Открой PowerShell от Администратора.
2. Попробуй остановить и удалить старые службы (ошибки игнорировать):

```powershell
sc.exe stop sshd; sc.exe stop ssh-agent
sc.exe delete sshd; sc.exe delete ssh-agent
```

3. Если при delete вы получили ошибку "The specified service has been marked for deletion" или ошибка 1072 — закрой GUI (services.msc, Event Viewer, Task Manager) и перезагрузи сервер:

```powershell
Restart-Computer -Force
```

4. После перезагрузки удалите остаточные папки (если они есть):

```powershell
Remove-Item -Path "C:\Program Files\OpenSSH" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "C:\ProgramData\ssh" -Recurse -Force -ErrorAction SilentlyContinue
# (опционально) удалить .ssh в профилях пользователей, если уверены
Get-ChildItem 'C:\Users' -Directory | ForEach-Object {
  $p = Join-Path $_.FullName '.ssh'
  if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
}
```

Пояснение: перезагрузка обязательна при пометке на удаление — Windows освобождает дескрипторы служб только при reboot.

Шаг 2 — Создание и подготовка пользователя bot-admin
---------------------------------------------------
Цель: создать ограниченного пользователя, которому дать право на shutdown, но не добавлять в группу Administrators.

1. Создание локального пользователя

Иногда вы можете столкнуться с ситуацией, когда командлет `New-LocalUser` **отсутствует**.
Это нормально для части серверов:
- `New-LocalUser/Get-LocalUser` есть начиная с **Windows Server 2016 / Windows 10** (модуль `Microsoft.PowerShell.LocalAccounts`).
- Также обычно требуется **Windows PowerShell 5.1**.

Перед созданием пользователя можно быстро проверить доступность командлета:

```powershell
# Версия PowerShell:
$PSVersionTable.PSVersion

# Доступен ли New-LocalUser:
Get-Command New-LocalUser -ErrorAction SilentlyContinue
```

Если `Get-Command` вернул команду — используйте современный способ:

```powershell
$pass = ConvertTo-SecureString "ВАШ_СЛОЖНЫЙ_ПАРОЛЬ" -AsPlainText -Force
New-LocalUser -Name "bot-admin" -Password $pass -Description "SSH Shutdown User"
```

Если `New-LocalUser` **не найден** (часто на Windows Server 2012 R2 и ниже, либо на “урезанных” сборках) — используйте универсальный способ через `net user`:

```powershell
# Работает практически на любой версии Windows
net user "bot-admin" "ВАШ_СЛОЖНЫЙ_ПАРОЛЬ" /add /comment:"SSH Shutdown User"
```

Если ОС новая, но модуль не подхватился, можно попробовать импортировать его вручную:

```powershell
Import-Module Microsoft.PowerShell.LocalAccounts -ErrorAction SilentlyContinue
Get-Command New-LocalUser -ErrorAction SilentlyContinue
```

2. Делегирование прав через GUI (secpol.msc):
- Win + R → `secpol.msc` → Local Policies → User Rights Assignment.  
- Добавить `bot-admin` в:
  - "Shut down the system"  
  - "Force shutdown from a remote system"  

Пояснение: эти два права дают возможность выполнить `shutdown /r /t 0` без членства в Administrators.

3. **Критически важный шаг: создать профиль (домашнюю папку) `bot-admin`**

Windows создаёт домашнюю папку пользователя (`C:\Users\<profile>`) **только при первом входе**. Если профиль не создан, дальнейшая настройка `.ssh` и `authorized_keys` становится существенно сложнее (некуда корректно положить ключи и сложно определить реальный путь профиля, который может иметь суффикс).

Сделайте ОДИН раз любой вариант ниже (достаточно одного):

- **Вариант A (самый надёжный, рекомендуемый)**: **выполните интерактивный вход** под `bot-admin` (RDP/консоль).
  - Войдите под `bot-admin` → дождитесь, пока Windows «подготовит рабочий стол» → сразу выйдите (Sign out).
  - После этого профиль и папка в `C:\Users\...` будут созданы.

- **Вариант B (без RDP, через запуск процесса от имени пользователя)**: запустите одну команду от имени `bot-admin`, чтобы Windows создала профиль.

```powershell
# Более надёжно, чем runas: запускаем процесс с учётными данными и загружаем профиль.
# ВАЖНО: команда попросит пароль (SecureString). Пароль никуда не сохраняется.
$sec = Read-Host "Пароль для .\bot-admin" -AsSecureString
$cred = New-Object System.Management.Automation.PSCredential(".\bot-admin", $sec)

# Запускаем короткую команду под bot-admin. Ключевой момент — загрузка профиля.
# В результате Windows создаст профиль/папку в C:\Users\bot-admin* (если её не было).
Start-Process -FilePath "cmd.exe" -ArgumentList "/c whoami > C:\Windows\Temp\bot-admin.whoami.txt" -Credential $cred -LoadUserProfile -Wait

# Проверка (файл должен появиться, и профиль тоже):
Get-Content "C:\Windows\Temp\bot-admin.whoami.txt"
```

Примечание про `runas`: если при вводе пароля `runas` пишет «Не удалось получить пароль пользователя», это обычно связано не с неверным паролем, а с особенностями текущей сессии/консоли (например, ввод пароля недоступен). В этом случае используйте `Start-Process -Credential` как выше.

- **Вариант C (полностью автоматизировано, без входа; продвинутый)**: принудительно создать профиль через API `CreateProfile`.

```powershell
# Принудительное создание профиля для bot-admin через userenv.dll
$userName = "bot-admin"
$account = New-Object System.Security.Principal.NTAccount("$env:COMPUTERNAME\$userName")
$sid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value

# ВАЖНО: используем -TypeDefinition (а не -MemberDefinition), чтобы 'using' был валиден.
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class UserEnvNative {
  [DllImport("userenv.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern int CreateProfile(
    string pszUserSid,
    string pszUserName,
    StringBuilder pszProfilePath,
    int cchProfilePath
  );
}
"@

$sb = New-Object System.Text.StringBuilder 260
$hr = [UserEnvNative]::CreateProfile($sid, $userName, $sb, $sb.Capacity)
if ($hr -ne 0) {
  throw ("CreateProfile failed with HRESULT 0x{0:X8}" -f $hr)
}
$sb.ToString()
```

Проверка результата (после любого варианта):
```powershell
Get-ChildItem C:\Users -Directory | Where-Object { $_.Name -like 'bot-admin*' } | Select-Object Name, FullName
```

Шаг 3 — Установка OpenSSH вручную (Win32-OpenSSH)
-------------------------------------------------
Используем официальный релиз Win32-OpenSSH (рекомендуется, если встроенный компонент не ставится).

1. Скачай архив OpenSSH (например OpenSSH-Win64.zip) с официальной страницы релизов: https://github.com/PowerShell/Win32-OpenSSH/releases
2. Распакуй содержимое в `C:\Program Files\OpenSSH`. Если папки нет — создай.

Пояснение: ручная установка даёт контроль и часто решает проблемы с отсутствием компонентов на WSUS.

Шаг 4 — Регистрация служб и генерация host‑ключей
------------------------------------------------
1. В PowerShell перейди в папку:
```powershell
cd "C:\Program Files\OpenSSH"
```
2. Зарегистрируй службы (внимание: пробелы после `binPath=` и `start=` обязательны):
```powershell
sc.exe create sshd binPath= "C:\Program Files\OpenSSH\sshd.exe" start= auto
sc.exe create ssh-agent binPath= "C:\Program Files\OpenSSH\ssh-agent.exe" start= auto
sc.exe description sshd "OpenSSH SSH Server"
sc.exe description ssh-agent "OpenSSH Key Agent"
```
3. Сгенерируй host‑ключи:
```powershell
.\ssh-keygen.exe -A
```
4. Исправь права на host‑ключи (если в комплекте есть скрипт `FixHostFilePermissions.ps1` — запусти его).

Если PowerShell пишет, что «выполнение сценариев отключено» (ExecutionPolicy), **не меняй политику глобально**. Используй безопасный обход **только для текущего окна** (Scope Process):

```powershell
# Разрешить запуск скриптов только в текущей сессии PowerShell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

# Если файл пришёл из интернета и помечен "blocked", сними блокировку (безопасно)
Unblock-File -Path .\FixHostFilePermissions.ps1 -ErrorAction SilentlyContinue

# Запусти скрипт
.\FixHostFilePermissions.ps1
```

Альтернатива (то же самое одной командой, без изменения политики даже в Process):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\FixHostFilePermissions.ps1
```

Пояснение: без host‑ключей `sshd` не запустится; `FixHostFilePermissions.ps1` выставляет корректные ACL для файлов в `C:\ProgramData\ssh` (host keys и конфиги), чтобы `sshd` не отказался стартовать из‑за «слишком широких» прав.

Шаг 5 — Подготовка профиля bot-admin и папки .ssh
-------------------------------------------------
1. Определи реальную папку профиля (иногда Windows добавляет суффиксы):
```powershell
$profile = Get-ChildItem C:\Users -Directory | Where-Object { $_.Name -like 'bot-admin*' } | Select-Object -First 1
$profile.FullName
```
2. Создай папку `.ssh` и файл authorized_keys:
```powershell
$sshPath = Join-Path $profile.FullName '.ssh'
New-Item -ItemType Directory -Path $sshPath -Force
New-Item -ItemType File -Path (Join-Path $sshPath 'authorized_keys') -Force
```

Пояснение: иногда профиль называется `bot-admin.<HOSTNAME>` — используем поиск по маске.

Шаг 6 — Импорт публичного ключа в authorized_keys (без BOM)
-----------------------------------------------------------
Вставлять ключ вручную через Блокнот рискованно (BOM/кодировка). Надёжнее записать ключ через PowerShell в ASCII.

1. Подготовь публичный ключ (одна строка) из клиента (файл `id_ed25519_bot.pub` или аналог).
2. На сервере выполни:
```powershell
$myKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI..._ваш_публичный_ключ_... user@client"
$authFile = Join-Path $sshPath 'authorized_keys'

# Записываем ключ в ASCII (без BOM)
$myKey | Out-File -FilePath $authFile -Encoding ascii
```

Пояснение:
- `Out-File -Encoding ascii` гарантирует отсутствие BOM.  
- Если на этом шаге вы получили `Отказано в доступе`, значит права на файл уже «перекрутили». В этом случае выполните блок восстановления в Шаге 7 и повторите запись ключа.

Шаг 7 — Жёсткая настройка прав (ACL) — критично (делать ПОСЛЕ записи ключа)
---------------------------------------------------------------------------
На этом шаге мы «запираем» доступ: оставляем только `bot-admin` и `SYSTEM`. Это важно для безопасности и для того, чтобы OpenSSH корректно принимал ключи.

Важно про практику:
- В процессе настройки **не делайте себя заложником**: сначала запишите ключ (Шаг 6), затем ужесточайте ACL (Шаг 7).
- Чтобы инструкция работала на любой Windows (любой язык ОС), используем **SID**:
  - Administrators: `*S-1-5-32-544`
  - SYSTEM: `*S-1-5-18`

7.1. Установить строгие права (с временным доступом Administrators на этапе настройки)

```powershell
$admin = '*S-1-5-32-544' # Builtin\Administrators
$system = '*S-1-5-18'    # NT AUTHORITY\SYSTEM

# Папка .ssh
icacls $sshPath /inheritance:r
icacls $sshPath /grant:r "bot-admin:(OI)(CI)F"
icacls $sshPath /grant:r "${system}:(OI)(CI)F"

# Временно оставляем Administrators, чтобы можно было чинить/проверять (уберём в п. 7.2)
icacls $sshPath /grant:r "${admin}:(OI)(CI)F"

# Файл authorized_keys
$authFile = Join-Path $sshPath 'authorized_keys'
icacls $authFile /inheritance:r
icacls $authFile /grant:r "bot-admin:F"
icacls $authFile /grant:r "${system}:F"
icacls $authFile /grant:r "${admin}:F"
```

Пояснение:
- `/inheritance:r` — удаляет наследование (очищает лишние записи).  
- `(OI)(CI)F` — полные права для папки и вложенных объектов.  
- В PowerShell переменные в строках вида `"${system}:F"` обязательно писать через `${...}`, иначе будет ошибка парсера.

7.2. (Опционально) вернуть максимальную «стерильность» — убрать Administrators из ACL

Делайте это **только после** успешной проверки входа по ключу (Шаг 10). Если уберёте раньше — можно снова словить проблемы с обслуживанием файла ключей.

```powershell
$admin = '*S-1-5-32-544'
$authFile = Join-Path $sshPath 'authorized_keys'
icacls $authFile /remove $admin
icacls $sshPath  /remove $admin
```

Если вы уже получили `Отказано в доступе` на `authorized_keys`
--------------------------------------------------------------
Это значит, что ACL уже выставили так, что текущая учётка не может даже изменить права файла.

Восстановление доступа (PowerShell от Админа), затем повторите п. 7.1 и снова выполните запись ключа (Шаг 6):

```powershell
$admin = '*S-1-5-32-544'
$sshPath = "C:\Users\bot-admin\.ssh"           # при необходимости поправьте путь
$authFile = Join-Path $sshPath 'authorized_keys'

takeown /F $sshPath /A /R /D Y
icacls  $sshPath /grant "${admin}:(OI)(CI)F" /T

takeown /F $authFile /A
icacls  $authFile /grant "${admin}:F"
```

Шаг 8 — Редактирование sshd_config (C:\ProgramData\ssh\sshd_config)
-------------------------------------------------------------------
Открой файл в блокноте от имени администратора и замени/вставь следующий минимально необходимый набор:

```
Port 22
PubkeyAuthentication yes
PasswordAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
AuthorizedKeysFile .ssh/authorized_keys
AllowUsers bot-admin

# ВНИМАНИЕ: ОБЯЗАТЕЛЬНО закомментируй блок для группы administrators, если он есть:
# Match Group administrators
#       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

Пояснение:
- Закомментирование блока `Match Group administrators` критично: если этот блок активен, SSH будет искать ключи в системной папке, игнорируя пользовательские `authorized_keys`.

Шаг 9 — Запуск служб и настройка Firewall
------------------------------------------
1. Запуск и автозапуск:
```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
Start-Service ssh-agent
Set-Service -Name ssh-agent -StartupType Automatic
```
2. Открыть порт 22:
```powershell
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
```

Пояснение: если `Start-Service` выдаёт ошибку "service not found", проверь `sc.exe create` и отсутствие состояния "marked for deletion".

Шаг 10 — Тестирование подключения и отладка
-------------------------------------------
1. На клиенте (где приватный ключ `id_ed25519_bot`), выполни:
```bash
# ВАЖНО: на Windows OpenSSH по умолчанию запускает cmd.exe, где разделитель команд — & или && (а не ';')
ssh -i /path/to/id_ed25519_bot bot-admin@SERVER_IP "echo ok && whoami"

# Альтернатива: если хотите использовать ';' как разделитель, явно вызывайте PowerShell
ssh -i /path/to/id_ed25519_bot bot-admin@SERVER_IP "powershell -Command \"Write-Host ok; whoami\""
```
Если всё OK — попробуй команду перезагрузки:
```bash
ssh -i /path/to/id_ed25519_bot bot-admin@SERVER_IP "shutdown /r /t 0"
```
2. Если получаешь `Permission denied (publickey)`:
- Укажи `-vvv` для подробного вывода: `ssh -vvv -i /path/to/key bot-admin@SERVER_IP` — смотри, какие ключи пытает клиент и почему сервер их отвергает.  
- Проверь права: `icacls "C:\Users\bot-admin.DC-MSTSC\.ssh\authorized_keys"` на сервере.  
- Проверь, что ключ в `authorized_keys` — ASCII, одна строка, соответствует приватному ключу на клиенте.

3. Логи OpenSSH в Windows:
 - Event Viewer → Applications and Services Logs → OpenSSH → Operational  
 или из PowerShell:
```powershell
wevtutil qe Microsoft-Windows-OpenSSH/Operational /f:text /c:20
```
Ищи записи об отказах аутентификации — там будет точная причина.

Частые ошибки и решения
-----------------------
- Ошибка 1072/1073 при создании служб: закрыть GUI, перезагрузить, затем повторить `sc.exe create`.  
- `Permission denied (publickey)`: неверный приватный ключ (`-i`), неправильная кодировка `authorized_keys`, ACL не те, или `sshd_config` указывает другой путь к authorized_keys.  
- `sshd` упал сразу после старта: возможно отсутствуют host-keys (ssh-keygen -A) или неправильные права на `C:\ProgramData\ssh`.

Чек‑лист (быстрая сверка перед вводом в эксплуатацию)
-----------------------------------------------------
- Старые службы удалены или полностью переинициализированы.  
- `C:\Program Files\OpenSSH` присутствует с корректными файлами.  
- `C:\ProgramData\ssh` содержит host keys.  
- Профиль `bot-admin` найден (возможно с постфиксом).  
- Папка `.ssh` и `authorized_keys` созданы.  
- ACL выставлены через icacls: только `bot-admin` и `SYSTEM`.  
- `sshd_config`: `PubkeyAuthentication yes`, `PasswordAuthentication no`, `AuthorizedKeysFile .ssh/authorized_keys`, `AllowUsers bot-admin`, блок admin закомментирован.  
- Служба `sshd` в статусе Running.  
- Firewall пропускает порт 22.  
- С клиента подключение по `ssh -i /path/to/key` проходит и команда `shutdown /r /t 0` срабатывает.

Полезные команды диагностики
-----------------------------
- Статус службы:
```powershell
Get-Service sshd
```
- Проверка прослушиваемых портов:
```powershell
netstat -ano | Select-String ":22"
```
- Список ACL:
```powershell
icacls "C:\Users\bot-admin.DC-MSTSC\.ssh\authorized_keys"
```
- Просмотр логов OpenSSH:
```powershell
wevtutil qe Microsoft-Windows-OpenSSH/Operational /f:text /c:50
```
- Подробное подключение с клиента:
```bash
ssh -vvv -i /path/to/key bot-admin@SERVER_IP
```

Заключение
----------
Эта инструкция покрывает все выявленные в процессе тонкости: «призраки» служб, отсутствие install скриптов, нестандартные имена профилей, проблемы с ACL и кодировкой ключей. Следуя шагам последовательно, вы получите «стерильную» установку OpenSSH и безопасный доступ для `bot-admin`, ограниченного правами только на выключение/перезагрузку.

Если хочешь, я могу:
- Сгенерировать тестовый PowerShell скрипт (dry-run + prompt) для автоматизации этого процесса.  
- Подготовить шаблон `sshd_config` с расширенными опциями логирования и ограничений (chroot, ForceCommand и т.д.).  
- Помочь отладить конкретный случай по логам и выводу `ssh -vvv`.

